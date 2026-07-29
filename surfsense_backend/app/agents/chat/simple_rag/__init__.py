"""Retrieve-then-answer chat flow: one hybrid search, one tool-free model call.

The alternative to :mod:`app.agents.chat.multi_agent_chat` for models too small
to drive an agent. See :mod:`.flow` for the rationale and the trade-offs.
"""

from __future__ import annotations

from .flow import stream_simple_rag
from .prompt import NO_RESULTS_MESSAGE, SIMPLE_RAG_SYSTEM_PROMPT

__all__ = [
    "NO_RESULTS_MESSAGE",
    "SIMPLE_RAG_SYSTEM_PROMPT",
    "stream_simple_rag",
]
