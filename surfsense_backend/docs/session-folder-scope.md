# Per-session folder scope (`folders.owner_thread_id`)

**Status:** implemented 2026-07-21 (migration 170). Written, not yet executed —
the dev box has no backend deps or Postgres. `tests/integration/folder_scope/`
exists and is unverified; run it (and migration 170) before trusting any of this.
**Scope:** `surfsense_backend/` only.

## The model

One nullable column, `folders.owner_thread_id` (FK `new_chat_threads.id`,
`ON DELETE SET NULL`):

- `NULL` → **space-wide** ("general knowledge") — the pre-existing behavior,
  and what every existing row stays after the migration.
- a thread id → the folder subtree is visible **only inside that chat session**:
  retrieval, the workspace tree, `ls`/`read`/`glob`/`grep`, and `@`-mentions all
  exclude it elsewhere.

**Uploads default to session-scoped** when the client sends `thread_id` with
`POST /documents/folder-upload` (the web client always does, using the open
session). **Promotion** (`PATCH /folders/{id}/scope`, body `{"scope":"space"}`,
`DOCUMENTS_UPDATE`; admin: `PATCH /admin/folders/{id}/scope`) clears the stamp
on the whole subtree — a live change of visibility, nothing copied or
re-embedded, same philosophy as folder sharing's live link. There is no
demotion. `ON DELETE SET NULL` means deleting a chat **auto-promotes** its
folders rather than destroying uploaded documents.

Invariant: **children carry their subtree root's stamp.** Every creation site
maintains it (upload mirroring, `_resolve_folder_for_file`, kb_persistence's
`_ensure_folder_hierarchy` inherits from the parent) and the folder move route
restamps the moved subtree to its new parent's scope. The read side does not
*trust* it: `_build_folder_paths` hides by ancestor closure, so a mis-stamped
child still fails closed.

Agent-written folders at the root (`mkdir /documents/Reports`) are deliberately
**space-wide** — only *uploads* are session-scoped by default; the agent's
output areas stay visible across sessions. Writes *inside* a session folder
inherit the session.

## Where the enforcement lives

Everything funnels through two chokepoints plus one predicate:

1. **`build_path_index(session, space_id, thread_id=...)`**
   (`app/agents/chat/runtime/path_resolver.py`). `PathIndex` gained
   `hidden_folder_ids`; hidden folders get no path and their documents are
   dropped from the occupancy seed. `readable_documents_filter(index, space)`
   excludes their documents. Callers: `kb_postgres` (ls/read/glob/grep/tree),
   `knowledge_tree` (cache key now includes the thread), `mention_resolver`.
2. **`virtual_path_to_doc(..., thread_id=...)`** — tries the session-scoped
   NOTE hash first, resolves folder chains thread-visibly (own-session folder
   shadows a same-named space-wide one), and re-verifies id-suffix hits. This
   is what makes the commit path's `rm`/`rmdir` fail closed: another session's
   paths simply do not resolve.
3. **Retrieval** — `_base_conditions` (`shared/retrieval/hybrid_search.py`)
   adds `folder_id IS NULL OR folder_id NOT IN (hidden-folders subquery)`.

The thread id is **resolved at call time** via `current_thread_id()`
(`path_resolver.py`) from `configurable.thread_id` — never captured at
construction, because compiled graphs are cached per space and serve many
threads. Two traps that function absorbs:

- Subagent runs carry an *extended* id (`"{parent}::task:{tool_call_id}"`,
  see `subagents/shared/invocation.py`) — and the knowledge_base subagent is
  where retrieval actually happens. The parent id is parsed off the front; a
  bare `int()` cast would silently un-scope every search.
- `None` (no graph context) means **unscoped**: REST document lists, exports,
  admin, and citation click-through keep the historical wide view. Session
  scoping within one user's space is organization, not a security boundary —
  the space stays the security boundary.

## The identity-hash problem (the sharp edge)

`documents.unique_identifier_hash` is **globally unique**, and two sessions can
each upload a root named `Research` containing `notes.pdf` — same path, same
space, so the historical hash collides and the second upload would silently
update the first session's rows. Therefore the owning thread is mixed into the
identity of session-scoped documents:

- `LOCAL_FOLDER_FILE`: `local_file_unique_id(folder, rel_path, owner)` →
  `"thread:{owner}:{folder}:{rel}"` (`indexing_pipeline/document_hashing.py`).
  Used by the upload indexer, `folder-unlink`, and `folder-sync-finalize` — all
  derive the owner from the **root folder row**, not from request state.
- `NOTE` (agent writes): `note_path_identifier(path, owner)` →
  `"thread:{owner}:{path}"` (`path_resolver.py`). Used by kb_persistence
  create/update/move and by `revert_service` (both restore and re-insert).

**Promotion must rewrite these hashes** to the space-wide form —
`folder_scope_service.promote_folder_to_space` does, pre-checking collisions
and returning 409 (with the conflicting titles) rather than clobbering either
side. Any future site that mints one of these hashes must go through the two
helpers, or promote/re-upload will duplicate documents.

The unique index `uq_folder_space_parent_name` widened to
`(search_space_id, COALESCE(parent_id,0), name, COALESCE(owner_thread_id,0))` —
without that, two sessions could never own same-named roots at all. Promotion of
a root whose name is already taken space-wide is pre-checked → 409.

## Interaction with folder sharing

Orthogonal by construction: linked folders belong to *another space*, the hidden
set only ever names own-space folders. One deliberate coupling: `/share` on a
session-scoped folder is refused (400 — promote first). Session scope is
narrower than the space; letting it cross a user boundary before deliberate
promotion would widen it by accident.

## Deliberately not done

- **Session-to-session sharing** of a folder (reusing `SharedFolder` within one
  space). The promote-then-visible flow covers the known need.
- **Demotion** (space-wide → session). Ambiguous target thread, and citations
  already minted in other sessions would dangle.
- **Scoping connector-indexed documents.** Connectors are space-level; the
  desktop watcher paths (`index_local_folder`, `_index_single_file`) still
  create space-wide folders and unprefixed hashes.
- **Document-list UI filtering.** `/documents` and `/folders` REST list
  everything in the space (the client shows badges instead). Session scope
  gates the *agent's* view, not the owner's management view.
