"""Decide whether a question is about the workspace itself, not its contents.

"How many folders do I have" and "how many casualties were reported" are the
same question shape, and no amount of reading the *form* separates them. What
separates them is the **object**: one names a thing the workspace is made of, the
other names a thing the documents talk about. So this gate matches a small closed
vocabulary of workspace nouns and ignores interrogatives entirely — "bao nhiêu"
and "how many" appear nowhere below on purpose.

Deliberately biased toward firing. The two errors are not symmetric:

  * **False positive** — a content question also containing "tài liệu" pays for
    a workspace tree it does not need. Costs tokens, changes no answer.
  * **False negative** — a workspace question arrives with no tree, and the
    model answers "how many folders do I have" from nothing at all. That is a
    confident wrong number, which is the failure this gate exists to prevent.

So when the two readings compete, fire. A wasted tree is the cheap mistake.

Deliberately not a model call, for the same reason as
:mod:`app.agents.chat.shared.search_query`: the flow that consumes this exists
*because* the model is too small to be trusted with a routing decision, so
routing it through that model would reintroduce what the flow removed.
"""

from __future__ import annotations

import re

# Nouns naming the workspace's own furniture. Multi-syllable Vietnamese phrases
# are listed whole: Vietnamese is written syllable-per-token, so "thư" and "mục"
# on their own carry no signal and would fire on unrelated prose.
_WORKSPACE_NOUNS = (
    # Vietnamese
    "thư mục",
    "tài liệu",
    "tệp",
    "tập tin",
    "kho tri thức",
    "cơ sở tri thức",
    "kho dữ liệu",
    "tải lên",
    "không gian làm việc",
    # English
    "folder",
    "folders",
    "directory",
    "directories",
    "document",
    "documents",
    "doc",
    "docs",
    "file",
    "files",
    "upload",
    "uploads",
    "uploaded",
    "workspace",
    "knowledge base",
    "search space",
)

# Word boundaries keep "file" from firing on "profile" and "doc" on "docket".
# Vietnamese diacritics are word characters under re.UNICODE, so ``\b`` brackets
# the multi-syllable phrases correctly too.
_WORKSPACE_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(noun) for noun in _WORKSPACE_NOUNS) + r")\b",
    re.IGNORECASE | re.UNICODE,
)


def mentions_workspace(question: str) -> bool:
    """True when ``question`` names the workspace itself and wants its tree."""
    if not question or not question.strip():
        return False
    return _WORKSPACE_RE.search(question) is not None


__all__ = ["mentions_workspace"]
