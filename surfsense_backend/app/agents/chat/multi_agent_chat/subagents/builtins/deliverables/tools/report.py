"""Markdown report generation: optional KB sourcing, section-aware revision, short-lived DB sessions.

Two entry points over one pipeline:

* :func:`generate_report_document` — the pipeline itself, a plain async
  function. It searches, writes, parses and persists on its own; nothing about
  it needs an agent. Callable from a route.
* :func:`create_generate_report_tool` — the LangGraph tool wrapper. Adds only
  the things the graph needs: ``runtime.tool_call_id`` for the ``Command``
  return, ``resolve_root_thread_id`` for thread attribution, and
  ``report_progress`` custom events.

The split exists because the agent's entire contribution to a report was
choosing six argument values. A caller that already knows them (a ``/report``
command, say) has no reason to pay for two delegation hops to have a model
guess them back.
"""

import asyncio
import json
import logging
import re
from collections.abc import Callable
from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.callbacks import dispatch_custom_event
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from langgraph.types import Command

from app.agents.chat.multi_agent_chat.shared.receipts.command import with_receipt
from app.agents.chat.multi_agent_chat.shared.receipts.receipt import make_receipt
from app.agents.chat.multi_agent_chat.subagents.builtins.deliverables.tools.thread_resolver import (
    resolve_root_thread_id,
)
from app.db import Report, shielded_async_session
from app.services.connector_service import ConnectorService
from app.services.llm_service import get_agent_llm

logger = logging.getLogger(__name__)


def _report_search_types(
    available_connectors: list[str] | None,
    available_document_types: list[str] | None,
) -> tuple[str, ...] | None:
    """Build the document-type scope for the shared KB search.

    ``None`` means "search every indexed type"; a tuple narrows the scope to the
    connectors/document types the search space actually has.
    """
    types: set[str] = set()
    if available_document_types:
        types.update(available_document_types)
    if available_connectors:
        types.update(available_connectors)
    return tuple(sorted(types)) or None


def _render_kb_hits_for_report(hits: list[Any]) -> str:
    """Render KB hits as plain titled source text for the report writer.

    Citations are intentionally omitted from reports for now, so no ``[n]``
    labels or chunk ids are emitted — just titled document content for grounding.
    """
    from app.agents.chat.multi_agent_chat.shared.document_render import source_label

    blocks: list[str] = []
    for hit in hits:
        label = source_label(hit.document_type, hit.metadata)
        header = f"{hit.title} ({label})" if label else hit.title
        body = "\n\n".join(
            chunk.content.strip() for chunk in hit.chunks if chunk.content.strip()
        )
        if not body:
            continue
        blocks.append(f"## {header}\n\n{body}")
    return "\n\n".join(blocks)


# ─── Shared Formatting Rules ────────────────────────────────────────────────
# Reusable formatting instructions appended to section-level and review prompts.

_FORMATTING_RULES = """\
- QUAN TRỌNG: Xuất Markdown thô trực tiếp. KHÔNG bọc toàn bộ đầu ra trong một khối \
code (ví dụ ```markdown, ````markdown hay bất kỳ hàng rào backtick nào). Các ví dụ \
code và sơ đồ bên trong báo cáo vẫn dùng khối code có rào, nhưng bản thân báo cáo \
KHÔNG được nằm trong một khối như vậy.
- Giữ định dạng Markdown chuẩn xuyên suốt báo cáo.
- Khi đưa ví dụ code, LUÔN trình bày trong khối code có rào với đúng định danh ngôn \
ngữ (ví dụ ```java, ```python). Code trong khối PHẢI xuống dòng và thụt lề đúng — \
KHÔNG BAO GIỜ gộp nhiều câu lệnh trên một dòng. Mỗi câu lệnh, dấu ngoặc và khối \
logic phải nằm trên dòng riêng với thụt lề chính xác.
- Khi vẽ sơ đồ Mermaid, dùng khối ```mermaid. Mỗi câu lệnh Mermaid PHẢI nằm trên \
một dòng riêng — KHÔNG dùng dấu chấm phẩy để nối nhiều câu lệnh trên một dòng. \
Xuống dòng trong nhãn nút dùng <br> (KHÔNG dùng <br/>).
- Công thức và phương trình toán học LUÔN viết bằng ký hiệu LaTeX. KHÔNG dùng \
code span hay ký tự Unicode cho toán."""

# ─── Standard Report Footer ─────────────────────────────────────────────────
# Appended to every generated report after content generation.

_REPORT_FOOTER = "Powered by SurfSense AI."

# ─── Prompt: Single-Shot Report Generation ───────────────────────────────────

