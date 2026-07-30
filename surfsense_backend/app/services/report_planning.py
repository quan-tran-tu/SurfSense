"""Derive a report's title and knowledge-base queries from a raw request.

This is the one genuinely model-shaped input to ``generate_report_document``.
Everything else a ``/report`` command needs — the source strategy, the style,
the parent report id, the template instructions — the caller already knows, so
the agent was only ever being asked to guess them back.

Deliberately a single-turn generation with a line-based output contract rather
than tool-calling or JSON: producing a title and a few search phrases is the
kind of task a small instruct model handles reliably, which is the whole point
of not routing reports through the agent. Every failure mode degrades to the
raw request rather than raising.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from langchain_core.messages import HumanMessage

logger = logging.getLogger(__name__)

_MAX_TOPIC_WORDS = 12
_MAX_TOPIC_CHARS = 120

_PLAN_PROMPT = """Người dùng yêu cầu viết một báo cáo. Hãy đọc yêu cầu và trả về:

- Dòng 1: tiêu đề ngắn cho báo cáo (tối đa 8 từ), không có dấu chấm cuối câu.
- Các dòng tiếp theo: {max_queries} truy vấn tìm kiếm trong kho tri thức, mỗi truy vấn một dòng.

Truy vấn phải cụ thể: chứa tên riêng, thuật ngữ, tổ chức, mốc thời gian mà báo
cáo cần tới — không lặp lại nguyên văn tiêu đề. Viết tiêu đề và truy vấn bằng
ĐÚNG ngôn ngữ của yêu cầu bên dưới (tài liệu trong kho chủ yếu là tiếng Việt).

Chỉ xuất các dòng đó. Không đánh số, không gạch đầu dòng, không giải thích.

Yêu cầu:
{request}
"""

# Leading list markers a model adds despite being told not to: "1.", "1)", "-",
# "*", "•". Stripped rather than rejected — the line's content is still good.
_LIST_MARKER = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s*")
_FENCE = re.compile(r"^```[a-z]*\s*$|^```$", re.IGNORECASE)


def _clean_line(line: str) -> str:
    cleaned = _LIST_MARKER.sub("", line).strip()
    # Models sometimes label the lines despite the instruction. Unaccented
    # spellings are included because smaller models drop diacritics.
    cleaned = re.sub(
        r"^(?:tiêu đề|tieu de|title|truy vấn|truy van|query)\s*\d*\s*[:：-]\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    return cleaned.strip(" \t\"'")


def _fallback_topic(request: str) -> str:
    words = request.split()
    topic = " ".join(words[:_MAX_TOPIC_WORDS])
    return topic[:_MAX_TOPIC_CHARS].strip() or "Báo cáo"


def parse_report_plan(raw: str, request: str, *, max_queries: int) -> tuple[str, list[str]]:
    """Split the model's line-based answer into ``(topic, queries)``.

    Pure, so the parsing contract is testable without a model. Falls back to the
    request itself for either half that comes back empty.
    """
    lines = [
        cleaned
        for line in (raw or "").splitlines()
        if not _FENCE.match(line.strip())
        and (cleaned := _clean_line(line))
    ]

    topic = lines[0] if lines else ""
    if len(topic) > _MAX_TOPIC_CHARS:
        topic = topic[:_MAX_TOPIC_CHARS].rstrip()
    if not topic:
        topic = _fallback_topic(request)

    queries: list[str] = []
    for candidate in lines[1:]:
        if candidate and candidate not in queries:
            queries.append(candidate)
        if len(queries) >= max_queries:
            break

    # A model that returned only a title still gets a usable search.
    if not queries:
        queries = [request.strip()] if request.strip() else []

    return topic, queries


async def plan_report_request(
    llm: Any,
    request: str,
    *,
    max_queries: int = 4,
) -> tuple[str, list[str]]:
    """Return ``(topic, search_queries)`` for a raw ``/report`` request.

    Never raises: a model error, an empty answer or unparseable output all fall
    back to the request text, which still produces a valid (if blunter) search.
    """
    request = (request or "").strip()
    if not request:
        return "Báo cáo", []

    if llm is None:
        return _fallback_topic(request), [request]

    prompt = _PLAN_PROMPT.format(max_queries=max_queries, request=request)
    try:
        response = await llm.ainvoke([HumanMessage(content=prompt)])
        raw = response.content if isinstance(response.content, str) else ""
    except Exception:
        logger.exception("[plan_report_request] Planning call failed; using raw request")
        return _fallback_topic(request), [request]

    topic, queries = parse_report_plan(raw, request, max_queries=max_queries)
    logger.info(
        "[plan_report_request] topic=%r queries=%d", topic[:60], len(queries)
    )
    return topic, queries


__all__ = ["parse_report_plan", "plan_report_request"]
