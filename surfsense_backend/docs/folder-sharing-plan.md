# Cross-user folder sharing (`/share` and `/import`)

**Status:** WS1, WS2, WS3, WS5 implemented; WS4 implemented as the interim option only.
**Scope:** `surfsense_backend/` only.
**Date:** 2026-07-09 (implemented 2026-07-13)

## Implementation notes (2026-07-13)

Built, but **not yet executed** — the dev box has no backend deps, no Postgres and
no Docker, so nothing here has been run. `tests/integration/folder_sharing/` is
written and unverified. Run it before trusting any of this.

Two things the plan got wrong, worth knowing:

- **Paths.** `retrieval/`, `middleware/filesystem/` etc. are really under
  `app/agents/chat/multi_agent_chat/{shared,main_agent}/`. Line numbers were
  right; prefixes were not.
- **`path_resolver` has ~7 space filters, not 2**, and `build_path_index` is the
  chokepoint feeding `ls`/`glob`/`grep`/the workspace tree/`@`-mentions. Widening
  it there covered WS2 item 2 and 3 in one place — but it also meant
  `knowledge_tree/middleware.py` (which the plan never mentions) had to be widened
  too, or linked folders would have rendered as visibly *empty* directories in the
  agent's system prompt.

The WS3 hazard was real but differently shaped than described. `rmdir` could not
in fact have cascaded, because the commit path resolves folders via
`_resolve_folder_id`, which is space-scoped and returns `None` for a linked path.
The live hazard was **`rm`**: the commit path resolves documents via
`virtual_path_to_doc`, and WS2 *had* to teach that function to resolve `_shared`
paths for `read` to work — which armed a delete of the sharer's row. The guard is
therefore in three layers: the tools refuse the path (UX), the commit drops the
staged op before any mutation loop (authoritative), and each resolution site
asserts `document.search_space_id == search_space_id` (fail-closed backstop).

WS4 shipped as the **interim option** only. The wire format is untouched; instead
`GET /documents/by-chunk/{id}` falls back to `user_can_read_via_link` when the
membership check on the sharer's space rejects the caller. This was not optional:
WS2 makes linked documents retrievable and citable, so without it every citation
to a linked document 403s on click. The ordinal-payload redesign, the merge-reducer
re-minting hypothesis, and the `thread_id`-scoping problem are all still open and
still unexamined.

## Goal

A user runs `/share <full_folder_path>` and receives an opaque string. A second
user runs `/import <string>` and gains read access to **that folder subtree and
nothing else** — the folder's documents become searchable and browsable inside
the importer's own search space.

Two decisions fix the design:

- **Same backend only.** Both users share one database. The share string is a
  token, not a payload. Cross-deployment sharing is explicitly out of scope.
- **Live link, not snapshot copy.** No documents are duplicated. The importer
  reads through to the sharer's rows. The sharer's later edits are visible; a
  revoked share disappears from the importer's retrieval on the next query.

Live link was chosen over copying because revocation is then real rather than
cosmetic, and because `Document.folder_id` already gives us grant granularity
at exactly the unit the feature promises.

## What already exists

Most of the round trip is built.

| Piece | Location | Reusable as-is? |
|---|---|---|
| Folder tree, per search space | `app/db.py:1310` | yes |
| `Document.folder_id` (nullable, `SET NULL`) | `app/db.py:1389` | yes |
| Folder subtree walk | `folder_service.get_folder_subtree_ids` | yes |
| Folder subtree export | `services/export_service.py:82` | reference only |
| Token table precedent | `SearchSpaceInvite`, `app/db.py:2113` | copy its shape |
| Permission check | `utils/rbac.check_permission` | yes |
| Citation `[n]` minting | `citations/registry.py:31` | yes, already ordinal |

