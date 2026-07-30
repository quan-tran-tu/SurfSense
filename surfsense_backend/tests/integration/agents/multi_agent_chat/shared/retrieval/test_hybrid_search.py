"""Behavior tests for the hybrid chunk retriever against a real Postgres.

These exercise ``search_chunks`` through its public surface only: seed real
documents/chunks, run a search, and assert on the returned ``DocumentHit``s —
never on SQL shape or internal ranking math. ``query_embedding`` is supplied
directly (a public parameter) so the semantic leg is deterministic instead of
depending on a live embedding model.
"""

from __future__ import annotations

import uuid

import pytest

from app.agents.chat.multi_agent_chat.shared.retrieval.hybrid_search import (
    search_chunks,
)
from app.agents.chat.multi_agent_chat.shared.retrieval.models import SearchScope
from app.config import config
from app.db import Chunk, Document, DocumentType, Folder, SearchSpace

pytestmark = pytest.mark.integration

_DIM = config.embedding_model_instance.dimension


def _axis(index: int) -> list[float]:
    """A unit vector pointing along one axis — orthogonal axes are dissimilar."""
    vector = [0.0] * _DIM
    vector[index] = 1.0
    return vector


async def _add_document(
    db_session,
    *,
    search_space_id: int,
    title: str = "Doc",
    document_type: DocumentType = DocumentType.FILE,
    state: str = "ready",
    folder_id: int | None = None,
    chunks: list[tuple[str, int, list[float]]],
) -> Document:
    """Persist one document and its chunks; ``chunks`` is (content, position, embedding)."""
    document = Document(
        title=title,
        document_type=document_type,
        content="\n".join(content for content, _, _ in chunks),
        content_hash=uuid.uuid4().hex,
        search_space_id=search_space_id,
        folder_id=folder_id,
        status={"state": state},
    )
    db_session.add(document)
    await db_session.flush()
    for content, position, embedding in chunks:
        db_session.add(
            Chunk(
                content=content,
                document_id=document.id,
                position=position,
                embedding=embedding,
            )
        )
    await db_session.flush()
    return document


async def test_keyword_relevant_document_is_retrieved(db_session, db_search_space):
    document = await _add_document(
        db_session,
        search_space_id=db_search_space.id,
        title="Asyncio Guide",
        chunks=[("The asyncio library enables concurrency.", 0, _axis(0))],
    )

    results = await search_chunks(
        db_session,
        search_space_id=db_search_space.id,
        query="asyncio",
        scope=SearchScope(),
        top_k=5,
        query_embedding=_axis(99),
    )

    assert document.id in {hit.document_id for hit in results}


async def test_semantically_closest_document_ranks_first(db_session, db_search_space):
    aligned = await _add_document(
        db_session,
        search_space_id=db_search_space.id,
        title="Background Work",
        chunks=[("Parallel execution of background work.", 0, _axis(0))],
    )
    await _add_document(
        db_session,
        search_space_id=db_search_space.id,
        title="Dessert",
        chunks=[("Recipes for chocolate cake.", 0, _axis(1))],
    )

    results = await search_chunks(
        db_session,
        search_space_id=db_search_space.id,
        query="asynchronous coroutines",
        scope=SearchScope(),
        top_k=5,
        query_embedding=_axis(0),
    )

    assert results[0].document_id == aligned.id


async def test_results_stay_within_the_search_space(db_session, db_search_space):
    other_space = SearchSpace(name="Other Space", user_id=db_search_space.user_id)
    db_session.add(other_space)
    await db_session.flush()

    mine = await _add_document(
        db_session,
        search_space_id=db_search_space.id,
        chunks=[("Shared keyword asyncio here.", 0, _axis(0))],
    )
    foreign = await _add_document(
        db_session,
        search_space_id=other_space.id,
        chunks=[("Shared keyword asyncio here.", 0, _axis(0))],
    )

    results = await search_chunks(
        db_session,
        search_space_id=db_search_space.id,
        query="asyncio",
        scope=SearchScope(),
        top_k=5,
        query_embedding=_axis(0),
    )

    found = {hit.document_id for hit in results}
    assert mine.id in found and foreign.id not in found


