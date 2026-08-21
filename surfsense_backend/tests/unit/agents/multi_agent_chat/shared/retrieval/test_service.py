"""Tests for the build_context pipeline (rerank → adapt → render)."""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.chat.multi_agent_chat.shared.citations import CitationRegistry
from app.agents.chat.multi_agent_chat.shared.retrieval import service
from app.agents.chat.multi_agent_chat.shared.retrieval.models import (
    ChunkHit,
    DocumentHit,
)
from app.agents.chat.multi_agent_chat.shared.retrieval.service import (
    build_context,
    search_knowledge_base_hits,
)

pytestmark = pytest.mark.unit


def _hit(document_id: int, chunk_id: int) -> DocumentHit:
    return DocumentHit(
        document_id=document_id,
        title=f"Doc {document_id}",
        document_type="FILE",
        metadata={},
        score=1.0 / document_id,
        chunks=[
            ChunkHit(
                chunk_id=chunk_id, content=f"text {chunk_id}", position=0, score=1.0
            )
        ],
    )


def test_no_hits_renders_nothing() -> None:
    assert build_context("q", [], CitationRegistry()) is None


def test_renders_block_and_registers_labels_in_order() -> None:
    registry = CitationRegistry()

    block = build_context("q", [_hit(1, 880), _hit(2, 12)], registry)

    assert block is not None
    assert "[1] text 880" in block
    assert "[2] text 12" in block
    assert registry.resolve(1).locator == {"document_id": 1, "chunk_id": 880}
    assert registry.resolve(2).locator == {"document_id": 2, "chunk_id": 12}


class _ReverseReranker:
    """Stand-in reranker that simply reverses document order."""

    def rerank_documents(
        self, query_text: str, documents: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        return list(reversed(documents))


def test_reranker_reorders_documents_before_labeling() -> None:
    registry = CitationRegistry()

    block = build_context(
        "q", [_hit(1, 880), _hit(2, 12)], registry, reranker=_ReverseReranker()
    )

    assert block is not None
    # Reversed: doc 2 now renders first and gets [1].
    assert registry.resolve(1).locator == {"document_id": 2, "chunk_id": 12}
    assert registry.resolve(2).locator == {"document_id": 1, "chunk_id": 880}


# --------------------------------------------------------------- rank-then-cut
#
# The bug these guard against was live for months and is easy to reintroduce:
# the document cut happened in SQL, so the reranker only ever reordered what
# reciprocal-rank fusion had already chosen. Measured on the osint corpus, an
# 80-chunk pool spanned 47-57 documents while the reranker saw 16 — so the one
# thing a cross-encoder is bought for, promoting a document that lexical fusion
# ranked poorly, was impossible by construction.
#
# These assert that the promotion is reachable, not on the multiplier, which is
# tuning.


@pytest.fixture
def captured_search(monkeypatch):
    """Replace the DB search with one that records how it was called."""
    captured: dict = {}

    async def _fake_search_chunks(_session, **kwargs):
        captured.update(kwargs)
        wanted = kwargs.get("fetch_k") or kwargs["top_k"]
        return [_hit(i, i) for i in range(1, wanted + 1)]

    monkeypatch.setattr(service, "search_chunks", _fake_search_chunks)
    return captured


async def test_reranker_can_promote_a_document_the_search_ranked_outside_top_k(
    captured_search,
) -> None:
    """The whole point: a document past the cut must be able to reach the answer."""
    hits = await search_knowledge_base_hits(
        None, search_space_id=1, query="q", reranker=_ReverseReranker(), top_k=4
    )

    # Reversed, so the widest-fetched document now ranks first. Before the fix
    # it was never scored at all.
    assert hits[0].document_id > 4


async def test_result_is_still_cut_to_top_k(captured_search) -> None:
    """Callers get what they asked for; the widening is internal."""
    hits = await search_knowledge_base_hits(
        None, search_space_id=1, query="q", reranker=_ReverseReranker(), top_k=4
    )

    assert len(hits) == 4


async def test_widened_fetch_costs_no_extra_database_work(captured_search) -> None:
    """``top_k`` still sizes the chunk pool; only the document cut widens."""
    await search_knowledge_base_hits(
        None, search_space_id=1, query="q", reranker=_ReverseReranker(), top_k=4
    )

    assert captured_search["top_k"] == 4
    assert captured_search["fetch_k"] > 4


async def test_without_a_reranker_nothing_is_over_fetched(captured_search) -> None:
    """Nothing to reorder means nothing to widen for; old behaviour stands."""
    hits = await search_knowledge_base_hits(
        None, search_space_id=1, query="q", reranker=None, top_k=4
    )

    assert captured_search["fetch_k"] == 4
    assert [h.document_id for h in hits] == [1, 2, 3, 4]
