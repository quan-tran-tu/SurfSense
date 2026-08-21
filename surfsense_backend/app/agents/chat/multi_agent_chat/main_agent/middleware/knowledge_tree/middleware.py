"""Workspace-tree middleware for the SurfSense agent.

Renders the full ``Folder``+``Document`` tree under ``/documents/`` once per
turn (cloud only), caches it by ``(search_space_id, tree_version)``, and
injects the result as a ``<workspace_tree>`` system message immediately
before the latest human turn.

The render is bounded by two truncation layers:

1. **Entry cap** — at most ``MAX_TREE_ENTRIES`` lines. The remainder is
   replaced with a "use ls" hint.
2. **Token cap** — at most ``MAX_TREE_TOKENS`` tokens (using the LLM's
   token-count profile when available). If the entry-truncated tree still
   exceeds the token cap we fall back to a root-only summary.

Anonymous mode renders only ``state['kb_anon_doc']`` (no DB calls).

This middleware also performs a one-time initialization of ``state['cwd']``
to ``"/documents"`` so subsequent middlewares and tools always see a valid
cwd in cloud mode.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langgraph.runtime import Runtime

from app.agents.chat.multi_agent_chat.shared.filesystem_selection import FilesystemMode
from app.agents.chat.multi_agent_chat.shared.state.filesystem_state import (
    SurfSenseFilesystemState,
)
from app.agents.chat.runtime.path_resolver import (
    DOCUMENTS_ROOT,
    current_thread_id,
)
from app.agents.chat.shared.workspace_tree import (
    MAX_TREE_ENTRIES,
    MAX_TREE_TOKENS,
    build_workspace_tree,
)
from app.services.folder_sharing_service import live_link_fingerprint
from app.db import shielded_async_session
from app.utils.perf import get_perf_logger

_perf_log = get_perf_logger()

logger = logging.getLogger(__name__)



class KnowledgeTreeMiddleware(AgentMiddleware):  # type: ignore[type-arg]
    """Inject the workspace folder/document tree into the agent's context."""

    tools = ()
    state_schema = SurfSenseFilesystemState

    def __init__(
        self,
        *,
        search_space_id: int,
        filesystem_mode: FilesystemMode,
        llm: BaseChatModel | None = None,
        max_entries: int = MAX_TREE_ENTRIES,
        max_tokens: int = MAX_TREE_TOKENS,
        inject_system_message: bool = True,  # For backwards compatibility
    ) -> None:
        self.search_space_id = search_space_id
        self.filesystem_mode = filesystem_mode
        self.llm = llm
        self.max_entries = max_entries
        self.max_tokens = max_tokens
        self.inject_system_message = inject_system_message
        # (search_space_id, thread_id, tree_version, live_link_fingerprint)
        # -> rendered tree. The thread is part of the key because session-scoped
        # folders make the tree differ per chat, and this middleware instance is
        # shared across threads via the cached compiled graph.
        self._cache: dict[tuple[int, int | None, int, tuple[int, int]], str] = {}
        self._last_cache_outcome = "miss"

    async def abefore_agent(  # type: ignore[override]
        self,
        state: AgentState,
        runtime: Runtime[Any],
    ) -> dict[str, Any] | None:
        del runtime
        if self.filesystem_mode != FilesystemMode.CLOUD:
            return None

        start = time.perf_counter()
        update: dict[str, Any] = {}
        if not state.get("cwd"):
            update["cwd"] = DOCUMENTS_ROOT

        anon_doc = state.get("kb_anon_doc")
        if anon_doc:
            tree_msg = self._render_anon_tree(anon_doc)
            cache_outcome = "anon"
        else:
            # _render_kb_tree owns the key (it needs a session to build it) and
            # reports what it did.
            tree_msg = await self._render_kb_tree(state)
            cache_outcome = self._last_cache_outcome

        update["workspace_tree_text"] = tree_msg

        if self.inject_system_message:
            messages = list(state.get("messages") or [])
            insert_at = max(len(messages) - 1, 0)
            messages.insert(insert_at, SystemMessage(content=tree_msg))
            update["messages"] = messages

        _perf_log.info(
            "[knowledge_tree] cache=%s chars=%d elapsed=%.3fs space=%d",
            cache_outcome,
            len(tree_msg),
            time.perf_counter() - start,
            self.search_space_id,
        )
        return update

    def before_agent(  # type: ignore[override]
        self,
        state: AgentState,
        runtime: Runtime[Any],
    ) -> dict[str, Any] | None:
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                return None
        except RuntimeError:
            pass
        return asyncio.run(self.abefore_agent(state, runtime))

    # ------------------------------------------------------------------ render

    def _render_anon_tree(self, anon_doc: dict[str, Any]) -> str:
        path = str(anon_doc.get("path") or "")
        title = str(anon_doc.get("title") or "uploaded_document")
        return (
            "<workspace_tree>\n"
            "Anonymous session — only one read-only document is available.\n"
            f"{DOCUMENTS_ROOT}/\n"
            f"  {path} — {title}\n"
            "</workspace_tree>"
        )

    async def _render_kb_tree(self, state: AgentState) -> str:
        version = int(state.get("tree_version") or 0)
        thread_id = current_thread_id()

        try:
            async with shielded_async_session() as session:
                # The fingerprint has to be part of the key: /import and /unshare
                # change what this space can see without bumping tree_version,
                # which only advances when the agent mutates documents.
                fingerprint = await live_link_fingerprint(
                    session, self.search_space_id
                )
                cache_key = (self.search_space_id, thread_id, version, fingerprint)
                cached = self._cache.get(cache_key)
                self._last_cache_outcome = "hit" if cached is not None else "miss"
                if cached is not None:
                    return cached

                # Rendering lives in app.agents.chat.shared.workspace_tree so the
                # simple_rag flow shows the same tree this agent sees.
                tree = await build_workspace_tree(
                    session,
                    search_space_id=self.search_space_id,
                    thread_id=thread_id,
                    llm=self.llm,
                    max_entries=self.max_entries,
                    max_tokens=self.max_tokens,
                )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("knowledge_tree: DB error %s", exc)
            self._last_cache_outcome = "error"
            return "<workspace_tree>\n(unavailable)\n</workspace_tree>"

        self._cache[cache_key] = tree.text
        return tree.text


__all__ = ["KnowledgeTreeMiddleware"]
