"""Read the assistant text off a non-streaming model response.

``AIMessage.content`` is not reliably a string. Depending on the provider and
the model, a single ``ainvoke`` can come back as:

  * a plain ``str`` — the shape most call sites assume;
  * a **list of content blocks** (``[{"type": "text", ...}, ...]``), which is
    what reasoning-capable models return through several providers;
  * an empty string with the real payload in ``additional_kwargs`` — DeepSeek's
    ``reasoning_content`` arrives that way.

Call sites that test ``isinstance(content, str)`` treat the last two as failure,
so a generation that plainly succeeded (and that shows a full answer in the
LangSmith trace) is reported as "LLM returned empty or invalid content". The
streaming path already normalizes all of this through ``extract_chunk_parts``;
this module is the same normalization for the one-shot path.

:func:`describe_content` exists for the branch where the text really is empty:
without it the log says only that nothing came back, which is precisely the
question you need answered.
"""

from __future__ import annotations

from typing import Any

from app.tasks.chat.streaming.helpers.chunk_parts import extract_chunk_parts


def message_text(response: Any) -> str:
    """The assistant's text, whatever shape the response arrived in.

    Returns ``""`` when there is genuinely no text — including when the model
    produced only reasoning. Reasoning is deliberately *not* substituted: a
    model's private trace is not the artifact that was asked for, and silently
    persisting one as a report is worse than reporting the miss.
    """
    return extract_chunk_parts(response)["text"].strip()


def describe_content(response: Any) -> str:
    """One-line description of a response's shape, for a failure log.

    Turns "nothing came back" into something diagnosable: which container the
    provider used, which block types were in it, and whether the text landed in
    the reasoning channel instead.
    """
    content = getattr(response, "content", None)

    if isinstance(content, str):
        shape = f"str(len={len(content)})"
    elif isinstance(content, list):
        block_types = [
            block.get("type", "?") if isinstance(block, dict) else type(block).__name__
            for block in content
        ]
        shape = f"list(n={len(content)}, types={block_types})"
    else:
        shape = f"{type(content).__name__}"

    extra = getattr(response, "additional_kwargs", None)
    keys = sorted(extra) if isinstance(extra, dict) else []
    reasoning_chars = len(extract_chunk_parts(response)["reasoning"])
    return f"content={shape} additional_kwargs={keys} reasoning_chars={reasoning_chars}"


__all__ = ["describe_content", "message_text"]
