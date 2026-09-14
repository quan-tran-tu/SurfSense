"""The ``<session_context>`` block: facts the user supplies for one chat session.

The knowledge base lags the world. When an official changes post, every document
still names the old one, and a grounded answer faithfully repeats it. The user
knows better and says so once; the web client keeps that note per session and
sends it with every turn (``NewChatRequest.session_context``). Both chat lanes
render it with this one function, so the agent and the retrieve-then-answer flow
read the same words.

Nothing here is persisted server-side or written to the knowledge base: the note
belongs to the session, and leaving it out of a request leaves it out of the answer.
"""

from __future__ import annotations


def render_session_context_block(text: str | None) -> str | None:
    """Wrap the user's note, or ``None`` when there is nothing to say."""
    note = (text or "").strip()
    if not note:
        return None
    return (
        "<session_context>\n"
        "Facts the user supplied for this conversation. Treat them as true and more "
        "current than any document: where a document disagrees, the document is out "
        "of date.\n\n"
        f"{note}\n"
        "</session_context>"
    )


__all__ = ["render_session_context_block"]
