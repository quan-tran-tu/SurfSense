# Per-question folder scope (`SearchScope.folder_ids`)

**Status:** implemented 2026-07-30. Written, not executed — this box has no
backend deps or Postgres. The tests below exist and are unverified; run them
before trusting any of this.
**Scope:** `surfsense_backend/` only.

The need: three folders uploaded, and a question that must be answered from only
two of them — *"is there anything about X in these, and nothing else?"* An
existence question is only meaningful if the search really was confined, so
"nothing found" has to mean nothing found **there**.

## The model

`SearchScope` (`shared/retrieval/models.py`) gained `folder_ids`, and
`_base_conditions` (`shared/retrieval/hybrid_search.py`) turns it into
`Document.folder_id IN (subtree of each id)` via
`folder_service.folder_subtree_ids_subquery` — one recursive CTE, no expansion
round trip. Both answer paths already carried the pins:

- **simple_rag** (`simple_rag/flow.py`) — `mentioned_folder_ids` on
  `POST /new_chat`, resolver-vetted (`accepted_folder_ids`) before it gets here.
- **agent path** — `search_knowledge_base`'s `_build_search_scope`, reading the
  turn's pins off subagent state / runtime context.
- **reports** — `folder_ids` on `POST /reports/generate` →
  `generate_report_document(folder_ids=...)`, applied to every one of its
  KB queries.

`document_ids` and `folder_ids` **union** (both answer "which documents may
match"); everything else in the scope intersects.

## Why recursion is the whole point

A folder upload mirrors the picked directory tree — `_resolve_folder_for_file`
(`local_folder_indexer.py`) creates a `Folder` row per subdirectory. The previous
mechanism (`references/documents/referenced.py`, now deleted) expanded a folder
mention with an **exact** `folder_id IN (roots)`, so pinning an uploaded root
matched only the files sitting loose at that root — usually none — and reported
it as "I couldn't find anything about this in the knowledge base". Silent, and
indistinguishable from a genuine miss. That module is gone rather than fixed:
keeping a second, id-list-shaped way to express a folder scope invites the same
bug back, and folder counts are tens where document counts are thousands.

## Traps absorbed

- **Space equality would break linked folders.** A folder held by link belongs to
  the *sharer's* space, so `folder_subtree_ids_subquery` deliberately does not
  filter by space; readability stays with `_base_conditions`' own
  `search_space_id == … OR folder_id IN linked` predicate. Same reason
  `mention_resolver` now accepts a folder id found in `index.linked_folder_ids`
  — it used to drop it, quietly narrowing the scope the user asked for.
- **An unreachable folder id is safe but empty.** The scope predicate is ANDed
  with readability, so scoping to another space's (unlinked) folder returns
  nothing instead of leaking it. Callers that want a legible error must validate
  first; the web client only offers ids it listed.
- **A scoped report must not fall back to the model's memory.** With
  `folder_ids` set and no KB material found, `generate_report_document` fails the
  report (a persisted failed row, as usual) instead of writing an ungrounded one
  that reads exactly like a grounded one. Revisions are exempt — they still have
  the parent's content to work on.
- **Session scope is orthogonal.** Another session's folders are hidden by
  `_base_conditions` independently; scoping to one yields nothing.

## The client

`scripts/web/index.html`: a checkbox per row in **Folders** and **Imported**,
`state.scopeFolderIds` (per-user prefs, sticky across reloads), a chip above the
composer whenever a scope is on, and `/scope [ids|all]` as the keyboard path.
Sticky-by-default is deliberate — one investigation asks many questions of the
same subset — which is exactly why the chip exists: a scope you forgot about
shrinks every answer. Two states the client refuses to let you walk into
silently: a folder owned by *another* session can't be ticked (retrieval hides it
here regardless, so it could only scope to nothing), and if the open session
changes such that an in-scope folder becomes unreachable, the chip says so rather
than the id being dropped behind your back.

## Tests

- `tests/integration/agents/multi_agent_chat/shared/retrieval/test_hybrid_search.py`
  — nested-subfolder reach, unpicked folders excluded, folderless documents
  excluded, union with `document_ids`, cross-space folder id yields nothing.
- `tests/integration/folder_sharing/test_shared_folder_write_protection.py`
  — scoping to an imported folder reaches the sharer's document.

## Deliberately not done

- **Folder pins on the agent's own `generate_report` tool.** The REST path is
  what the OSINT client uses; wiring the tool means reading mention pins off a
  second runtime and buys nothing today.
- **Per-turn scope** (`@folder` chips that reset each question). The sticky set
  plus the chip covers the known need; the upstream web app's mention seam
  already exists for the other style.
- **Scoping the workspace tree / `ls` / `grep`.** Scope narrows *retrieval*; the
  agent's filesystem view still spans the space (session scope is what gates
  that).