Notably **not** existing: a read-only folder **path → id** resolver. Folders are
addressed by integer id everywhere. `ensure_folder_hierarchy_with_depth_validation`
walks name segments but *creates* them as it goes, so it cannot be reused for
`/share`. Model the new resolver on `_build_folder_path_map`
(`services/export_service.py:24`), which already builds `folder_id → "A/B/C"`.

## Naming

Do **not** call the new concept a "mount". That word is taken:
`MultiRootLocalFolderBackend.list_mounts()` and `extract_mount_from_path`
(`middleware/filesystem/shared/paths.py`) use it for desktop local-folder roots.

Use **shared folder link**: `SharedFolder` (the grant) and `FolderLink` (the
acceptance).

## Schema

Both tables mirror `SearchSpaceInvite`.

```
SharedFolder
  token                 String(64), unique, indexed   # what /share returns
  source_folder_id      FK folders.id      ON DELETE CASCADE
  source_search_space_id FK searchspaces.id ON DELETE CASCADE
  created_by_id         FK user.id         ON DELETE SET NULL
  expires_at            timestamptz, nullable
  max_uses              int, nullable
  revoked_at            timestamptz, nullable

FolderLink
  share_id              FK shared_folders.id ON DELETE CASCADE
  source_folder_id      FK folders.id        ON DELETE CASCADE
  target_search_space_id FK searchspaces.id  ON DELETE CASCADE
  created_by_id         FK user.id           ON DELETE SET NULL
  UNIQUE (target_search_space_id, source_folder_id)
```

`FolderLink` denormalizes `source_folder_id` off the share so the retrieval
predicate never has to join through `SharedFolder` on the hot path — but the
liveness check (revoked / expired / over-used) *must* still consult it. Decide
deliberately whether liveness is enforced per-query (correct, costs a join) or
by a background sweep that deletes dead links (fast, revocation lags). **Prefer
per-query.** A stale grant is a security bug, not a performance one.

### The linked-folder subtree CTE

One shared helper, used by every read surface:

```sql
WITH RECURSIVE linked AS (
    SELECT f.id
    FROM folder_links fl
    JOIN shared_folders sf ON sf.id = fl.share_id
    JOIN folders f ON f.id = fl.source_folder_id
    WHERE fl.target_search_space_id = :sid
      AND sf.revoked_at IS NULL
      AND (sf.expires_at IS NULL OR sf.expires_at > now())
    UNION ALL
    SELECT c.id FROM folders c JOIN linked l ON c.parent_id = l.id
)
SELECT id FROM linked;
```

## Workstreams, in dependency order

### WS1 — Schema and token endpoints

Alembic migration for both tables. Then:

- `POST /search-spaces/{id}/folder-shares` — body `{"path": "Research/AI"}`.
  Resolve path → folder id (new resolver), `check_permission(DOCUMENTS_READ)`
  on the source space, mint token. Returns the share string.
- `DELETE /folder-shares/{token}` — set `revoked_at`. Creator or space owner only.
- `POST /search-spaces/{id}/folder-links` — body `{"token": "..."}`. Validate
  liveness, `check_permission(DOCUMENTS_CREATE)` on the *target* space, insert
  `FolderLink`, increment use count.

Reject a link whose `source_search_space_id == target_search_space_id` (a space
linking its own folder is a no-op that would double-count rows in the `or_`).

### WS2 — Read surfaces

Three places gate KB reads by space. All three need the `or_(own, linked)` form.

1. **Retrieval** — `_base_conditions`, `retrieval/hybrid_search.py:125`. One
   predicate. This is the whole retrieval change; the chat API keeps
   `search_space_id: int` and nothing upstream moves.

   ```python
   or_(
       Document.search_space_id == search_space_id,
       Document.folder_id.in_(linked_folder_subtree_ids(search_space_id)),
   )
   ```

2. **Agent filesystem** — `middleware/filesystem/backends/kb_postgres.py`, four
   `Document.search_space_id == self.search_space_id` filters (lines 421, 673,
   754, 859) backing `ls`, `read`, `glob`, `grep`.