async def test_document_ids_scope_pins_results(db_session, db_search_space):
    pinned = await _add_document(
        db_session,
        search_space_id=db_search_space.id,
        chunks=[("asyncio appears in the pinned doc.", 0, _axis(0))],
    )
    await _add_document(
        db_session,
        search_space_id=db_search_space.id,
        chunks=[("asyncio appears in the other doc too.", 0, _axis(0))],
    )

    results = await search_chunks(
        db_session,
        search_space_id=db_search_space.id,
        query="asyncio",
        scope=SearchScope(document_ids=(pinned.id,)),
        top_k=5,
        query_embedding=_axis(0),
    )

    assert {hit.document_id for hit in results} == {pinned.id}


async def _add_folder(db_session, *, search_space_id: int, name: str, parent_id=None):
    folder = Folder(
        name=name,
        position="a0",
        search_space_id=search_space_id,
        parent_id=parent_id,
    )
    db_session.add(folder)
    await db_session.flush()
    return folder


async def test_folder_ids_scope_reaches_nested_subfolders(db_session, db_search_space):
    """The uploaded-tree case: a folder upload mirrors its subdirectories.

    Scoping to the picked root must find a file several levels down — an exact
    ``folder_id`` match would see only the files sitting loose at the root, which
    for a real document set reads as "nothing found" rather than as a bug.
    """
    root = await _add_folder(db_session, search_space_id=db_search_space.id, name="Root")
    middle = await _add_folder(
        db_session, search_space_id=db_search_space.id, name="Mid", parent_id=root.id
    )
    leaf = await _add_folder(
        db_session, search_space_id=db_search_space.id, name="Leaf", parent_id=middle.id
    )

    deep = await _add_document(
        db_session,
        search_space_id=db_search_space.id,
        folder_id=leaf.id,
        chunks=[("asyncio buried three levels down.", 0, _axis(0))],
    )

    results = await search_chunks(
        db_session,
        search_space_id=db_search_space.id,
        query="asyncio",
        scope=SearchScope(folder_ids=(root.id,)),
        top_k=5,
        query_embedding=_axis(0),
    )

    assert {hit.document_id for hit in results} == {deep.id}


async def test_folder_ids_scope_excludes_unpicked_folders(db_session, db_search_space):
    """Ask about two of three folders: the third one's documents must not answer."""
    picked = [
        await _add_folder(db_session, search_space_id=db_search_space.id, name=name)
        for name in ("First", "Second")
    ]
    unpicked = await _add_folder(
        db_session, search_space_id=db_search_space.id, name="Third"
    )

    wanted = [
        await _add_document(
            db_session,
            search_space_id=db_search_space.id,
            folder_id=folder.id,
            chunks=[(f"asyncio in {folder.name}.", 0, _axis(index))],
        )
        for index, folder in enumerate(picked)
    ]
    excluded = await _add_document(
        db_session,
        search_space_id=db_search_space.id,
        folder_id=unpicked.id,
        chunks=[("asyncio in the folder nobody picked.", 0, _axis(0))],
    )

    results = await search_chunks(
        db_session,
        search_space_id=db_search_space.id,
        query="asyncio",
        scope=SearchScope(folder_ids=tuple(folder.id for folder in picked)),
        top_k=5,
        query_embedding=_axis(0),
    )

    found = {hit.document_id for hit in results}
    assert found == {document.id for document in wanted}
    assert excluded.id not in found


