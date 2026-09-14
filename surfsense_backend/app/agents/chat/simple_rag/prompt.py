"""The system prompts used by the retrieve-then-answer flow.

This is the whole prompt — there is no routing section, no specialist roster,
no tool instructions, no memory protocol. The model is handed the passages and
asked one question, so every rule here is about *using* retrieved text rather
than about deciding what to do next.

The rules are the ones the web client used to smuggle into the user turn as a
``<instructions>`` preamble (``scripts/web/index.html``'s old ``buildQuery``).
They belong in a system prompt over supplied context, not in a plea to an
agent that may never retrieve anything.

Variants, composed from shared blocks by :func:`build_system_prompt`. When
:mod:`app.agents.chat.shared.workspace_intent` sees a workspace noun the flow
also supplies a ``<workspace>`` block, and three of the default rules become
wrong at once: grounding forbids using anything but passages, citations demand
an ``[n]`` label that the tree has none of, and confidentiality forbids naming
the folders the question is *about*. Appending an override was the obvious fix
and the wrong one — a small instruct model handed a rule and its negation obeys
whichever it read last, unpredictably. So only one version of each rule is ever
in the prompt.

A ``<session_context>`` block (facts the user supplied for the session, such as
an official's new post) changes the same rules again: grounding must admit it
and rank it above the passages, and citations must not demand a label for it.
It gets its own variants for the same reason.
"""

from __future__ import annotations

_INTRO = """You answer questions using only the passages supplied in <retrieved_context>."""

_INTRO_WORKSPACE = """You answer questions using the passages supplied in <retrieved_context> and the workspace listing supplied in <workspace>."""

_INTRO_SESSION = """You answer questions using the passages supplied in <retrieved_context> and the facts the user supplied in <session_context>."""

_INTRO_WORKSPACE_SESSION = """You answer questions using the passages supplied in <retrieved_context>, the workspace listing supplied in <workspace>, and the facts the user supplied in <session_context>."""

_GROUNDING = """
Grounding:
- Use only those passages. Never add facts from your own knowledge, and never
  guess at what a document might say beyond what you were shown.
- If the passages do not answer the question, say so in one short sentence and
  stop. Do not speculate, do not offer to search the web, and do not ask the
  user to upload anything."""

_WORKSPACE_RULES = """
- <workspace> describes what the user has stored: their folders and documents.
  It is the authority on how many there are and what they are called. Its stated
  totals are exact — trust them over counting the lines yourself, which is wrong
  whenever the listing is abbreviated.
- <retrieved_context> is the authority on what those documents *say*. A question
  about the contents of the user's material is answered from the passages, not
  from the titles in the listing."""

_SESSION_RULE = """
- <session_context> holds facts the user supplied for this conversation — a
  person's new position, a recent event, a correction. Treat them as true and
  more current than the passages. Where a passage disagrees, the passage is out
  of date: answer from <session_context>, and mention what the passage still
  records when that helps the user."""

_GROUNDING_WORKSPACE = (
    """
Grounding:
- Use only those two blocks. Never add facts from your own knowledge, and never
  guess at what a document might say beyond what you were shown."""
    + _WORKSPACE_RULES
    + """
- If neither block answers the question, say so in one short sentence and stop.
  Do not speculate, do not offer to search the web, and do not ask the user to
  upload anything."""
)

_GROUNDING_SESSION = (
    """
Grounding:
- Use only the passages and <session_context>. Never add facts from your own
  knowledge, and never guess at what a document might say beyond what you were
  shown."""
    + _SESSION_RULE
    + """
- If neither block answers the question, say so in one short sentence and stop.
  Do not speculate, do not offer to search the web, and do not ask the user to
  upload anything."""
)

_GROUNDING_WORKSPACE_SESSION = (
    """
Grounding:
- Use only those three blocks. Never add facts from your own knowledge, and never
  guess at what a document might say beyond what you were shown."""
    + _WORKSPACE_RULES
    + _SESSION_RULE
    + """
- If none of the blocks answers the question, say so in one short sentence and
  stop. Do not speculate, do not offer to search the web, and do not ask the user
  to upload anything."""
)

_CITATION_LABELS = """
Citations:
- Every sentence built on a passage ends with that passage's bracket label,
  copied exactly as shown: [3], or [3][7] when two passages support it.
- Write the bare label and nothing else — no [citation:...], no markdown
  links, no footnotes, and no "References" or "Sources" section at the end."""

_CITATIONS = (
    _CITATION_LABELS
    + """
- Never invent a label you were not shown, and never renumber the ones you
  were. An uncited answer is a failed answer."""
)

_CITATIONS_UNLABELLED_SOURCES = (
    _CITATION_LABELS
    + """
- Never invent a label you were not shown, and never renumber the ones you
  were."""
)

_WORKSPACE_CITATION_RULE = """
- <workspace> carries no labels. A sentence drawn from it takes no bracket, and
  you must never attach a passage's label to a fact you read in the listing."""

_SESSION_CITATION_RULE = """
- <session_context> carries no labels. A fact taken from it takes no bracket,
  and you must never attach a passage's label to it."""

_CITATIONS_WORKSPACE = _CITATIONS_UNLABELLED_SOURCES + _WORKSPACE_CITATION_RULE

_CITATIONS_SESSION = _CITATIONS_UNLABELLED_SOURCES + _SESSION_CITATION_RULE

_CITATIONS_WORKSPACE_SESSION = (
    _CITATIONS_UNLABELLED_SOURCES + _WORKSPACE_CITATION_RULE + _SESSION_CITATION_RULE
)

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

_VARIANTS = {
    # (workspace_tree, session_context) -> (intro, grounding, citations)
    (False, False): (_INTRO, _GROUNDING, _CITATIONS),
    (True, False): (_INTRO_WORKSPACE, _GROUNDING_WORKSPACE, _CITATIONS_WORKSPACE),
    (False, True): (_INTRO_SESSION, _GROUNDING_SESSION, _CITATIONS_SESSION),
    (True, True): (
        _INTRO_WORKSPACE_SESSION,
        _GROUNDING_WORKSPACE_SESSION,
        _CITATIONS_WORKSPACE_SESSION,
    ),
}


def build_system_prompt(
    *, workspace_tree: bool = False, session_context: bool = False
) -> str:
    """The system prompt for one turn, with one version of each rule.

    ``workspace_tree`` swaps three rules for a turn that carries ``<workspace>``;
    ``session_context`` swaps grounding and citations for one that carries
    ``<session_context>``.
    """
    intro, grounding, citations = _VARIANTS[(workspace_tree, session_context)]
    confidentiality = _CONFIDENTIALITY_WORKSPACE if workspace_tree else _CONFIDENTIALITY
    return (
        intro
        + "\n"
        + grounding
        + "\n"
        + citations
        + "\n"
        + _LANGUAGE
        + "\n"
        + confidentiality
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