_REPORT_PROMPT = """Bạn là một chuyên gia viết báo cáo. Hãy viết một báo cáo Markdown toàn diện, BẰNG TIẾNG VIỆT (trừ khi Hướng dẫn bổ sung yêu cầu ngôn ngữ khác).

**Chủ đề:** {topic}
**Kiểu báo cáo:** {report_style}
{user_instructions_section}
{previous_version_section}

**Nội dung nguồn:**
{source_content}

---

{length_instruction}

Viết một báo cáo Markdown có cấu trúc tốt: tiêu đề mức #, phần tóm tắt, các mục được tổ chức hợp lý và kết luận. Nếu Hướng dẫn bổ sung mô tả một định dạng/bố cục báo cáo mẫu thì PHẢI theo đúng bố cục, cách đặt tiêu đề và văn phong đó thay vì bố cục mặc định. Dẫn các sự kiện, số liệu từ nội dung nguồn. Viết kỹ lưỡng và chuyên nghiệp.

{formatting_rules}
"""

# ─── Prompt: Full-Document Revision (fallback when section-level fails) ──────

_REVISION_PROMPT = """Bạn là một biên tập viên báo cáo chuyên nghiệp. CHỈ thực hiện các thay đổi được yêu cầu — KHÔNG viết lại từ đầu.

**Chủ đề:** {topic}
**Kiểu báo cáo:** {report_style}
**Yêu cầu chỉnh sửa:** {user_instructions_section}

**Nội dung nguồn (dùng nếu liên quan):**
{source_content}

---

**BÁO CÁO HIỆN TẠI:**

{previous_report_content}

---

{length_instruction}

Giữ nguyên toàn bộ cấu trúc và nội dung không bị ảnh hưởng bởi yêu cầu chỉnh sửa. Giữ nguyên ngôn ngữ của báo cáo hiện tại trừ khi được yêu cầu đổi.

{formatting_rules}
"""

# ─── Prompt: Section-Level Revision — Identify Affected Sections ─────────────

_IDENTIFY_SECTIONS_PROMPT = """Bạn đang phân tích một báo cáo Markdown để xác định những phần (section) cần chỉnh sửa theo yêu cầu của người dùng.

**Yêu cầu chỉnh sửa của người dùng:** {user_instructions}

**Các phần của báo cáo (đánh chỉ số từ 0):**
{sections_listing}

---

Xác định những phần cần sửa, thêm hoặc xóa để đáp ứng yêu cầu của người dùng.

CHỈ trả về một đối tượng JSON với các trường sau:
- "modify": mảng chỉ số (bắt đầu từ 0) của các phần cần sửa nội dung
- "add": mảng các đối tượng dạng {{"after_index": 2, "heading": "## Tiêu đề phần mới", "description": "Phần này cần trình bày gì"}} cho các phần mới cần chèn
- "remove": mảng chỉ số của các phần cần xóa hẳn (hạn chế dùng)
- "reasoning": giải thích ngắn gọn quyết định của bạn

Hướng dẫn:
- Nếu thay đổi mang tính TOÀN CỤC (ví dụ: "đổi giọng văn", "rút ngắn cả báo cáo", "dịch sang tiếng Anh"), đưa TẤT CẢ chỉ số vào "modify".
- Nếu thay đổi mang tính CỤC BỘ (ví dụ: "mở rộng phần ngân sách", "sửa phần kết luận"), CHỈ đưa các chỉ số bị ảnh hưởng.
- Với "thêm một phần về X", dùng trường "add" với vị trí chèn phù hợp.
- Ưu tiên sửa thay vì xóa rồi thêm mới khi có thể.

CHỈ trả về JSON hợp lệ, không bọc trong khối markdown:
"""

# ─── Prompt: Section-Level Revision — Revise a Single Section ────────────────

_REVISE_SECTION_PROMPT = """CHỈ chỉnh sửa phần dưới đây theo yêu cầu. Nếu yêu cầu không liên quan đến phần này, trả lại NGUYÊN VĂN không đổi.

**Yêu cầu chỉnh sửa:** {user_instructions}

**Phần hiện tại:**
{section_content}

**Ngữ cảnh (các phần xung quanh — chỉ để giữ mạch văn, KHÔNG xuất chúng ra):**
{context_sections}

**Nội dung nguồn:**
{source_content}

---

Giữ nguyên tiêu đề và cấp tiêu đề của phần. Giữ nguyên nội dung không bị ảnh hưởng bởi yêu cầu chỉnh sửa. Giữ nguyên ngôn ngữ của báo cáo trừ khi được yêu cầu đổi.
{formatting_rules}
"""

# ─── Prompt: New Section Generation (for section-level add) ─────────────────

_NEW_SECTION_PROMPT = """Bạn là một chuyên gia viết báo cáo. Hãy viết một phần mới để chèn vào một báo cáo có sẵn, dùng đúng ngôn ngữ của báo cáo đó (mặc định là tiếng Việt).

**Chủ đề báo cáo:** {topic}
**Kiểu báo cáo:** {report_style}
**Tiêu đề phần:** {heading}
**Mục tiêu của phần:** {description}
**Yêu cầu của người dùng:** {user_instructions}

**Ngữ cảnh xung quanh:**
{context_sections}

**Nội dung nguồn:**
{source_content}

---

**Quy tắc:**
1. CHỈ viết phần này, bắt đầu bằng tiêu đề "{heading}".
2. Đảm bảo phần mới liền mạch với ngữ cảnh xung quanh.
3. Viết đầy đủ — bao quát trọn vẹn chủ đề mô tả ở trên.
{formatting_rules}

Viết phần mới ngay bây giờ:
"""