3. **Path resolution** — `agents/chat/runtime/path_resolver.py:106` builds the
   folder tree; `:153` populates occupants. Both filter by space.

Skipping (2) and (3) leaves a linked folder *searchable but invisible to `ls`* —
a state the agent handles badly. Do all three together.

Surface linked folders under a reserved prefix (`/documents/_shared/<name>`) so
they are visibly distinct from owned content and so the write guard has a cheap
path-prefix test in addition to the id check.

### WS3 — Write protection (fail closed)

**The highest-risk workstream.** The agent's write tools are `write_file`,
`edit_file`, `move_file`, `rm`, `rmdir`, `mkdir`, and `execute_code`
(`middleware/filesystem/tools/`). Once a linked folder is reachable through
`path_resolver`, the importer's agent can address it.

The hazard is concrete: `Folder.parent_id` is `ON DELETE CASCADE` and
`Document.folder_id` is `ON DELETE SET NULL`. An `rmdir` on a linked folder
would delete the **sharer's** folder rows and silently orphan the **sharer's**
documents from their own tree. The importer's own space would look unchanged.

Enforcement does **not** belong in `path_resolution.py` — that module resolves
paths, it does not authorize them. `kb_postgres` stages mutations into graph
state (`_pending_moves`, `_pending_deletes`, `_pending_dir_deletes`) and the
actual DB write happens in `main_agent/middleware/kb_persistence/middleware.py`.

Guard in **both** places:
- `kb_postgres.awrite` / `aedit` and the staging paths reject a target whose
  resolved folder is in the linked set, so the agent gets an immediate,
  legible error rather than a silently dropped write.
- `kb_persistence` re-checks before committing, and refuses. This is the
  authoritative check; the first is UX.

Write a test that asserts `rmdir /documents/_shared/X` leaves the sharer's
`folders` and `documents` rows untouched. This is the one test that must exist
before the feature ships.

### WS4 — Citation resolution

A linked document *will* be retrieved and cited, and today that citation breaks.

**Current state, verified:**

- The model-facing `[n]` is already a per-conversation ordinal: `next_n` starts
  at 1 and `register()` mints monotonically, deduped by locator
  (`citations/registry.py:31,45-53`).
- But `finalize_assistant_message` → `normalize_citations` rewrites `[n]` into
  `[citation:<payload>]`, and `to_frontend_payload` returns the **raw global
  `Chunk.id`** (`citations/markers.py:19-21`). The ordinal is discarded exactly
  when it stops being ephemeral.
- The frontend resolves that payload via `GET /documents/by-chunk/{chunk_id}`
  (`routes/documents_routes.py:966`), which looks the chunk up globally, loads
  its document, then calls
  `check_permission(document.search_space_id, DOCUMENTS_READ)` (`:1001`).

For a linked document, `document.search_space_id` is the **sharer's** space,
where the importer has no membership. **The citation 403s on click.** It fails
closed, not open — but it fails.

The payload space is also already overloaded: positive int = KB chunk, negative
int = anonymous-upload chunk, string = URL. Connector items and chat turns
return `None` and are silently dropped.

**Target state:** the wire payload becomes the per-conversation ordinal, and the
`CitationRegistry` — already checkpointed on graph state behind a merge reducer —
becomes the resolution table. Linked chunk ids never leave the backend. The
permission check then runs against the *link*, not the owning space. This also
retires the negative-id hack and gives connector items a renderable form.

Three complications, all real:

1. **Ordinals are thread-scoped.** `next_n` is per conversation, so the resolver
   key is `(thread_id, n)`. The marker stops being self-describing. Anything
   reading a message outside its thread loses resolution.