async def test_folder_scope_excludes_folderless_documents(db_session, db_search_space):
    """A scoped question must not be answered from documents in no folder at all."""
    folder = await _add_folder(
        db_session, search_space_id=db_search_space.id, name="Only"
    )
    inside = await _add_document(
        db_session,
        search_space_id=db_search_space.id,
        folder_id=folder.id,
        chunks=[("asyncio inside the folder.", 0, _axis(0))],
    )
    loose = await _add_document(
        db_session,
        search_space_id=db_search_space.id,
        chunks=[("asyncio in a folderless document.", 0, _axis(0))],
    )

    results = await search_chunks(
        db_session,
        search_space_id=db_search_space.id,
        query="asyncio",
        scope=SearchScope(folder_ids=(folder.id,)),
        top_k=5,
        query_embedding=_axis(0),
    )

    found = {hit.document_id for hit in results}
    assert inside.id in found and loose.id not in found


async def test_folder_and_document_pins_union(db_session, db_search_space):
    """Both pins answer the same question, so they widen rather than intersect."""
    folder = await _add_folder(
        db_session, search_space_id=db_search_space.id, name="Folder"
    )
    in_folder = await _add_document(
        db_session,
        search_space_id=db_search_space.id,
        folder_id=folder.id,
        chunks=[("asyncio inside the picked folder.", 0, _axis(0))],
    )
    pinned = await _add_document(
        db_session,
        search_space_id=db_search_space.id,
        chunks=[("asyncio in a separately pinned document.", 0, _axis(1))],
    )
    await _add_document(
        db_session,
        search_space_id=db_search_space.id,
        chunks=[("asyncio in a document nobody pointed at.", 0, _axis(2))],
    )

    results = await search_chunks(
        db_session,
        search_space_id=db_search_space.id,
        query="asyncio",
        scope=SearchScope(folder_ids=(folder.id,), document_ids=(pinned.id,)),
        top_k=5,
        query_embedding=_axis(0),
    )

    assert {hit.document_id for hit in results} == {in_folder.id, pinned.id}


async def test_folder_scope_keeps_search_space_boundary(db_session, db_search_space):
    """A folder id from another space (no link) scopes to nothing, not to its documents."""
    other_space = SearchSpace(name="Other Space", user_id=db_search_space.user_id)
    db_session.add(other_space)
    await db_session.flush()

    foreign_folder = await _add_folder(
        db_session, search_space_id=other_space.id, name="Foreign"
    )
    await _add_document(
        db_session,
        search_space_id=other_space.id,
        folder_id=foreign_folder.id,
        chunks=[("asyncio in another space's folder.", 0, _axis(0))],
    )

    results = await search_chunks(
        db_session,
        search_space_id=db_search_space.id,
        query="asyncio",
        scope=SearchScope(folder_ids=(foreign_folder.id,)),
        top_k=5,
        query_embedding=_axis(0),
    )

    assert results == []


async def test_deleting_documents_are_excluded(db_session, db_search_space):
    ready = await _add_document(
        db_session,
        search_space_id=db_search_space.id,
        chunks=[("asyncio in a ready document.", 0, _axis(0))],
    )
    deleting = await _add_document(
        db_session,
        search_space_id=db_search_space.id,
        state="deleting",
        chunks=[("asyncio in a deleting document.", 0, _axis(0))],
    )

    results = await search_chunks(
        db_session,
        search_space_id=db_search_space.id,
        query="asyncio",
        scope=SearchScope(),
        top_k=5,
        query_embedding=_axis(0),
    )

    found = {hit.document_id for hit in results}
    assert ready.id in found and deleting.id not in found


async def test_matched_chunks_are_ordered_for_reading(db_session, db_search_space):
    # Insert out of order, and give the later-position chunk the stronger
    # semantic score, so reading order differs from both insertion and score.
    document = await _add_document(
        db_session,
        search_space_id=db_search_space.id,
        chunks=[
            ("asyncio paragraph two.", 1, _axis(0)),
            ("asyncio paragraph one.", 0, _axis(50)),
        ],
    )

    results = await search_chunks(
        db_session,
        search_space_id=db_search_space.id,
        query="asyncio",
        scope=SearchScope(),
        top_k=5,
        query_embedding=_axis(0),
    )

    hit = next(hit for hit in results if hit.document_id == document.id)
    assert [chunk.position for chunk in hit.chunks] == [0, 1]