# ─── Utility Functions ──────────────────────────────────────────────────────


def _strip_wrapping_code_fences(text: str) -> str:
    """Remove wrapping code fences that LLMs often add around Markdown output.

    Handles patterns like:
        ```markdown\\n...content...\\n```
        ````markdown\\n...content...\\n````
        ```md\\n...content...\\n```
        ```\\n...content...\\n```
        ```json\\n...content...\\n```
    Supports 3 or more backticks (LLMs escalate when content has triple-backtick blocks).
    """
    stripped = text.strip()
    # Match opening fence with 3+ backticks and optional language tag
    m = re.match(r"^(`{3,})(?:markdown|md|json)?\s*\n", stripped)
    if m:
        fence = m.group(1)  # e.g. "```" or "````"
        if stripped.endswith(fence):
            stripped = stripped[m.end() :]  # remove opening fence
            stripped = stripped[: -len(fence)].rstrip()  # remove closing fence
    return stripped


def _extract_metadata(content: str) -> dict[str, Any]:
    """Extract metadata from generated Markdown content."""
    headings = re.findall(r"^(#{1,6})\s+(.+)$", content, re.MULTILINE)
    word_count = len(content.split())
    char_count = len(content)

    return {
        "status": "ready",
        "word_count": word_count,
        "char_count": char_count,
        "section_count": len(headings),
    }


def _parse_sections(content: str) -> list[dict[str, str]]:
    """Parse Markdown content into sections split by # and ## headings.

    Returns a list of dicts: [{"heading": "## Title", "body": "content..."}, ...]
    Content before the first heading is captured with heading="".
    ### and deeper headings are kept inside their parent ## section's body.
    """
    lines = content.split("\n")
    sections: list[dict[str, str]] = []
    current_heading = ""
    current_body_lines: list[str] = []
    in_code_block = False

    for line in lines:
        # Track fences so headings inside code blocks aren't treated as splits.
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_block = not in_code_block

        is_section_heading = (
            not in_code_block
            and re.match(r"^#{1,2}\s+", line)
            and not re.match(r"^#{3,}\s+", line)
        )

        if is_section_heading:
            if current_heading or current_body_lines:
                sections.append(
                    {
                        "heading": current_heading,
                        "body": "\n".join(current_body_lines).strip(),
                    }
                )
            current_heading = line.strip()
            current_body_lines = []
        else:
            current_body_lines.append(line)

    if current_heading or current_body_lines:
        sections.append(
            {
                "heading": current_heading,
                "body": "\n".join(current_body_lines).strip(),
            }
        )

    return sections


def _stitch_sections(sections: list[dict[str, str]]) -> str:
    """Stitch parsed sections back into a single Markdown string."""
    parts = []
    for section in sections:
        if section["heading"]:
            parts.append(section["heading"])
        if section["body"]:
            parts.append(section["body"])
    return "\n\n".join(parts)


# ─── Async Generation Helpers ───────────────────────────────────────────────


