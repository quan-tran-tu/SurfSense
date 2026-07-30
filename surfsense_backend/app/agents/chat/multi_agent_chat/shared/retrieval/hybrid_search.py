"""Hybrid (semantic + keyword) chunk search with reciprocal-rank fusion.

Only matched chunks are citable, so the fused result already holds every passage
shown — there is no second per-document fetch. Returns the top ``top_k``
documents, each carrying its matched chunks in reading order.

The keyword leg has two modes. By default it runs ``plainto_tsquery`` over the
whole query, which ANDs every surviving lexeme. Callers that can decompose their
query into independent terms pass ``keyword_terms`` instead and get an OR fold —
see :func:`_keyword_tsquery`.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Sequence

from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.agents.chat.runtime.path_resolver import current_thread_id
from app.config import config
from app.db import Chunk, Document, DocumentType, Folder
from app.observability import metrics, otel
from app.services.folder_service import folder_subtree_ids_subquery
from app.services.folder_sharing_service import linked_folder_ids_subquery
from app.utils.perf import get_perf_logger

from .models import ChunkHit, DocumentHit, SearchScope

_RRF_K = 60
_CANDIDATE_MULTIPLIER = 5  # fused-chunk pool size relative to top_k
_MAX_PASSAGES_PER_DOC = 12
_SURFACE = "chunks"


async def search_chunks(
    db_session: AsyncSession,
    *,
    search_space_id: int,
    query: str,
    scope: SearchScope,
    top_k: int,
    query_embedding: list[float] | None = None,
    keyword_terms: Sequence[str] | None = None,
) -> list[DocumentHit]:
    """Top ``top_k`` documents for ``query`` within scope, each with its chunks.

    ``query`` drives the semantic leg (and is what a reranker should score
    against). ``keyword_terms``, when given, replaces ``query`` in the keyword
    leg with an OR over the supplied terms.

    Instrumented seam: traces the search, records its duration, and logs a
    timing line. The fusion logic lives in :func:`_search`.
    """
    started = time.perf_counter()
    with otel.kb_search_span(
        search_space_id=search_space_id,
        query_chars=len(query),
        extra={"search.surface": _SURFACE, "search.mode": "hybrid"},
    ) as span:
        try:
            documents = await _search(
                db_session,
                search_space_id=search_space_id,
                query=query,
                scope=scope,
                top_k=top_k,
                query_embedding=query_embedding,
                keyword_terms=keyword_terms,
            )
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000
            metrics.record_kb_search_duration(
                elapsed_ms, search_space_id=search_space_id, surface=_SURFACE
            )
        span.set_attribute("result.count", len(documents))
        get_perf_logger().info(
            "[chunk_search] hybrid in %.3fs docs=%d space=%d",
            elapsed_ms / 1000,
            len(documents),
            search_space_id,
        )
        return documents


async def _search(
    db_session: AsyncSession,
    *,
    search_space_id: int,
    query: str,
    scope: SearchScope,
    top_k: int,
    query_embedding: list[float] | None,
    keyword_terms: Sequence[str] | None = None,
) -> list[DocumentHit]:
    """Fusion search itself: resolve scope, fuse the two legs, group by document."""
    document_types = _resolve_document_types(scope.document_types)
    if document_types == []:  # types requested, none recognized → nothing matches
        return []

    if query_embedding is None:
        query_embedding = await asyncio.to_thread(
            config.embedding_model_instance.embed, query
        )

    conditions = _base_conditions(
        search_space_id, scope, document_types, thread_id=current_thread_id()
    )
    rows = await _fused_chunks(
        db_session,
        query=query,
        query_embedding=query_embedding,
        conditions=conditions,
        candidate_pool=top_k * _CANDIDATE_MULTIPLIER,
        keyword_terms=keyword_terms,
    )
    return _group_into_documents(rows, top_k=top_k)


def _resolve_document_types(
    raw: tuple[str, ...] | None,
) -> list[DocumentType] | None:
    """Map type names to enum members; ``None`` when unfiltered, ``[]`` if all unknown."""
    if not raw:
        return None
    resolved: list[DocumentType] = []
    for name in raw:
        with contextlib.suppress(KeyError):
            resolved.append(DocumentType[name])
    return resolved


def _base_conditions(
    search_space_id: int,
    scope: SearchScope,
    document_types: list[DocumentType] | None,
    thread_id: int | None = None,
) -> list:
    """Filters shared by both search legs."""
    conditions = [
        # Readable = owned by this space, or inside a folder this space holds a
        # live link to. Linked documents keep the *sharer's* search_space_id —
        # nothing is copied — so the plain equality alone would never match them.
        or_(
            Document.search_space_id == search_space_id,
            Document.folder_id.in_(linked_folder_ids_subquery(search_space_id)),
        ),
        func.coalesce(Document.status["state"].astext, "ready") != "deleting",
    ]
    if thread_id is not None:
        # Session scoping: documents inside a folder owned by a *different* chat
        # session are invisible here. Folderless documents keep their space-wide
        # visibility (NOT IN would silently drop them on NULL). The hidden set
        # only ever names this space's folders, so linked (foreign) documents
        # are unaffected.
        hidden_folders = select(Folder.id).where(
            Folder.search_space_id == search_space_id,
            Folder.owner_thread_id.is_not(None),
            Folder.owner_thread_id != thread_id,
        )
        conditions.append(
            or_(
                Document.folder_id.is_(None),
                Document.folder_id.notin_(hidden_folders),
            )
        )
    if document_types:
        conditions.append(Document.document_type.in_(document_types))
    # "These documents" and "inside these folders" are one question asked two
    # ways, so they OR: pinning a folder and a loose document must not yield the
    # empty intersection. Folder matching covers each root's subtree — an upload
    # mirrors its directory tree, so an exact match would see only the files
    # sitting loose at the root.
    pins = []
    if scope.document_ids:
        pins.append(Document.id.in_(scope.document_ids))
    if scope.folder_ids:
        pins.append(
            Document.folder_id.in_(folder_subtree_ids_subquery(scope.folder_ids))
        )
    if pins:
        conditions.append(or_(*pins) if len(pins) > 1 else pins[0])
    if scope.start_date is not None:
        conditions.append(Document.updated_at >= scope.start_date)
    if scope.end_date is not None:
        conditions.append(Document.updated_at <= scope.end_date)
    return conditions


def _keyword_tsquery(query: str, keyword_terms: Sequence[str] | None):
    """The keyword leg's tsquery: AND over one query, OR over an explicit term list.

    ``plainto_tsquery`` inserts ``&`` between every surviving lexeme, so passing
    a whole question matches only a chunk containing *all* of its words. Past a
    handful of words that is no chunk at all, and because the keyword leg is
    gated on ``@@`` the whole leg then silently contributes nothing to the
    fusion — the search quietly degrades to semantic-only.

    A caller that has decomposed its query into independent terms passes them
    here instead and gets an OR fold: each term is its own way in, so adding
    terms can only widen the match. Each term still goes through
    ``plainto_tsquery``, which keeps the query-side and document-side parsers
    identical (so a term like ``06/07/2026`` matches however Postgres chooses to
    tokenize it) and leaves no room for tsquery syntax errors or injection.
    """
    if not keyword_terms:
        return func.plainto_tsquery("english", query)
    tsquery = func.plainto_tsquery("english", keyword_terms[0])
    for term in keyword_terms[1:]:
        tsquery = tsquery.op("||")(func.plainto_tsquery("english", term))
    return tsquery.self_group()


async def _fused_chunks(
    db_session: AsyncSession,
    *,
    query: str,
    query_embedding: list[float],
    conditions: list,
    candidate_pool: int,
    keyword_terms: Sequence[str] | None = None,
):
    """Run semantic + keyword legs and fuse them with RRF; return (Chunk, score) rows."""
    tsvector = func.to_tsvector("english", Chunk.content)
    tsquery = _keyword_tsquery(query, keyword_terms)

    semantic = (
        select(
            Chunk.id,
            func.rank()
            .over(order_by=Chunk.embedding.op("<=>")(query_embedding))
            .label("rank"),
        )
        .join(Document, Chunk.document_id == Document.id)
        .where(*conditions)
        .order_by(Chunk.embedding.op("<=>")(query_embedding))
        .limit(candidate_pool)
        .cte("semantic_search")
    )

    keyword = (
        select(
            Chunk.id,
            func.rank()
            .over(order_by=func.ts_rank_cd(tsvector, tsquery).desc())
            .label("rank"),
        )
        .join(Document, Chunk.document_id == Document.id)
        .where(*conditions)
        .where(tsvector.op("@@")(tsquery))
        .order_by(func.ts_rank_cd(tsvector, tsquery).desc())
        .limit(candidate_pool)
        .cte("keyword_search")
    )

    fused = (
        select(
            Chunk,
            (
                func.coalesce(1.0 / (_RRF_K + semantic.c.rank), 0.0)
                + func.coalesce(1.0 / (_RRF_K + keyword.c.rank), 0.0)
            ).label("score"),
        )
        .select_from(
            semantic.outerjoin(keyword, semantic.c.id == keyword.c.id, full=True)
        )
        .join(Chunk, Chunk.id == func.coalesce(semantic.c.id, keyword.c.id))
        .options(joinedload(Chunk.document))
        .order_by(text("score DESC"))
        .limit(candidate_pool)
    )

    result = await db_session.execute(fused)
    return result.all()


def _group_into_documents(rows, *, top_k: int) -> list[DocumentHit]:
    """Group fused chunks by document, keep the top_k best, order chunks for reading."""
    chunks_by_doc: dict[int, list[ChunkHit]] = {}
    document_by_id: dict[int, Document] = {}
    best_score: dict[int, float] = {}
    order: list[int] = []

    for chunk, score in rows:
        document_id = chunk.document.id
        if document_id not in chunks_by_doc:
            chunks_by_doc[document_id] = []
            document_by_id[document_id] = chunk.document
            best_score[document_id] = float(score)
            order.append(document_id)
        chunks_by_doc[document_id].append(
            ChunkHit(
                chunk_id=chunk.id,
                content=chunk.content,
                position=chunk.position,
                score=float(score),
            )
        )

    return [
        DocumentHit(
            document_id=document_id,
            title=document_by_id[document_id].title,
            document_type=_type_value(document_by_id[document_id]),
            metadata=document_by_id[document_id].document_metadata or {},
            score=best_score[document_id],
            chunks=_reading_order(chunks_by_doc[document_id]),
        )
        for document_id in order[:top_k]
    ]


def _reading_order(chunks: list[ChunkHit]) -> list[ChunkHit]:
    """Keep the most relevant chunks, then present them in document order."""
    most_relevant = sorted(chunks, key=lambda c: c.score, reverse=True)[
        :_MAX_PASSAGES_PER_DOC
    ]
    return sorted(most_relevant, key=lambda c: c.position)


def _type_value(document: Document) -> str | None:
    document_type = getattr(document, "document_type", None)
    return document_type.value if document_type is not None else None


__all__ = ["search_chunks"]