async def test_top_k_caps_the_number_of_documents(db_session, db_search_space):
    for index in range(3):
        await _add_document(
            db_session,
            search_space_id=db_search_space.id,
            title=f"Doc {index}",
            chunks=[(f"asyncio mentioned in doc {index}.", 0, _axis(index))],
        )

    results = await search_chunks(
        db_session,
        search_space_id=db_search_space.id,
        query="asyncio",
        scope=SearchScope(),
        top_k=2,
        query_embedding=_axis(0),
    )

    assert len(results) == 2


_LONG_QUERY = "summarize everything about asyncio and threading and subprocesses"


async def _keyword_leg_fixture(db_session, search_space_id):
    """A keyword-relevant document that is semantically *further* than a decoy.

    Ranking is what isolates the keyword leg: the semantic leg has no relevance
    threshold, so both documents are always retrieved and only their order says
    which leg contributed.
    """
    relevant = await _add_document(
        db_session,
        search_space_id=search_space_id,
        title="Asyncio Guide",
        chunks=[("The asyncio library enables concurrency.", 0, _axis(1))],
    )
    decoy = await _add_document(
        db_session,
        search_space_id=search_space_id,
        title="Dessert",
        chunks=[("Recipes for chocolate cake.", 0, _axis(0))],
    )
    return relevant, decoy


async def test_whole_query_keyword_leg_requires_every_word(db_session, db_search_space):
    """Baseline for the OR path below: ``plainto_tsquery`` ANDs its lexemes.

    A question mentioning anything the document does not contain takes the
    keyword leg to zero — which is why a long natural-language query silently
    degrades this search to semantic-only, letting the decoy win.
    """
    _, decoy = await _keyword_leg_fixture(db_session, db_search_space.id)

    results = await search_chunks(
        db_session,
        search_space_id=db_search_space.id,
        query=_LONG_QUERY,
        scope=SearchScope(),
        top_k=5,
        query_embedding=_axis(0),
    )

    assert results[0].document_id == decoy.id


async def test_keyword_terms_match_any_one_term(db_session, db_search_space):
    """``keyword_terms`` ORs, so one matching term is enough to outrank the decoy."""
    relevant, _ = await _keyword_leg_fixture(db_session, db_search_space.id)

    results = await search_chunks(
        db_session,
        search_space_id=db_search_space.id,
        query=_LONG_QUERY,
        scope=SearchScope(),
        top_k=5,
        query_embedding=_axis(0),
        keyword_terms=["threading", "asyncio", "subprocesses"],
    )

    assert results[0].document_id == relevant.id


async def test_keyword_terms_reach_documents_sharing_no_other_term(
    db_session, db_search_space
):
    """The date-range case: one term per day, each pulling in its own document.

    No daily report shares a term with any other, so nothing but its own date
    can retrieve it — and none of them is semantically close to the query.
    """
    reports = [
        await _add_document(
            db_session,
            search_space_id=db_search_space.id,
            title=f"Report {day:02d}",
            chunks=[(f"Daily report for 0{day}/07/2026.", 0, _axis(day))],
        )
        for day in (6, 7, 8)
    ]
    decoy = await _add_document(
        db_session,
        search_space_id=db_search_space.id,
        title="Dessert",
        chunks=[("Recipes for chocolate cake.", 0, _axis(0))],
    )

    results = await search_chunks(
        db_session,
        search_space_id=db_search_space.id,
        query="tổng hợp thông tin từ 06/07/2026 đến 08/07/2026",
        scope=SearchScope(),
        top_k=5,
        query_embedding=_axis(0),
        keyword_terms=["06/07/2026", "07/07/2026", "08/07/2026"],
    )

    ranked = [hit.document_id for hit in results]
    assert set(ranked[:3]) == {report.id for report in reports}
    assert ranked.index(decoy.id) == 3
