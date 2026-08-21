"""Search the knowledge base and render it as model-facing ``<retrieved_context>``.

The retrieval spine end to end: hybrid search → rerank → adapt → render, with
each shown passage registered for ``[n]`` citation along the way.

Split in two so surfaces that render differently still retrieve identically:
:func:`search_knowledge_base_hits` is the retrieval half (search + rerank), and
:func:`search_knowledge_base_context` adds the citable rendering on top. Reports
call the former because they ship without ``[n]`` labels — that is a rendering
decision, and it should not silently buy them a different set of passages.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.chat.multi_agent_chat.shared.citations import CitationRegistry
from app.agents.chat.multi_agent_chat.shared.document_render import (
    render_search_context,
)

from .adapter import to_renderable_document
from .hybrid_search import search_chunks
from .models import DocumentHit, SearchScope
from .reranking import rerank_hits

if TYPE_CHECKING:
    from app.services.reranker_service import RerankerService

# The default for server-driven retrieval, where breadth has to come from a
# single search rather than from the union of several model-chosen tool calls.
# The ``search_knowledge_base`` tool keeps its own, lower, model-facing default.
DEFAULT_TOP_K = 16

# How many documents to rank before cutting to ``top_k``. The fused chunk pool
# spans far more documents than it returns — measured at 47-57 for a 16-document
# search — so ranking only the 16 that RRF happened to order first wasted the
# cross-encoder on a third of what the search had already found. Ranking 3x and
# cutting afterwards changed 7-10 of the final 16 documents on every query
# measured, for about +1.9s on an A100.
_RERANK_FETCH_MULTIPLIER = 3


async def search_knowledge_base_hits(
    db_session: AsyncSession,
    *,
    search_space_id: int,
    query: str,
    scope: SearchScope | None = None,
    reranker: RerankerService | None = None,
    top_k: int = DEFAULT_TOP_K,
    keyword_terms: Sequence[str] | None = None,
) -> list[DocumentHit]:
    """Retrieve and rank KB evidence for ``query`` — everything before rendering.

    ``keyword_terms`` widens only the keyword leg (see
    :func:`~.hybrid_search._keyword_tsquery`); ``query`` still drives the
    semantic leg and the reranker, so expansion terms cannot dilute either.

    Order matters here: rank a wider set, *then* cut to ``top_k``. Cutting first
    leaves the reranker able only to reorder what RRF already chose, never to
    promote the document RRF ranked 17th — which is the one case a reranker is
    bought for. Without a reranker there is nothing to reorder, so the widened
    fetch is skipped and the result is exactly what it always was.
    """
    ranked_pool = (
        top_k * _RERANK_FETCH_MULTIPLIER if reranker is not None else top_k
    )
    hits = await search_chunks(
        db_session,
        search_space_id=search_space_id,
        query=query,
        scope=scope or SearchScope(),
        top_k=top_k,
        fetch_k=ranked_pool,
        keyword_terms=keyword_terms,
    )
    return rerank_hits(query, hits, reranker)[:top_k]


async def search_knowledge_base_context(
    db_session: AsyncSession,
    *,
    search_space_id: int,
    query: str,
    registry: CitationRegistry,
    scope: SearchScope | None = None,
    reranker: RerankerService | None = None,
    top_k: int = DEFAULT_TOP_K,
    keyword_terms: Sequence[str] | None = None,
) -> str | None:
    """Retrieve KB evidence for ``query`` and render it, registering each ``[n]``.

    Returns ``None`` when nothing matched, so the caller can skip the block.
    """
    ranked = await search_knowledge_base_hits(
        db_session,
        search_space_id=search_space_id,
        query=query,
        scope=scope,
        reranker=reranker,
        top_k=top_k,
        keyword_terms=keyword_terms,
    )
    # Already reranked above; passing the reranker again would rank it twice.
    return build_context(query, ranked, registry)


def build_context(
    query: str,
    hits: list[DocumentHit],
    registry: CitationRegistry,
    *,
    reranker: RerankerService | None = None,
) -> str | None:
    """Rerank → adapt → render. Pure given ``hits``, so it is unit-testable."""
    ranked = rerank_hits(query, hits, reranker)
    documents = [to_renderable_document(hit) for hit in ranked]
    return render_search_context(documents, registry)


__all__ = [
    "DEFAULT_TOP_K",
    "build_context",
    "search_knowledge_base_context",
    "search_knowledge_base_hits",
]