async def _revise_with_sections(
    llm: Any,
    parent_content: str,
    user_instructions: str,
    source_content: str,
    topic: str,
    report_style: str,
) -> str | None:
    """Section-level revision: identify affected sections and revise only those.

    Unchanged sections are kept byte-for-byte identical.
    Returns the revised content, or None to trigger full-document revision fallback.
    """
    sections = _parse_sections(parent_content)
    if len(sections) < 2:
        logger.info(
            "[generate_report] Too few sections for section-level revision, using full revision"
        )
        return None

    sections_listing = ""
    for i, sec in enumerate(sections):
        heading = sec["heading"] or "(phần mở đầu — nội dung trước tiêu đề đầu tiên)"
        body_preview = (
            sec["body"][:200] + "..." if len(sec["body"]) > 200 else sec["body"]
        )
        sections_listing += f"\n[{i}] {heading}\n    Preview: {body_preview}\n"

    # Step 1: Ask LLM which sections need modification
    identify_prompt = _IDENTIFY_SECTIONS_PROMPT.format(
        user_instructions=user_instructions,
        sections_listing=sections_listing,
    )

    try:
        response = await llm.ainvoke([HumanMessage(content=identify_prompt)])
        raw = response.content
        if not raw or not isinstance(raw, str):
            return None

        raw = _strip_wrapping_code_fences(raw).strip()
        json_match = re.search(r"\{[\s\S]*\}", raw)
        if json_match:
            raw = json_match.group(0)

        plan = json.loads(raw)
        modify_indices: list[int] = plan.get("modify", [])
        add_sections: list[dict[str, Any]] = plan.get("add", [])
        remove_indices: list[int] = plan.get("remove", [])
        reasoning = plan.get("reasoning", "")

        logger.info(
            f"[generate_report] Section-level revision plan: "
            f"modify={modify_indices}, add={len(add_sections)}, "
            f"remove={remove_indices}, reasoning={reasoning}"
        )
    except Exception:
        logger.warning(
            "[generate_report] Failed to identify sections for revision, "
            "falling back to full revision",
            exc_info=True,
        )
        return None

    # If ALL sections need modification, full revision is more efficient and coherent
    if len(modify_indices) >= len(sections):
        logger.info(
            "[generate_report] All sections need modification, deferring to full revision"
        )
        return None

    total_ops = len(modify_indices) + len(add_sections)
    current_op = 0

    parts = []
    if modify_indices:
        parts.append(
            f"modifying {len(modify_indices)} section{'s' if len(modify_indices) > 1 else ''}"
        )
    if add_sections:
        parts.append(
            f"adding {len(add_sections)} new section{'s' if len(add_sections) > 1 else ''}"
        )
    if remove_indices:
        parts.append(
            f"removing {len(remove_indices)} section{'s' if len(remove_indices) > 1 else ''}"
        )
    plan_summary = ", ".join(parts) if parts else "no changes needed"

    dispatch_custom_event(
        "report_progress",
        {
            "phase": "revision_plan",
            "message": plan_summary.capitalize(),
            "modify_count": len(modify_indices),
            "add_count": len(add_sections),
            "remove_count": len(remove_indices),
            "total_ops": total_ops,
        },
    )

    # Step 2: Revise only the affected sections
    revised_sections = list(sections)  # shallow copy — unmodified sections stay as-is

    for idx in modify_indices:
        if idx < 0 or idx >= len(sections):
            continue

        current_op += 1
        sec = sections[idx]

        section_name = (
            re.sub(r"^#+\s*", "", sec["heading"]).strip()
            if sec["heading"]
            else "Preamble"
        )
        dispatch_custom_event(
            "report_progress",
            {
                "phase": "revising_section",
                "message": f"Revising: {section_name} ({current_op}/{total_ops})...",
            },
        )

        section_content = (
            f"{sec['heading']}\n\n{sec['body']}" if sec["heading"] else sec["body"]
        )

        context_parts = []
        if idx > 0:
            prev = sections[idx - 1]
            prev_preview = prev["body"][:300] + (
                "..." if len(prev["body"]) > 300 else ""
            )
            context_parts.append(f"**Phần trước:** {prev['heading']}\n{prev_preview}")
        if idx < len(sections) - 1:
            nxt = sections[idx + 1]
            nxt_preview = nxt["body"][:300] + ("..." if len(nxt["body"]) > 300 else "")
            context_parts.append(f"**Phần sau:** {nxt['heading']}\n{nxt_preview}")
        context = (
            "\n\n".join(context_parts)
            if context_parts
            else "(Không có phần xung quanh)"
        )

        revise_prompt = _REVISE_SECTION_PROMPT.format(
            user_instructions=user_instructions,
            section_content=section_content,
            context_sections=context,
            source_content=source_content[:40000],
            formatting_rules=_FORMATTING_RULES,
        )

        resp = await llm.ainvoke([HumanMessage(content=revise_prompt)])
        revised_text = resp.content
        if revised_text and isinstance(revised_text, str):
            revised_text = _strip_wrapping_code_fences(revised_text).strip()
            revised_parsed = _parse_sections(revised_text)
            if revised_parsed:
                revised_sections[idx] = revised_parsed[0]
            else:
                revised_sections[idx] = {
                    "heading": sec["heading"],
                    "body": revised_text,
                }

        logger.info(f"[generate_report] Revised section [{idx}]: {sec['heading']}")

    # Step 3: Handle new section additions (insert in reverse order to preserve indices)
    for add_info in sorted(
        add_sections,
        key=lambda x: x.get("after_index", len(revised_sections) - 1),
        reverse=True,
    ):
        current_op += 1
        after_idx = add_info.get("after_index", len(revised_sections) - 1)
        heading = add_info.get("heading", "## Phần mới")
        description = add_info.get("description", "")

        plain_heading = re.sub(r"^#+\s*", "", heading).strip()
        dispatch_custom_event(
            "report_progress",
            {
                "phase": "adding_section",
                "message": f"Adding: {plain_heading} ({current_op}/{total_ops})...",
            },
        )

        ctx_parts = []
        if 0 <= after_idx < len(revised_sections):
            before_sec = revised_sections[after_idx]
            ctx_parts.append(
                f"**Phần đứng trước:** {before_sec['heading']}\n{before_sec['body'][:300]}"
            )
        insert_idx = min(after_idx + 1, len(revised_sections))
        if insert_idx < len(revised_sections):
            after_sec = revised_sections[insert_idx]
            ctx_parts.append(
                f"**Phần đứng sau:** {after_sec['heading']}\n{after_sec['body'][:300]}"
            )

        new_prompt = _NEW_SECTION_PROMPT.format(
            topic=topic,
            report_style=report_style,
            heading=heading,
            description=description,
            user_instructions=user_instructions,
            context_sections="\n\n".join(ctx_parts) if ctx_parts else "(Không có)",
            source_content=source_content[:30000],
            formatting_rules=_FORMATTING_RULES,
        )

        resp = await llm.ainvoke([HumanMessage(content=new_prompt)])
        new_content = resp.content
        if new_content and isinstance(new_content, str):
            new_content = _strip_wrapping_code_fences(new_content).strip()
            new_parsed = _parse_sections(new_content)
            if new_parsed:
                revised_sections.insert(insert_idx, new_parsed[0])
            else:
                revised_sections.insert(
                    insert_idx,
                    {
                        "heading": heading,
                        "body": new_content,
                    },
                )

        logger.info(
            f"[generate_report] Added new section after [{after_idx}]: {heading}"
        )

    # Step 4: Handle removals (reverse order to preserve indices)
    for idx in sorted(remove_indices, reverse=True):
        if 0 <= idx < len(revised_sections):
            logger.info(
                f"[generate_report] Removed section [{idx}]: "
                f"{revised_sections[idx]['heading']}"
            )
            revised_sections.pop(idx)

    return _stitch_sections(revised_sections)


