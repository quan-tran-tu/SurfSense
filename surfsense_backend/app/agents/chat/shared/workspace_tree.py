"""Render the workspace ``Folder``/``Document`` tree, with its exact totals.

One renderer, two callers. ``KnowledgeTreeMiddleware`` owns the agent path's
caching and injection; the ``simple_rag`` flow calls :func:`build_workspace_tree`
directly when a question mentions the workspace. Both must see the same tree —
two renderers would drift, and "how many folders do I have" would then have two
answers depending on which lane happened to answer it.

The totals are carried alongside the text rather than left to be counted off it.
Both truncation layers (``max_entries``, then ``max_tokens``) drop lines, and a
model asked to count what it was shown will confidently report the truncated
number. Handing it ``folder_count`` directly keeps the count correct at any
corpus size — which is what makes this cheap: no upload quota is needed to keep
counting honest.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.language_models import BaseChatModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.chat.runtime.path_resolver import (
    DOCUMENTS_ROOT,
    PathIndex,
    build_path_index,
    doc_to_virtual_path,
    readable_documents_filter,
)
from app.db import Document

MAX_TREE_ENTRIES = 500
MAX_TREE_TOKENS = 4000

try:
    from litellm import token_counter
except Exception:  # pragma: no cover - optional dep
    token_counter = None  # type: ignore[assignment]


@dataclass(frozen=True)
class WorkspaceTree:
    """The rendered tree plus the totals that survive truncation."""

    text: str
    folder_count: int
    document_count: int
    truncated: bool


def approx_tokens(text: str) -> int:
    """Cheap fallback token estimate (1 token ~= 4 chars)."""
    return max(1, (len(text) + 3) // 4)


def count_tokens(text: str, *, llm: BaseChatModel | None) -> int:
    if llm is None:
        return approx_tokens(text)
    count_fn = getattr(llm, "_count_tokens", None)
    if callable(count_fn):
        try:
            return int(count_fn([{"role": "user", "content": text}]))
        except Exception:
            pass
    profile = getattr(llm, "profile", None)
    model_names: list[str] = []
    if isinstance(profile, dict):
        tcms = profile.get("token_count_models")
        if isinstance(tcms, list):
            model_names.extend(name for name in tcms if isinstance(name, str) and name)
        tcm = profile.get("token_count_model")
        if isinstance(tcm, str) and tcm and tcm not in model_names:
            model_names.append(tcm)
    model_name = model_names[0] if model_names else getattr(llm, "model", None)
    if not isinstance(model_name, str) or not model_name or token_counter is None:
        return approx_tokens(text)
    try:
        return int(
            token_counter(
                messages=[{"role": "user", "content": text}],
                model=model_name,
            )
        )
    except Exception:
        return approx_tokens(text)


async def build_workspace_tree(
    session: AsyncSession,
    *,
    search_space_id: int,
    thread_id: int | None,
    llm: BaseChatModel | None = None,
    max_entries: int = MAX_TREE_ENTRIES,
    max_tokens: int = MAX_TREE_TOKENS,
) -> WorkspaceTree:
    """Read the readable folders/documents for a space and render them."""
    index = await build_path_index(session, search_space_id, thread_id=thread_id)
    doc_rows = await session.execute(
        select(Document.id, Document.title, Document.folder_id).where(
            readable_documents_filter(index, search_space_id)
        )
    )
    return format_workspace_tree(
        index,
        list(doc_rows.all()),
        llm=llm,
        max_entries=max_entries,
        max_tokens=max_tokens,
    )


def format_workspace_tree(
    index: PathIndex,
    docs: list[Any],
    *,
    llm: BaseChatModel | None = None,
    max_entries: int = MAX_TREE_ENTRIES,
    max_tokens: int = MAX_TREE_TOKENS,
) -> WorkspaceTree:
    """Render ``<workspace_tree>``; pure given the index and the document rows."""
    folder_paths = sorted(set(index.folder_paths.values()))
    doc_paths = sorted(
        doc_to_virtual_path(
            doc_id=row.id,
            title=str(row.title or "untitled"),
            folder_id=row.folder_id,
            index=index,
        )
        for row in docs
    )
    all_paths = sorted(set(folder_paths + doc_paths + [DOCUMENTS_ROOT]))

    # Pre-compute which folders have at least one descendant (folder or doc).
    # A folder is "empty" iff no path in `all_paths` is strictly under it.
    # Used to emit an explicit "(empty)" marker so the LLM doesn't have to
    # infer emptiness from indentation alone.
    non_empty_folders = _compute_non_empty_folders(folder_paths, doc_paths)

    lines: list[str] = []
    truncated = False
    for path in all_paths:
        depth = (
            0
            if path == DOCUMENTS_ROOT
            else len([p for p in path[len(DOCUMENTS_ROOT) :].split("/") if p])
        )
        indent = "  " * depth
        is_dir = path == DOCUMENTS_ROOT or path in folder_paths
        display = path.rsplit("/", 1)[-1] if path != DOCUMENTS_ROOT else "/documents"
        if is_dir:
            if path != DOCUMENTS_ROOT and path not in non_empty_folders:
                lines.append(f"{indent}{display}/ (empty)")
            else:
                lines.append(f"{indent}{display}/")
        else:
            lines.append(f"{indent}{display}")
        if len(lines) >= max_entries:
            remaining = len(all_paths) - len(lines)
            if remaining > 0:
                truncated = True
                lines.append(
                    f"... {remaining} more entries — use "
                    "ls('/documents/<folder>', offset, limit) to expand"
                )
            break

    body = "\n".join(lines)
    rendered = f"<workspace_tree>\n{body}\n</workspace_tree>"

    if count_tokens(rendered, llm=llm) > max_tokens:
        rendered = _format_root_summary(folder_paths, doc_paths)
        truncated = True

    return WorkspaceTree(
        text=rendered,
        folder_count=len(folder_paths),
        document_count=len(doc_paths),
        truncated=truncated,
    )


def _compute_non_empty_folders(
    folder_paths: list[str], doc_paths: list[str]
) -> set[str]:
    """Return the set of folder paths that contain at least one descendant.

    A folder is "non-empty" if any document path or any other folder path
    is strictly under it. Documents propagate emptiness up to every
    ancestor folder, while a sub-folder only marks its direct ancestors
    non-empty (so a chain of empty folders all read ``(empty)``).
    """
    non_empty: set[str] = set()
    folder_set = set(folder_paths)

    for doc_path in doc_paths:
        parent = doc_path.rsplit("/", 1)[0]
        while parent and parent != DOCUMENTS_ROOT:
            if parent in folder_set:
                non_empty.add(parent)
            parent = parent.rsplit("/", 1)[0]

    for child in folder_paths:
        parent = child.rsplit("/", 1)[0]
        while parent and parent != DOCUMENTS_ROOT and parent in folder_set:
            non_empty.add(parent)
            parent = parent.rsplit("/", 1)[0]

    return non_empty


def _format_root_summary(folder_paths: list[str], doc_paths: list[str]) -> str:
    top_level: dict[str, int] = {}
    loose_docs = 0
    for path in doc_paths:
        rel = path[len(DOCUMENTS_ROOT) :].lstrip("/")
        if "/" in rel:
            top = rel.split("/", 1)[0]
            top_level[top] = top_level.get(top, 0) + 1
        else:
            loose_docs += 1
    for path in folder_paths:
        rel = path[len(DOCUMENTS_ROOT) :].lstrip("/")
        if not rel:
            continue
        top = rel.split("/", 1)[0]
        top_level.setdefault(top, 0)

    lines = [DOCUMENTS_ROOT + "/"]
    for name in sorted(top_level):
        count = top_level[name]
        suffix = "s" if count != 1 else ""
        lines.append(f"  {name}/ ({count} document{suffix})")
    if loose_docs:
        suffix = "s" if loose_docs != 1 else ""
        lines.append(f"  ({loose_docs} loose document{suffix})")
    lines.append(
        "Tree is large; use list_tree('/documents/<folder>') to drill in "
        "or ls('/documents/<folder>', offset, limit) for paginated listings."
    )
    return "<workspace_tree>\n" + "\n".join(lines) + "\n</workspace_tree>"


__all__ = [
    "MAX_TREE_ENTRIES",
    "MAX_TREE_TOKENS",
    "WorkspaceTree",
    "build_workspace_tree",
    "format_workspace_tree",
    "approx_tokens",
    "count_tokens",
]
