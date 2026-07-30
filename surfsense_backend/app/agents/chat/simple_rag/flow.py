"""Retrieve-then-answer: one hybrid search, one tool-free model call.

The multi-agent path leaves retrieval to the model's own judgement. To ground
an answer the model must first choose ``task(knowledge_base, ...)``, and that
specialist must then choose ``search_knowledge_base`` from among its own
filesystem tools — two delegation hops, both discretionary, behind a system
prompt whose routing section alone runs to several kilobytes. Models in the
1-2B class clear neither hop: they answer straight from parametric memory,
which reads as a fluent, confident, ungrounded answer.

This flow removes the judgement call. The server retrieves first, renders the
passages into the prompt, and asks the model exactly one question with no
tools bound. What is left is a single forward pass over supplied context,
which is a job small instruct models are actually good at.

Deliberate trade-offs versus the agent path:
  * One retrieval per turn — no iteration, no second pass in another language.
    The question is not used verbatim, though: :mod:`.search_query` widens it
    into keyword terms first, which is the cheap deterministic stand-in for the
    query rewrite a capable model does before calling the search tool.
  * No ``web_search``, ``scrape_webpage``, ``update_memory``, connectors, or
    document writes. This path is read-only Q&A.
  * No conversation history in the retrieval query, so a bare follow-up
    ("what about the second one?") retrieves on that literal string.

The citation spine is shared with the agent path: ``search_knowledge_base_context``
registers every shown passage in the ``CitationRegistry``, and publishing that
registry on the ``StreamResult`` is what lets ``finalize_assistant_message``
rewrite ``[n]`` into ``[citation:<payload>]`` at persist time.
"""

from __future__ import annotations

import time
from collections.abc import AsyncGenerator
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.agents.chat.multi_agent_chat.shared.citations import CitationRegistry
from app.agents.chat.multi_agent_chat.shared.retrieval import (
    SearchScope,
    search_knowledge_base_context,
)
from app.agents.chat.runtime.references import referenced_document_ids
from app.db import shielded_async_session
from app.tasks.chat.streaming.helpers.chunk_parts import extract_chunk_parts
from app.utils.perf import get_perf_logger

from .prompt import NO_RESULTS_MESSAGE, SIMPLE_RAG_SYSTEM_PROMPT
from .search_query import build_search_terms

_perf_log = get_perf_logger()

# Higher than the agent path's per-call default because this flow gets one
# retrieval per turn instead of one per tool call: breadth has to come from a
# single search rather than from the union of several. Note this also scales the
# candidate pool (``_CANDIDATE_MULTIPLIER``), and therefore how much context the
# prompt can grow to — the ceiling is roughly ``top_k * 5`` chunks.
_DEFAULT_TOP_K = 16


async def _retrieve(
    *,
    search_space_id: int,
    question: str,
    registry: CitationRegistry,
    mentioned_document_ids: list[int] | None,
    mentioned_folder_ids: list[int] | None,
    top_k: int,
    keyword_terms: list[str],
) -> str | None:
    """Run the retrieval spine on its own short-lived session.

    Mirrors the ``search_knowledge_base`` tool: an isolated session keeps the
    search from re-opening (and holding) a transaction on the orchestrator's
    session for the duration of the stream.
    """
    from app.services.reranker_service import RerankerService

    async with shielded_async_session() as session:
        document_ids = await referenced_document_ids(
            session,
            search_space_id=search_space_id,
            document_ids=mentioned_document_ids,
            folder_ids=mentioned_folder_ids,
        )
        return await search_knowledge_base_context(
            session,
            search_space_id=search_space_id,
            query=question,
            registry=registry,
            scope=SearchScope(document_ids=document_ids or None),
            # No-ops when nothing is configured (``rerank_hits`` short-circuits
            # on None), but with a single non-iterative retrieval, rank quality
            # is doing all the work — so use one when it is available.
            reranker=RerankerService.get_reranker_instance(),
            top_k=top_k,
            # Widens only the keyword leg. ``query`` stays the question, so the
            # semantic leg and the reranker still score against what was asked.
            keyword_terms=keyword_terms or None,
        )