# ─── Tool Factory ───────────────────────────────────────────────────────────

def _noop_progress(name: str, data: dict[str, Any]) -> None:
    """Progress sink for callers outside a LangGraph run."""


async def generate_report_document(
    *,
    topic: str,
    search_space_id: int,
    thread_id: int | None = None,
    source_content: str = "",
    source_strategy: str = "provided",
    search_queries: list[str] | None = None,
    report_style: str = "detailed",
    user_instructions: str | None = None,
    parent_report_id: int | None = None,
    available_connectors: list[str] | None = None,
    available_document_types: list[str] | None = None,
    allow_kb_search: bool = True,
    emit_progress: Callable[[str, dict[str, Any]], None] = _noop_progress,
) -> dict[str, Any]:
    """Search, write and persist one report. Returns the result payload.

    The whole pipeline: optional multi-query KB search, single-shot generation
    (or section-aware revision when ``parent_report_id`` is set), fence
    stripping, metadata extraction, and the ``Report`` row write. Every DB
    session is short-lived so no connection is held across the LLM call.

    Never raises for expected failures — on error it persists a failed report
    row and returns ``{"status": "failed", "error": ..., "report_id": ...}``,
    so both callers can report the same way.

    Args:
        thread_id: Chat to attribute the report to, already resolved. Tool
            callers pass ``resolve_root_thread_id(runtime, ...)``.
        allow_kb_search: Gate on the internal KB search, mirroring the tool's
            historical ``connector_service`` check.
        emit_progress: Called as ``(event_name, payload)`` at each phase.
            Defaults to a no-op for non-graph callers.
    """
    # Shared with the _save_failed_report closure.
    parent_report_content: str | None = None
    report_group_id: int | None = None

    def _failed(payload: dict[str, Any], *, error: str) -> dict[str, Any]:
        payload["error"] = error
        return payload

    async def _save_failed_report(error_msg: str) -> int | None:
        """Persist a failed report row using a short-lived session."""
        try:
            async with shielded_async_session() as session:
                failed_report = Report(
                    title=topic,
                    content=None,
                    report_metadata={
                        "status": "failed",
                        "error_message": error_msg,
                    },
                    report_style=report_style,
                    search_space_id=search_space_id,
                    thread_id=thread_id,
                    report_group_id=report_group_id,
                )
                session.add(failed_report)
                await session.commit()
                await session.refresh(failed_report)
                # New group (v1 failed): point the group at itself.
                if not failed_report.report_group_id:
                    failed_report.report_group_id = failed_report.id
                    await session.commit()
                logger.info(
                    f"[generate_report] Saved failed report {failed_report.id}: {error_msg}"
                )
                return failed_report.id
        except Exception:
            logger.exception(
                "[generate_report] Could not persist failed report row"
            )
            return None

    try:
        # ── Phase 1: READ (short-lived session) ──────────────────────
        # Fetch parent report + LLM config, then release the connection
        # before the long LLM call.
        async with shielded_async_session() as read_session:
            if parent_report_id:
                parent_report = await read_session.get(Report, parent_report_id)
                if parent_report:
                    report_group_id = parent_report.report_group_id
                    parent_report_content = parent_report.content
                    logger.info(
                        f"[generate_report] Creating new version from parent {parent_report_id} "
                        f"(group {report_group_id})"
                    )
                else:
                    logger.warning(
                        f"[generate_report] parent_report_id={parent_report_id} not found, "
                        "creating standalone report"
                    )

            llm = await get_agent_llm(read_session, search_space_id)

        if not llm:
            error_msg = (
                "No LLM configured. Please configure a language model in Settings."
            )
            report_id = await _save_failed_report(error_msg)
            return _failed(
                {
                    "status": "failed",
                    "error": error_msg,
                    "report_id": report_id,
                    "title": topic,
                },
                error=error_msg,
            )

        user_instructions_section = ""
        if user_instructions:
            user_instructions_section = (
                f"**Hướng dẫn bổ sung:** {user_instructions}"
            )

        # ── Phase 1b: SOURCE COLLECTION (smart KB search) ────────────
        # Decide whether to augment source_content with KB search results.
        effective_source = source_content or ""

        strategy = (source_strategy or "provided").lower().strip()

        needs_kb_search = False
        if strategy == "kb_search":
            needs_kb_search = True
        elif strategy == "auto":
            # Heuristic: if source_content has fewer than 200 words,
            # it's likely insufficient — augment with KB search.
            word_count_estimate = len(effective_source.split())
            if word_count_estimate < 200:
                needs_kb_search = True
                logger.info(
                    f"[generate_report] auto strategy: source has ~{word_count_estimate} words, "
                    "triggering KB search"
                )
        # "provided" and "conversation" → use source_content as-is

        if needs_kb_search and allow_kb_search and search_queries:
            query_count = min(len(search_queries), 5)
            emit_progress(
                "report_progress",
                {
                    "phase": "kb_search",
                    "message": f"Searching knowledge base ({query_count} queries)...",
                },
            )
            logger.info(
                f"[generate_report] Running internal KB search with "
                f"{query_count} queries: {search_queries[:5]}"
            )
            try:
                from app.agents.chat.multi_agent_chat.shared.retrieval.hybrid_search import (
                    search_chunks,
                )
                from app.agents.chat.multi_agent_chat.shared.retrieval.models import (
                    DocumentHit,
                    SearchScope,
                )

                scope = SearchScope(
                    document_types=_report_search_types(
                        available_connectors, available_document_types
                    )
                )

                # Each query gets its own short-lived session.
                async def _run_single_query(q: str) -> list[DocumentHit]:
                    async with shielded_async_session() as kb_session:
                        return await search_chunks(
                            kb_session,
                            search_space_id=search_space_id,
                            query=q,
                            scope=scope,
                            top_k=10,
                        )

                hits_per_query = await asyncio.gather(
                    *[_run_single_query(q) for q in search_queries[:5]]
                )

                seen_doc_ids: set[int] = set()
                merged_hits: list[DocumentHit] = []
                for hits in hits_per_query:
                    for hit in hits:
                        if hit.document_id in seen_doc_ids:
                            continue
                        seen_doc_ids.add(hit.document_id)
                        merged_hits.append(hit)

                kb_combined = _render_kb_hits_for_report(merged_hits)
                if kb_combined.strip():
                    if effective_source.strip():
                        effective_source = (
                            effective_source
                            + "\n\n--- Kết quả tìm kiếm trong kho tri thức ---\n\n"
                            + kb_combined
                        )
                    else:
                        effective_source = kb_combined

                    doc_count = len(merged_hits)
                    emit_progress(
                        "report_progress",
                        {
                            "phase": "kb_search_done",
                            "message": f"Found {doc_count} relevant documents",
                        },
                    )
                    logger.info(
                        f"[generate_report] KB search added ~{len(kb_combined)} chars "
                        f"from {doc_count} documents"
                    )
                else:
                    emit_progress(
                        "report_progress",
                        {
                            "phase": "kb_search_done",
                            "message": "No results found in knowledge base",
                        },
                    )
                    logger.info("[generate_report] KB search returned no results")

            except Exception as e:
                logger.warning(
                    f"[generate_report] Internal KB search failed: {e}. "
                    "Proceeding with existing source_content."
                )
        elif needs_kb_search and not allow_kb_search:
            logger.warning(
                "[generate_report] KB search requested but KB search is disabled "
                "not available. Using source_content as-is."
            )
        elif needs_kb_search and not search_queries:
            logger.warning(
                "[generate_report] KB search requested but no search_queries "
                "provided. Using source_content as-is."
            )

        capped_source = effective_source[:100000]

        # Length constraint only when the user explicitly asked for brevity.
        length_instruction = ""
        if report_style == "brief":
            length_instruction = (
                "**GIỚI HẠN ĐỘ DÀI (BẮT BUỘC):** Người dùng muốn một báo cáo NGẮN. "
                "Viết cô đọng — khoảng 400 từ (~1 trang), trừ khi Hướng dẫn bổ sung "
                "ở trên yêu cầu độ dài khác. Ưu tiên ngắn gọn hơn là đầy đủ. "
                "KHÔNG viết báo cáo dài."
            )

        # ── Phase 2: LLM GENERATION (no DB connection held) ──────────

        report_content: str | None = None

        if parent_report_content:
            # Revision mode: section-level first (preserves untouched
            # sections), falling back to full-doc revision.
            emit_progress(
                "report_progress",
                {
                    "phase": "revision_start",
                    "message": "Analyzing sections to modify...",
                },
            )
            logger.info(
                "[generate_report] Revision mode — attempting section-level revision"
            )
            report_content = await _revise_with_sections(
                llm=llm,
                parent_content=parent_report_content,
                user_instructions=user_instructions
                or "Cải thiện và trau chuốt báo cáo.",
                source_content=capped_source,
                topic=topic,
                report_style=report_style,
            )

            if report_content is None:
                emit_progress(
                    "report_progress",
                    {"phase": "writing", "message": "Rewriting your full report"},
                )
                logger.info(
                    "[generate_report] Section-level revision deferred, "
                    "using full-document revision"
                )
                prompt = _REVISION_PROMPT.format(
                    topic=topic,
                    report_style=report_style,
                    user_instructions_section=user_instructions_section
                    or "Cải thiện và trau chuốt báo cáo.",
                    source_content=capped_source,
                    previous_report_content=parent_report_content,
                    length_instruction=length_instruction,
                    formatting_rules=_FORMATTING_RULES,
                )
                response = await llm.ainvoke([HumanMessage(content=prompt)])
                report_content = response.content

        else:
            # New report: single-shot generation (one LLM call).
            emit_progress(
                "report_progress",
                {"phase": "writing", "message": "Writing your report"},
            )
            logger.info(
                "[generate_report] New report — using single-shot generation"
            )
            prompt = _REPORT_PROMPT.format(
                topic=topic,
                report_style=report_style,
                user_instructions_section=user_instructions_section,
                previous_version_section="",
                source_content=capped_source,
                length_instruction=length_instruction,
                formatting_rules=_FORMATTING_RULES,
            )
            response = await llm.ainvoke([HumanMessage(content=prompt)])
            report_content = response.content

        if not report_content or not isinstance(report_content, str):
            error_msg = "LLM returned empty or invalid content"
            report_id = await _save_failed_report(error_msg)
            return _failed(
                {
                    "status": "failed",
                    "error": error_msg,
                    "report_id": report_id,
                    "title": topic,
                },
                error=error_msg,
            )

        # LLMs often wrap output in ```markdown ... ``` fences — strip them
        report_content = _strip_wrapping_code_fences(report_content)

        if not report_content:
            error_msg = "LLM returned empty or invalid content"
            report_id = await _save_failed_report(error_msg)
            return _failed(
                {
                    "status": "failed",
                    "error": error_msg,
                    "report_id": report_id,
                    "title": topic,
                },
                error=error_msg,
            )

        # Strip the branding footer, including any carried over from a parent
        # version. It is intentionally NOT re-appended — reports ship without it.
        while report_content.rstrip().endswith(_REPORT_FOOTER):
            idx = report_content.rstrip().rfind(_REPORT_FOOTER)
            report_content = report_content[:idx].rstrip()
            if report_content.rstrip().endswith("---"):
                report_content = report_content.rstrip()[:-3].rstrip()

        metadata = _extract_metadata(report_content)

        # ── Phase 3: WRITE (short-lived session) ─────────────────────
        async with shielded_async_session() as write_session:
            report = Report(
                title=topic,
                content=report_content,
                report_metadata=metadata,
                report_style=report_style,
                search_space_id=search_space_id,
                thread_id=thread_id,
                report_group_id=report_group_id,
            )
            write_session.add(report)
            await write_session.commit()
            await write_session.refresh(report)

            # Brand-new report (v1): point the group at itself.
            if not report.report_group_id:
                report.report_group_id = report.id
                await write_session.commit()

            saved_report_id = report.id
            saved_group_id = report.report_group_id

        logger.info(
            f"[generate_report] Created report {saved_report_id} "
            f"(group={saved_group_id}): "
            f"{metadata.get('word_count', 0)} words, "
            f"{metadata.get('section_count', 0)} sections"
        )

        payload: dict[str, Any] = {
            "status": "ready",
            "report_id": saved_report_id,
            "title": topic,
            "word_count": metadata.get("word_count", 0),
            "is_revision": bool(parent_report_content),
            "report_markdown": report_content,
            "message": f"Report generated successfully: {topic}",
        }
        return payload

    except Exception as e:
        error_message = str(e)
        logger.exception(f"[generate_report] Error: {error_message}")
        report_id = await _save_failed_report(error_message)
        return _failed(
            {
                "status": "failed",
                "error": error_message,
                "report_id": report_id,
                "title": topic,
            },
            error=error_message,
        )


