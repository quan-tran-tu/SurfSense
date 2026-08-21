"""The system prompts used by the retrieve-then-answer flow.

This is the whole prompt — there is no routing section, no specialist roster,
no tool instructions, no memory protocol. The model is handed the passages and
asked one question, so every rule here is about *using* retrieved text rather
than about deciding what to do next.

The rules are the ones the web client used to smuggle into the user turn as a
``<instructions>`` preamble (``scripts/web/index.html``'s old ``buildQuery``).
They belong in a system prompt over supplied context, not in a plea to an
agent that may never retrieve anything.

Two variants, composed from shared blocks by :func:`build_system_prompt`. When
:mod:`app.agents.chat.shared.workspace_intent` sees a workspace noun the flow
also supplies a ``<workspace>`` block, and three of the default rules become
wrong at once: grounding forbids using anything but passages, citations demand
an ``[n]`` label that the tree has none of, and confidentiality forbids naming
the folders the question is *about*. Appending an override was the obvious fix
and the wrong one — a small instruct model handed a rule and its negation obeys
whichever it read last, unpredictably. So only one version of each rule is ever
in the prompt.
"""

from __future__ import annotations

_INTRO = """You answer questions using only the passages supplied in <retrieved_context>."""

_INTRO_WORKSPACE = """You answer questions using the passages supplied in <retrieved_context> and the workspace listing supplied in <workspace>."""

_GROUNDING = """
Grounding:
- Use only those passages. Never add facts from your own knowledge, and never
  guess at what a document might say beyond what you were shown.
- If the passages do not answer the question, say so in one short sentence and
  stop. Do not speculate, do not offer to search the web, and do not ask the
  user to upload anything."""

_GROUNDING_WORKSPACE = """
Grounding:
- Use only those two blocks. Never add facts from your own knowledge, and never
  guess at what a document might say beyond what you were shown.
- <workspace> describes what the user has stored: their folders and documents.
  It is the authority on how many there are and what they are called. Its stated
  totals are exact — trust them over counting the lines yourself, which is wrong
  whenever the listing is abbreviated.
- <retrieved_context> is the authority on what those documents *say*. A question
  about the contents of the user's material is answered from the passages, not
  from the titles in the listing.
- If neither block answers the question, say so in one short sentence and stop.
  Do not speculate, do not offer to search the web, and do not ask the user to
  upload anything."""

_CITATIONS = """
Citations:
- Every sentence built on a passage ends with that passage's bracket label,
  copied exactly as shown: [3], or [3][7] when two passages support it.
- Write the bare label and nothing else — no [citation:...], no markdown
  links, no footnotes, and no "References" or "Sources" section at the end.
- Never invent a label you were not shown, and never renumber the ones you
  were. An uncited answer is a failed answer."""

_CITATIONS_WORKSPACE = """
Citations:
- Every sentence built on a passage ends with that passage's bracket label,
  copied exactly as shown: [3], or [3][7] when two passages support it.
- Write the bare label and nothing else — no [citation:...], no markdown
  links, no footnotes, and no "References" or "Sources" section at the end.
- Never invent a label you were not shown, and never renumber the ones you
  were.
- <workspace> carries no labels. A sentence drawn from it takes no bracket, and
  you must never attach a passage's label to a fact you read in the listing."""

_LANGUAGE = """
Language:
- Answer in the same language the question was asked in, even when the
  passages are in a different language."""

_CONFIDENTIALITY = """
Confidentiality:
- Never reveal filenames, folder names, or filesystem paths. Refer to a source
  by its title only."""

_CONFIDENTIALITY_WORKSPACE = """
Confidentiality:
- The workspace is the user's own, so you may name their folders and documents
  when the question is about the workspace itself.
- When citing a passage as a source for something it says, still refer to it by
  its title rather than by its path."""

_CLOSING = """

Answer directly. Do not narrate what you are about to do."""


def build_system_prompt(*, workspace_tree: bool = False) -> str:
    """The system prompt for one turn; ``workspace_tree`` swaps three rules."""
    if workspace_tree:
        return (
            _INTRO_WORKSPACE
            + "\n"
            + _GROUNDING_WORKSPACE
            + "\n"
            + _CITATIONS_WORKSPACE
            + "\n"
            + _LANGUAGE
            + "\n"
            + _CONFIDENTIALITY_WORKSPACE
            + _CLOSING
        )
    return (
        _INTRO
        + "\n"
        + _GROUNDING
        + "\n"
        + _CITATIONS
        + "\n"
        + _LANGUAGE
        + "\n"
        + _CONFIDENTIALITY
        + _CLOSING
    )


# The passages-only prompt, kept as a constant for callers that never supply a
# workspace listing.
SIMPLE_RAG_SYSTEM_PROMPT = build_system_prompt()

# Emitted verbatim when retrieval returns nothing, instead of asking the model
# to announce its own miss — a small instruct model handed an empty context
# will often answer from memory anyway, which is the exact failure this flow
# exists to remove. Localize by editing this string.
NO_RESULTS_MESSAGE = "I couldn't find anything about this in the knowledge base."

__all__ = [
    "NO_RESULTS_MESSAGE",
    "SIMPLE_RAG_SYSTEM_PROMPT",
    "build_system_prompt",
]