async def stream_simple_rag(
    *,
    llm: Any,
    search_space_id: int,
    question: str,
    streaming_service: Any,
    stream_result: Any,
    content_builder: Any | None = None,
    mentioned_document_ids: list[int] | None = None,
    mentioned_folder_ids: list[int] | None = None,
    initial_step_id: str | None = None,
    initial_step_title: str = "",
    top_k: int = _DEFAULT_TOP_K,
    no_results_message: str = NO_RESULTS_MESSAGE,
) -> AsyncGenerator[str, None]:
    """Retrieve, then stream one grounded answer. Yields SSE frames.

    Emits the same text-start / text-delta / text-end triple the agent relay
    does, so no client change is needed to render it. Populates
    ``stream_result.accumulated_text`` and ``stream_result.citation_registry``
    for the shared finalize path.
    """
    registry = CitationRegistry()

    keyword_terms = build_search_terms(question)

    _t0 = time.perf_counter()
    context = await _retrieve(
        search_space_id=search_space_id,
        question=question,
        registry=registry,
        mentioned_document_ids=mentioned_document_ids,
        mentioned_folder_ids=mentioned_folder_ids,
        top_k=top_k,
        keyword_terms=keyword_terms,
    )
    _perf_log.info(
        "[simple_rag] Retrieval in %.3fs (hits=%s, terms=%d)",
        time.perf_counter() - _t0,
        "none" if context is None else len(registry.by_n),
        len(keyword_terms),
    )

    # Close the "Understanding your request" placeholder the orchestrator
    # opened; this flow emits no further steps, so leaving it in_progress
    # would spin in the UI for the whole turn.
    if initial_step_id:
        items = [
            "Searched the knowledge base"
            if context is not None
            else "Searched the knowledge base — no matches"
        ]
        if content_builder is not None:
            content_builder.on_thinking_step(
                initial_step_id, initial_step_title, "completed", items
            )
        yield streaming_service.format_thinking_step(
            step_id=initial_step_id,
            title=initial_step_title,
            status="completed",
            items=items,
        )

    if context is None:
        # No passages means nothing to answer from and nothing to leak, so the
        # miss is reported without a model round-trip. Asking the model to
        # announce its own miss is what small models turn back into an
        # ungrounded answer.
        text_id = streaming_service.generate_text_id()
        yield streaming_service.format_text_start(text_id)
        if content_builder is not None:
            content_builder.on_text_start(text_id)
        for sse in streaming_service.stream_text(text_id, no_results_message):
            yield sse
        if content_builder is not None:
            content_builder.on_text_delta(text_id, no_results_message)
        stream_result.accumulated_text += no_results_message
        yield streaming_service.format_text_end(text_id)
        if content_builder is not None:
            content_builder.on_text_end(text_id)
        stream_result.citation_registry = registry
        return

    messages = [
        SystemMessage(content=SIMPLE_RAG_SYSTEM_PROMPT),
        HumanMessage(content=f"{context}\n\n<question>\n{question}\n</question>"),
    ]

    # Both opened lazily: a thinking model streams its trace first, and an empty
    # text part sitting in front of it would render as a stray blank block.
    text_id: str | None = None
    reasoning_id: str | None = None
    _t_stream = time.perf_counter()

    # No ``.bind_tools()``, no graph, no checkpointer: one pass over the
    # supplied context. Token usage is still captured — the LiteLLM success
    # callback reads the turn accumulator off its ContextVar, which the
    # orchestrator set before this call.
    async for chunk in llm.astream(messages):
        parts = extract_chunk_parts(chunk)

        # A thinking model (qwen3, deepseek-reasoner) streams its trace here.
        # Route it to reasoning frames so it never lands in the answer text.
        if parts["reasoning"]:
            if reasoning_id is None:
                reasoning_id = streaming_service.generate_reasoning_id()
                yield streaming_service.format_reasoning_start(reasoning_id)
                if content_builder is not None:
                    content_builder.on_reasoning_start(reasoning_id)
            yield streaming_service.format_reasoning_delta(
                reasoning_id, parts["reasoning"]
            )
            if content_builder is not None:
                content_builder.on_reasoning_delta(reasoning_id, parts["reasoning"])

        delta = parts["text"]
        if delta:
            if reasoning_id is not None:
                yield streaming_service.format_reasoning_end(reasoning_id)
                if content_builder is not None:
                    content_builder.on_reasoning_end(reasoning_id)
                reasoning_id = None
            if text_id is None:
                text_id = streaming_service.generate_text_id()
                yield streaming_service.format_text_start(text_id)
                if content_builder is not None:
                    content_builder.on_text_start(text_id)
            stream_result.accumulated_text += delta
            yield streaming_service.format_text_delta(text_id, delta)
            if content_builder is not None:
                content_builder.on_text_delta(text_id, delta)

    if reasoning_id is not None:
        yield streaming_service.format_reasoning_end(reasoning_id)
        if content_builder is not None:
            content_builder.on_reasoning_end(reasoning_id)

    if text_id is not None:
        yield streaming_service.format_text_end(text_id)
        if content_builder is not None:
            content_builder.on_text_end(text_id)

    _perf_log.info(
        "[simple_rag] Answer streamed in %.3fs (%d chars)",
        time.perf_counter() - _t_stream,
        len(stream_result.accumulated_text),
    )

    # Read at finalize to rewrite [n] -> [citation:<payload>].
    stream_result.citation_registry = registry


__all__ = ["stream_simple_rag"]