2. **The merge reducer can re-mint an ordinal.** `CitationRegistry.merge`
   re-mints a colliding entry to a fresh `n` (`registry.py:82-83`) when parallel
   branches registered different sources at the same slot. Normalization runs
   once, at the end, against the *final merged* registry
   (`assistant_finalize.py:60-65`) — but the model already wrote `[3]` into its
   text mid-turn using its branch's fork. If the merge moved that source to
   `[7]`, the text says `[3]` and `[3]` now belongs to a different source.

   **This is an unconfirmed hypothesis, not a verified bug.** No failing case has
   been constructed. It matters because moving to ordinals makes the registry the
   *only* source of truth, converting a latent misattribution into a guaranteed
   one. Construct the failing case before building WS4 on this foundation.

3. **Backward compatibility.** Every persisted assistant message already contains
   `[citation:<chunk_id>]`. Changing what a bare integer means breaks all chat
   history. Use a discriminated payload — `[citation:n:3]` for the new form,
   bare `[citation:8412]` still resolving the old way — rather than a silent
   reinterpretation.

   This is a **frontend-visible wire change** and therefore escapes the
   backend-only scope of this work. It needs an explicit decision.

**Interim option:** if WS4 is deferred, relax `get_document_by_chunk_id` to also
accept a caller whose space holds a live `FolderLink` covering the chunk's
document's folder. That unblocks citations for linked documents without touching
the wire format. It leaves the id-overloading and thread-scoping problems in
place, but it is small, safe, and does not depend on resolving (2).

### WS5 — `ask.sh`

Two thin slash commands, no client-side retrieval logic:

- `/share <path>` → `POST .../folder-shares`, print the token.
- `/import <token>` → `POST .../folder-links`, print the linked folder name.

This is the whole point of doing the work in the backend: the client stays dumb.

## Deliberately not done

- **Copying documents.** Rejected in favour of the live link. Recorded here
  because the trap is non-obvious if anyone revisits it: `generate_unique_identifier_hash`
  and `generate_content_hash` both mix `search_space_id` into the digest, and
  `uq_documents_unique_identifier_hash` (migration 29) is a **global,
  single-column** unique constraint — despite the comment at `db.py:1360`
  claiming per-space uniqueness. A verbatim row copy raises on the second import.
  Any copy must recompute both hashes against the target space.

- **Widening `search_space_id` to `list[int]`** through `NewChatRequest` → agent
  factory → `search_chunks` → `_base_conditions`. Unnecessary. The folder-link
  predicate achieves live linking with one changed line in retrieval.

- **Cross-deployment sharing.** Would require a portable payload and full
  re-embedding, since `Vector(config.embedding_model_instance.dimension)` is a
  single global config — safe to assume one embedding model *within* a
  deployment, never *across* two.

## Open questions

1. Should linked documents appear in the document-list UI
   (`documents_routes.py`, 8 space-filtered queries), or only in retrieval and
   the agent filesystem? Listing them invites the user to try to edit them.
   **Still open — left alone.** The 8 list queries are untouched, so linked
   documents are searchable and visible to the agent but do *not* appear in the
   document-list UI. That is a defensible default, not a decision.
2. Do `@`-mention pins need to resolve into linked folders?
   **Answered incidentally: yes, they now do.** `mention_resolver` and
   `references/` both go through `build_path_index`, which was widened, so
   linked folders became `@`-mentionable without anyone choosing that. Worth a
   look — `_build_search_scope` accepting linked document ids is probably right,
   but it was not a deliberate call.
3. Is `citation_registry` durably queryable by `thread_id` after the turn ends?
   **Not investigated.** The interim WS4 fix does not depend on it. Any real
   ordinal-payload work still has to answer this first.
4. Per-query liveness join vs. background sweep for revocation.
   **Settled: per-query.** `linked_folder_ids_subquery` checks `revoked_at` and
   `expires_at` inline, so revocation lands on the next query. `max_uses` is
   deliberately *not* checked at read time — it caps how many spaces may accept a
   share, and must not retroactively sever links already granted (the
   `SearchSpaceInvite` precedent: a used-up invite does not evict members).