def create_generate_report_tool(
    search_space_id: int,
    thread_id: int | None = None,
    connector_service: ConnectorService | None = None,
    available_connectors: list[str] | None = None,
    available_document_types: list[str] | None = None,
):
    """Create the generate_report tool with injected dependencies.

    Uses short-lived DB sessions per operation so no connection is held during
    the long LLM call. Generation: new reports are single-shot; revisions try
    section-level first (unchanged sections preserved) and fall back to full-doc.
    Source strategies: provided/conversation (use source_content), kb_search
    (internal KB queries), auto (KB search only when source_content is thin).
    """

    @tool
    async def generate_report(
        topic: str,
        runtime: ToolRuntime,
        source_content: str = "",
        source_strategy: str = "provided",
        search_queries: list[str] | None = None,
        report_style: str = "detailed",
        user_instructions: str | None = None,
        parent_report_id: int | None = None,
    ) -> Command:
        """
        Generate a structured Markdown report artifact from provided content.

        Use this tool when the user asks to create, generate, write, produce,
        draft, or summarize into a report-style deliverable.

        Trigger classes include:
        - Direct trigger words WITH creation/modification verb: report,
          document, memo, letter, template, article, guide, blog post,
          one-pager, briefing, comprehensive guide.
        - Creation-intent phrases: "write a report", "generate a document",
          "draft a summary", "create an executive summary".
        - Modification-intent phrases: "revise the report", "update the
          report", "make it shorter", "add a section about X", "expand the
          budget section", "rewrite in formal tone".

        IMPORTANT — what does NOT count as "asking for a report":
        - Questions or discussion about a report or its topic are NOT report
          requests. Respond to these conversationally in chat.
          Examples: "What other examples to put there?", "What else could be
          added?", "Can you explain section 2?", "Is the data accurate?",
          "What's missing?", "How could this be improved?", "What other
          topics are related?"
        - Quick summary requests, explanations, or follow-up questions.
        - The test: Does the message contain a creation/modification VERB
          (write, create, generate, draft, add, revise, update, expand,
          rewrite, make) directed at producing a deliverable? If no verb
          → answer in chat.

        FORMAT/EXPORT RULE:
        - Always generate the report content in Markdown.
        - If the user requests DOCX/Word/PDF or another file format, export
          from the generated Markdown report.

        SOURCE STRATEGY (how to collect source material):
        - source_strategy="conversation" — The conversation already has
          enough context (prior Q&A, filesystem exploration, pasted text,
          uploaded files, scraped webpages). Pass a thorough summary as
          source_content.
        - source_strategy="kb_search" — Search the knowledge base
          internally. Provide 1-5 targeted search_queries. The tool
          handles searching internally — do NOT manually read and dump
          /documents/ files into source_content.
        - source_strategy="provided" — Use only what is in source_content
          (default, backward-compatible).
        - source_strategy="auto" — Use source_content if it has enough
          material; otherwise fall back to internal KB search using
          search_queries.

        CONVERSATION REUSE (HIGH PRIORITY):
        - If the user has been asking questions in this chat and the
          conversation contains substantive answers/discussion on the
          topic, prefer source_strategy="conversation" with a thorough
          summary of the full chat history as source_content.
        - The user's prior questions and your answers ARE the source
          material. Do NOT redundantly search the knowledge base for
          information that is already in the chat.

        VERSIONING — parent_report_id:
        - Set parent_report_id when the user wants to MODIFY, REVISE,
          IMPROVE, UPDATE, EXPAND, or ADD CONTENT TO an existing report
          that was already generated in this conversation.
        - This includes both explicit AND implicit modification requests.
          If the user references the existing report using words like "it",
          "this", "here", "the report", or clearly refers to a previously
          generated report, treat it as a revision request.
        - The value must be the report_id from a previous generate_report
          result in this same conversation.
        - Do NOT set parent_report_id when:
          * The user asks for a report on a completely NEW/DIFFERENT topic
          * The user says "generate another report" (new report, not revision)
          * There is no prior report to reference

        Examples of when to SET parent_report_id:
          User: "Make that report shorter" → parent_report_id = <previous report_id>
          User: "Add a cost analysis section to the report" → parent_report_id = <previous report_id>
          User: "Rewrite the report in a more formal tone" → parent_report_id = <previous report_id>
          User: "I want more details about pricing in here" → parent_report_id = <previous report_id>
          User: "Include more examples" → parent_report_id = <previous report_id>
          User: "Can you also cover nutrition in this?" → parent_report_id = <previous report_id>
          User: "Make it more detailed" → parent_report_id = <previous report_id>
          User: "Not bad, but expand on the budget section" → parent_report_id = <previous report_id>
          User: "Also mention the competitor landscape" → parent_report_id = <previous report_id>

        Examples of when to LEAVE parent_report_id as None:
          User: "Generate a report on climate change" → None (new topic)
          User: "Write me a report about the budget" → None (new topic)
          User: "Create another report, this time about marketing" → None
          User: "Now write one about travel trends in Europe" → None (new topic)

        Args:
            topic: Short title for the report (max ~8 words).
            source_content: Text to base the report on. Can be empty when
                using source_strategy="kb_search".
            source_strategy: How to collect source material. One of
                "provided", "conversation", "kb_search", or "auto".
            search_queries: When source_strategy is "kb_search" or "auto",
                provide 1-5 targeted search queries for the knowledge base.
                These should be specific, not just the topic repeated.
            report_style: "detailed", "deep_research", or "brief".
            user_instructions: Optional focus or modification instructions.
                When revising (parent_report_id set), describe WHAT TO CHANGE.
            parent_report_id: ID of a previous report to revise (creates new
                version in the same version group).

        Returns:
            Dict with status, report_id, title, word_count, and message.
        """
        payload = await generate_report_document(
            topic=topic,
            search_space_id=search_space_id,
            thread_id=resolve_root_thread_id(runtime, thread_id),
            source_content=source_content,
            source_strategy=source_strategy,
            search_queries=search_queries,
            report_style=report_style,
            user_instructions=user_instructions,
            parent_report_id=parent_report_id,
            available_connectors=available_connectors,
            available_document_types=available_document_types,
            allow_kb_search=connector_service is not None,
            emit_progress=dispatch_custom_event,
        )

        succeeded = payload.get("status") == "ready"
        report_id = payload.get("report_id")
        return with_receipt(
            payload=payload,
            receipt=make_receipt(
                route="deliverables",
                type="report",
                operation="generate",
                status="success" if succeeded else "failed",
                external_id=str(report_id) if report_id is not None else None,
                preview=topic,
                error=None if succeeded else payload.get("error"),
            ),
            tool_call_id=runtime.tool_call_id,
        )

    return generate_report
