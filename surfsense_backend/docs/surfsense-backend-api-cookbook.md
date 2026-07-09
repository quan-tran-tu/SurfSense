# SurfSense Backend API Cookbook

A practical, CLI-first guide to driving a self-hosted SurfSense backend directly over
HTTP — login, upload a document, wire up an LLM, and ask a question that returns a
cited answer — without touching the web frontend.

Everything here was derived by probing a live instance's OpenAPI (`/openapi.json`), so
the exact field names match a real build. Your commit may differ slightly; when in
doubt, read the schema from your own instance (see **§1**).

---

## 0. Setup & conventions

Every command below relies on **two shell variables**. Set both at the start of your
session:

```bash
export SS=http://localhost:8000       # your backend base URL
export ORIGIN=http://localhost:3000   # must match backend's trusted frontend origin
```

Two things are true of this build and drive almost every command:

1. **Auth is cookie-based, not bearer.** Login sets an `HttpOnly` session cookie; you
   carry it in a cookie jar, not an `Authorization: Bearer` header.
2. **Authenticated writes require a CSRF `Origin` header** matching the trusted frontend
   origin. GETs don't; POST/PUT/PATCH/DELETE do.

> ⚠️ **These variables do not survive a new shell.** If you open a new terminal (or the
> variable is otherwise unset), `-H "Origin: $ORIGIN"` becomes an empty `Origin:` header
> and every write fails with **`{"detail":"CSRF origin check failed"}`** — a blank origin
> is the #1 cause of that error, not a wrong value. Likewise an empty `$SS` gives a
> connection error. Guard against it by dropping this at the top of any script — it sets
> each only if unset:
>
> ```bash
> : "${SS:=http://localhost:8000}" "${ORIGIN:=http://localhost:3000}"
> ```
>
> Sanity-check anytime with: `echo "SS=$SS  ORIGIN=$ORIGIN"`

> **Make them permanent** so new terminals stop losing them (the root cause of most
> "nothing shows up" / "not found" / "login failed" noise):
>
> ```bash
> echo 'export SS=http://localhost:8000 ORIGIN=http://localhost:3000' >> ~/.bashrc
> source ~/.bashrc
> ```

> ⚠️ **Inline `VAR=val cmd` does not help when the same line *uses* `$VAR`.**
> `SS=http://localhost:8000 curl "$SS/api/v1/threads/4"` fails with
> `URL rejected: No host part` — the shell expands `$SS` (still empty) *before* running
> the command, so curl gets `/api/v1/threads/4` with no host. Either use a literal URL,
> or `export SS=...` as its **own** statement first, then the curl.

> Find the trusted origin if `localhost:3000` is rejected:
> `grep -Ei 'FRONTEND|ORIGIN|CORS' surfsense_backend/.env`
> Match scheme + host + port exactly, no trailing slash.

---

## 1. Read your own API (do this first)

Endpoint paths and request bodies vary between SurfSense versions. Rather than trust any
guide (including this one), dump the truth from your instance:

```bash
# save the full spec once
curl -s "$SS/openapi.json" > /tmp/oa.json

# list all paths
jq -r '.paths | keys[]' /tmp/oa.json

# filter to what you need
jq -r '.paths | keys[]' /tmp/oa.json | grep -Ei 'document|chat|thread|model|auth'

# resolve a request body schema (follow the $ref it prints)
jq -r '.paths["/api/v1/new_chat"].post.requestBody.content["application/json"].schema["$ref"]' /tmp/oa.json
jq '.components.schemas["NewChatRequest"]' /tmp/oa.json
```

**Tip:** templated paths must be looked up with the literal `{placeholder}`, not a real
id. `jq '.paths["/api/v1/search-spaces/1/model-roles"]'` returns `null`; use
`{search_space_id}` instead, or search:

```bash
jq '.paths | to_entries[] | select(.key | test("model-roles")) | {key, methods:(.value|keys)}' /tmp/oa.json
```

---

## 2. Log in (cookie jar)

Standard fastapi-users password flow: `POST /auth/jwt/login`, form-encoded.

```bash
curl -s -c cookies.txt -X POST "$SS/auth/jwt/login" \
  --data-urlencode "username=you@example.com" \
  --data-urlencode "password=yourpassword"
```

- `-c cookies.txt` **writes** the session cookie on success.
- `-b cookies.txt` **sends** it on every later call.
- The body is `{"authenticated":true,...}` — there is **no `access_token` field**, so
  `jq -r .access_token` returns `null`. That's expected; the JWT is in the
  `surfsense_session` cookie.
- The session cookie is short-lived (**~1 hour**). Re-run login when calls start
  returning 401.

Verify the session works:

```bash
curl -s -b cookies.txt "$SS/users/me"
```

> If login 400s with `LOGIN_BAD_CREDENTIALS`, the account may not exist — register with
> `POST /auth/register` (`Content-Type: application/json`, body `{"email","password"}`).

---

## 3. Find a search space

Everything (documents, chats, models) is scoped to a **search space**.

```bash
curl -s -b cookies.txt "$SS/api/v1/searchspaces?limit=10&skip=0&owned_only=false"
```

Grab an `id` (referred to below as `search_space_id`, typically `1` on a fresh install).

---

## 4. Upload a document

`fileupload` is multipart. Fields: `files` (the file) and `search_space_id`.
**This is a write → needs the `Origin` header.**

```bash
echo "Project Zephyr launched on 3 March 2026 with a budget of 4.2 million USD, led by Quentin." > /tmp/testdoc.txt

curl -s -b cookies.txt -X POST "$SS/api/v1/documents/fileupload" \
  -H "Origin: $ORIGIN" \
  -F "files=@/tmp/testdoc.txt" \
  -F "search_space_id=1"
```

> 422? The response names the expected fields. Dump the schema:
> `jq '.paths["/api/v1/documents/fileupload"].post.requestBody.content' /tmp/oa.json`

### Wait for indexing

Upload just **enqueues** a Celery job (ETL → chunk → embed). The doc is not queryable
until it reaches `ready`. Poll:

```bash
curl -s -b cookies.txt "$SS/api/v1/documents?search_space_id=1"
```

Look for `"status":{"state":"ready"}`. If it never leaves `processing`, your **Celery
worker isn't running** — start it and re-check. Nothing downstream works until the doc
is indexed.

### Deduplication & the same-name overwrite guard

Dedup is keyed by **search space, not by user**. Both identity hashes bake
`search_space_id` into their input, and neither includes the uploader:

- `content_hash = sha256("{search_space_id}:{content}")`
- for a plain file upload, `unique_identifier_hash = sha256("FILE:{filename}:{search_space_id}")`
  — i.e. **a plain upload's identity is its filename within the space** (connectors instead
  use a stable source id: Google Drive `file_id`, Slack message id, Notion page id, …).

Consequences:

- **Same document, same space (any two users):** identical content → identical
  `content_hash` → the second upload is **deduped** onto the existing row; **embedded once**.
- **Same document, different spaces:** the space id differs → hashes differ → **embedded
  twice**, one independent copy per space (spaces are the isolation boundary).
- **Same *filename*, different content, same space:** the upload matches the existing doc by
  `unique_identifier_hash`. This is the dangerous case — historically it **silently
  overwrote** the existing document's content, chunks, and embedding in place (same doc id,
  original text destroyed). A blank re-upload named `report.pdf` would clobber someone
  else's `report.pdf`.

> **Guard (this build).** For **plain file uploads only**, a same-name / different-content
> match now **refuses to overwrite by default** and fails that upload with a name conflict,
> leaving the existing document intact. To intentionally replace it, re-run with overwrite
> confirmed (`allow_overwrite=True`, wired from a "replace existing file?" confirmation).
> **Connector re-syncs are unaffected** — their stable source-id identity means a content
> change is a legitimate edit and still updates in place. Server-side this lives in
> `handle_existing_document_update` (`app/tasks/document_processors/_helpers.py`), which
> raises `SameNameOverwriteError`; the file task (`file_processors.py`) catches it and logs
> a `same_name_conflict` task failure.

**To actually share a document with another user, don't re-upload it** — add them to the
search space (membership is the real, server-enforced access boundary). Re-uploading a
same-named file is a replace, not a share.

---

## 5. Wire up an LLM (the part that bites)

Chat models are **not** read from `.env` in this build — they're stored in the DB as
*model connections*, then mapped to *roles*. Three steps: create connection → confirm
model row → assign the chat role.

### 5a. Create a model connection

SurfSense routes through LiteLLM. For an OpenAI-compatible endpoint (local vLLM,
DeepSeek, etc.), the most unambiguous form is `provider: openai` + explicit `base_url`,
which forces LiteLLM to use the key you pass on the connection (no env-var lookup):

```bash
curl -s -b cookies.txt -X POST "$SS/api/v1/model-connections" \
  -H "Origin: $ORIGIN" \
  -H "Content-Type: application/json" \
  -d '{
    "provider": "openai",
    "base_url": "https://api.deepseek.com/v1",
    "api_key": "sk-...",
    "scope": "SEARCH_SPACE",
    "search_space_id": 1,
    "enabled": true,
    "models": [
      {
        "model_id": "deepseek-chat",
        "display_name": "DeepSeek Chat",
        "source": "MANUAL",
        "supports_chat": true,
        "supports_tools": true,
        "enabled": true
      }
    ]
  }'
```

Critical flags in the `models[]` entry:

- `enabled: true` — **defaults to `false`**; omit it and the model is unusable.
- `supports_tools: true` — the deep agent uses tools; without this the model can be
  rejected for the chat role.

The response returns the connection `id` **and** a nested model row with its own `id` —
**that model-row id is what roles reference**, not the connection id. If LiteLLM
recognizes the model it also auto-fills `max_input_tokens`, a good sign the mapping is
clean.

> Provider notes: DeepSeek/Qwen/Moonshot/Zhipu all expose OpenAI-compatible APIs.
> `provider: deepseek` (native) also works but pulls the key from provider-native
> resolution — using `provider: openai` + `base_url` keeps the key strictly
> per-connection and avoids ambiguity.

### 5b. Assign the chat role

Read current roles (all `0` = unassigned):

```bash
curl -s -b cookies.txt "$SS/api/v1/search-spaces/1/model-roles"
```

Point `chat_model_id` at the **model-row id** from 5a (via `PUT`):

```bash
curl -s -b cookies.txt -X PUT "$SS/api/v1/search-spaces/1/model-roles" \
  -H "Origin: $ORIGIN" \
  -H "Content-Type: application/json" \
  -d '{"chat_model_id": 4}'
```

Confirm status flips to configured:

```bash
curl -s -b cookies.txt "$SS/api/v1/global-llm-config-status"   # {"exists":false} before, populated after
```

---

## 6. Create a chat thread

`new_chat` appends a turn to an existing **thread** — `chat_id` is a thread id, so make
one first. Only `search_space_id` is required.

```bash
curl -s -b cookies.txt -X POST "$SS/api/v1/threads" \
  -H "Origin: $ORIGIN" \
  -H "Content-Type: application/json" \
  -d '{"search_space_id": 1, "title": "api test"}'
```

Grab the returned `id`. (List existing threads to reuse one:
`curl -s -b cookies.txt "$SS/api/v1/threads?search_space_id=1"`.)

> **Watch the response shapes — they are not uniform** (discovered the hard way):
> - **Create** (`POST /threads`) returns the thread object directly, with `.id` at top level.
> - **List** (`GET /threads?search_space_id=…`) wraps the array under **`.threads`** —
>   *not* `.items` and *not* a bare array. Iterate `(.threads // .items // .)[]?` to stay
>   safe: `... | jq -r '.threads[] | "#\(.id) \(.title)"'`.
> - **Get-by-id** (`GET /threads/{id}`) returns **`{"messages":[...]}`** — a messages view
>   with **no `.id` field**. Do *not* use it to check "does this thread exist"; a 200 here
>   with no `.id` will fool a naive check. Verify existence via the **list** route and
>   test membership of the id instead.
> - Note the mixed casing: list items use `createdAt`/`updatedAt` (camelCase) while other
>   endpoints use `created_at` (snake_case). Don't assume one convention.

---

## 7. Ask the question (streaming RAG)

`POST /api/v1/new_chat`. Required: `chat_id`, `user_query`, `search_space_id`. Pin the
document with `mentioned_document_ids` so the agent definitely retrieves it. It's a
**write** (Origin header) and it **streams** SSE (`-N` to see events live).

```bash
curl -sN -b cookies.txt -X POST "$SS/api/v1/new_chat" \
  -H "Origin: $ORIGIN" \
  -H "Content-Type: application/json" \
  -d '{
    "chat_id": 1,
    "search_space_id": 1,
    "user_query": "What is Project Zephyr, when did it launch, what was its budget, and who led it?",
    "mentioned_document_ids": [1]
  }'
```

You'll get an SSE stream: `data-thinking-step` events, a `task` to the `knowledge_base`
subagent, tool calls, then `text` deltas assembling the cited answer, and finally
`finish` / `[DONE]`. A successful run answers from your document (launch date, budget,
owner).

---

## 7b. Merged (non-streaming) output

**There is no synchronous chat endpoint on this build.** `new_chat` is the only
answer-generating route, `NewChatRequest` has no `stream: false` flag, and no
`complete`/`generate`/`answer` path exists. The agent is built on the AI SDK stream
protocol — streaming is the design. So to get one merged answer, collapse the stream
**client-side**: keep only the `text-delta` events and concatenate their `delta` fields.

Canonical recipe (hardened — use this):

```bash
curl -sN -b cookies.txt -X POST "$SS/api/v1/new_chat" \
  -H "Origin: $ORIGIN" -H "Content-Type: application/json" \
  -d '{"chat_id":1,"search_space_id":1,"user_query":"What is Project Zephyr, when did it launch, what was its budget, and who led it?","mentioned_document_ids":[1]}' \
| sed -n 's/^data: //p' \
| jq -Rrj --unbuffered 'fromjson? | select(.type=="text-delta") | .delta'
echo
```

Why this exact form:

- `sed -n 's/^data: //p'` — keep only SSE data lines, strip the `data: ` prefix.
- `jq -R` — read each line as a **raw string** (not pre-parsed JSON).
- `fromjson?` — parse it, but the trailing `?` **silently drops anything unparseable**.
  This is essential: the stream ends with a literal `data: [DONE]` line (not JSON) plus
  occasional blank keep-alive lines. Without `?`, jq errors on `[DONE]` and — because
  `-j` buffers with no newline — can abort **before flushing**, printing *nothing* even
  when the answer streamed fine.
- `select(.type=="text-delta") | .delta` — drop all thinking-step / tool / status events;
  emit only the user-visible answer tokens.
- `-j` joins with no separator (fusing tokens into continuous prose); `--unbuffered`
  flushes each delta; trailing `echo` adds the final newline.

Capture into a variable instead of stdout:

```bash
ANSWER=$(curl -sN -b cookies.txt -X POST "$SS/api/v1/new_chat" \
  -H "Origin: $ORIGIN" -H "Content-Type: application/json" \
  -d '{"chat_id":1,"search_space_id":1,"user_query":"...","mentioned_document_ids":[1]}' \
  | sed -n 's/^data: //p' \
  | jq -Rrj 'fromjson? | select(.type=="text-delta") | .delta')
printf '%s\n' "$ANSWER"
```

> **Empty output?** It almost never means the merge is wrong — it means the stream
> carried no `text-delta` events, i.e. the request errored before answering. Re-run
> **without the pipe** to see the raw events. Usual causes: unset `$ORIGIN` →
> `CSRF origin check failed` (see §0), or an expired ~1h session cookie → 401. Fix those,
> then re-pipe.

**Alternative — fire-and-fetch.** Let the turn finish, then read the persisted assistant
message back as plain JSON (the completed turn is saved to the thread):

```bash
curl -s -b cookies.txt "$SS/api/v1/threads/1/turn-status"   # wait until idle
curl -s -b cookies.txt "$SS/api/v1/threads/1/messages"      # final message as JSON
```

More calls (you may need to poll `turn-status` until `idle`), but avoids SSE parsing
entirely. Prefer the `jq` collapse above unless you specifically want the stored form.

---

## 8. Troubleshooting (everything that actually went wrong)

| Symptom | Cause | Fix |
|---|---|---|
| `jq .access_token` → `null` on login | Cookie-based auth; token isn't in the body | Use a cookie jar (`-c`/`-b`); ignore the missing field |
| `{"detail":"CSRF origin check failed"}` on a POST | Authenticated writes need CSRF `Origin` — often because `$ORIGIN` is **unset** (empty header), not a wrong value | Add `-H "Origin: $ORIGIN"`; ensure `$ORIGIN` is set in the current shell (`echo "$ORIGIN"`), see §0 |
| Merge pipe prints **nothing** | Stream had no `text-delta` (request errored first), or `jq` died on the `[DONE]` line before flushing | Re-run without the pipe to see the real error (usually unset `$ORIGIN` or expired cookie); use the `fromjson?` + `--unbuffered` recipe in §7b |
| Doc stuck, never `ready` | Celery worker not running | Start the Celery worker; re-poll `/documents?search_space_id=...` |
| Upload fails with a **name conflict** (`same_name_conflict`) | A *different* file with the same name already exists in the space; the overwrite guard refused to clobber it (see §4) | Rename the file, or intentionally replace with overwrite confirmed (`allow_overwrite=True`). To *share* an existing doc, add the user to the space instead of re-uploading |
| `new_chat` → `{"exists":false}` / no answer | No LLM connection or chat role unassigned | Create connection (5a), assign `chat_model_id` (5b) |
| model row unusable despite existing | `enabled` defaults to `false` | Set `enabled: true` on both connection and model entry |
| `System message must be at the beginning` (`litellm.BadRequestError`) | The **inference server** enforces leading-only system messages; SurfSense sends system mid-array | Fix on the serving side (relax the check / chat template), or front it with a normalizing proxy (e.g. LiteLLM Proxy), or use a provider without that rule |
| `MODEL_AUTH_FAILED` but key works via direct curl | Stale router/model cache, or provider-native key resolution reading elsewhere | Restart the backend; or use `provider: openai` + `base_url` so the key is strictly per-connection |
| `different vector dimensions 384 and 1024` | Query embedding model ≠ the model that embedded stored chunks | Align `EMBEDDING_MODEL` with the stored chunks' dimension, **restart the backend** (reloads the in-memory embedder), and re-embed the doc if needed |
| Config change "ignored" | Backend loaded old config/model into memory at boot | **Restart the backend** (and Celery). Many issues above reduce to this |

### The embedding-dimension trap (important)

pgvector's `chunks.embedding` column has a **fixed dimension** baked in at table
creation (`vector(N)`). The stored chunks and the *live query embedder* must produce the
same N:

- `all-MiniLM-L6-v2` → **384**
- `Qwen/Qwen3-Embedding-0.6B` → **1024**

If you change `EMBEDDING_MODEL` after documents are already indexed, queries embed at the
new dimension and pgvector can't compare them to the stored vectors. Either keep
`EMBEDDING_MODEL` matched to what's already stored, **or** re-embed all documents under
the new model (and ensure the column dimension matches). After any change, **restart the
backend** so the new embedder is actually loaded — a stale in-memory model was the root
cause here.

---

## 9. Operational hygiene

- **API keys land in the DB and are echoed in connection responses.** Rotate keys before
  exposing the box; scrub them from any logs/pastes.
- **Don't pile up connections.** Updating a key by creating connection after connection
  leaves dead rows. Prefer `PATCH /api/v1/model-connections/{id}` to update in place, or
  `DELETE` the stale ones. Always confirm the **role** points at the model row carrying
  the valid key (`GET /api/v1/search-spaces/{id}/model-roles`).
- **`{connection_id}/verify` can be a no-op** for provider-native connections (it returns
  `"provider-native authentication"` without testing the key). Don't treat it as proof
  the key works — a real generation call is the true test.
- **Restart after config changes.** Embedding model, env vars, and (sometimes) new model
  connections are read into memory at boot.

---

## Quick reference — the happy path

```bash
export SS=http://localhost:8000 ORIGIN=http://localhost:3000   # both, every new shell

# 1. login
curl -s -c cookies.txt -X POST "$SS/auth/jwt/login" \
  --data-urlencode "username=you@example.com" --data-urlencode "password=..."

# 2. upload + wait for ready
curl -s -b cookies.txt -X POST "$SS/api/v1/documents/fileupload" \
  -H "Origin: $ORIGIN" -F "files=@/tmp/doc.txt" -F "search_space_id=1"
curl -s -b cookies.txt "$SS/api/v1/documents?search_space_id=1"   # -> status ready

# 3. LLM: connection -> role
curl -s -b cookies.txt -X POST "$SS/api/v1/model-connections" -H "Origin: $ORIGIN" \
  -H "Content-Type: application/json" -d '{ ...connection... }'    # note the model-row id
curl -s -b cookies.txt -X PUT "$SS/api/v1/search-spaces/1/model-roles" -H "Origin: $ORIGIN" \
  -H "Content-Type: application/json" -d '{"chat_model_id": <MODEL_ROW_ID>}'

# 4. thread -> ask
curl -s -b cookies.txt -X POST "$SS/api/v1/threads" -H "Origin: $ORIGIN" \
  -H "Content-Type: application/json" -d '{"search_space_id":1,"title":"t"}'   # note thread id
curl -sN -b cookies.txt -X POST "$SS/api/v1/new_chat" -H "Origin: $ORIGIN" \
  -H "Content-Type: application/json" \
  -d '{"chat_id":<THREAD_ID>,"search_space_id":1,"user_query":"...","mentioned_document_ids":[1]}'

# 4b. same call, merged into one answer (no streaming) — see §7b
curl -sN -b cookies.txt -X POST "$SS/api/v1/new_chat" -H "Origin: $ORIGIN" \
  -H "Content-Type: application/json" \
  -d '{"chat_id":<THREAD_ID>,"search_space_id":1,"user_query":"...","mentioned_document_ids":[1]}' \
  | sed -n 's/^data: //p' \
  | jq -Rrj --unbuffered 'fromjson? | select(.type=="text-delta") | .delta' ; echo
```

---

## 10. Wrapper script — `ask.sh`

`ask.sh` bundles the whole §2–§7b flow into one interactive tool so you don't re-type
curls. It models a **session** as *one chat thread plus the set of document ids ingested
into it*, persisted in `.ss_session_<SPACE>.json` so it survives across runs.

```bash
chmod +x ask.sh
./ask.sh --new --folder ./mydocs     # fresh session, ingest folder, then Q&A loop
./ask.sh --folder ./more             # resume session, add ./more to its knowledge
./ask.sh                             # resume session, straight to Q&A
```

**What it does, in order:**

1. **Auth** — reuses `cookies.txt` if the session is still valid (`GET /users/me` → 200),
   logs in only when needed, and auto-relogins once if a call fails on CSRF/auth/empty.
2. **Session** — `--new` creates a fresh thread; otherwise it resumes the cached one
   (re-verifying it still exists). A session accumulates document ids.
3. **Ingest** (`--folder PATH`) — uploads every file (top-level; `RECURSIVE=1` recurses),
   then **blocks on a live progress bar**, polling `documents?search_space_id=…` until
   every file is `ready`. This is the correct gate: querying before `ready` silently
   omits un-indexed docs (see §4). Only ready ids are appended to the session.
4. **Q&A loop** — each question is pinned to the session's accumulated ids via
   `mentioned_document_ids`, and the SSE stream is collapsed to one merged answer with the
   §7b `fromjson?` pipe.

**In-loop commands:** `/add <path>` (ingest another folder), `/docs` (list scoped ids),
`/list` (show current session + all threads), `/attach <id>` (switch to an existing
thread), `/new` (fresh session), `/help`, `/exit` (or Ctrl-D).

**Session management** (a "session" is stored only in `.ss_session_<SPACE>.json` — a
local `{thread_id, doc_ids}` pointer; the backend has no notion of "this thread's docs"):

- `--list` — show the saved session plus every thread in the space (id + title).
- `--session <id>` / `/attach <id>` — reattach to an existing thread by id. This restores
  the **conversation** but not the **doc scope** (that lived in the session file); until
  you `/add` a folder, questions search the whole space.
- `--new` — fresh thread; **backs up** the current session file to `…json.bak` first, so a
  new session never silently destroys the old pointer.
- **Safe resume:** if a session file exists but its thread can't be verified, the script
  **stops and reports** instead of auto-creating a new session on top of it (an earlier
  version silently overwrote a good session — don't reintroduce that).

**Rebuilding a lost session pointer.** The pointer is just local state; the thread and its
`ready` docs persist server-side. To rebuild it, **write to a temp file and move only if
non-empty** — never redirect straight onto the live file (`> file` truncates it the moment
the command starts, so a failed `curl`/`jq` leaves you with a blank):

```bash
curl -s -b cookies.txt "$SS/api/v1/documents?search_space_id=1" \
| jq '{thread_id:4, doc_ids:[.items[]|select(.title|test("\\.md$"))|.id]}' > /tmp/sess.json
[ -s /tmp/sess.json ] && mv /tmp/sess.json .ss_session_1.json
```

**Config via env** (local defaults baked in): `SS`, `ORIGIN`, `EMAIL`, `PASSWORD`,
`SPACE`, `COOKIES`, `SESSION`, `POLL`, `TIMEOUT`, `RECURSIVE`.

**Known limitation — content dedup vs. filename tracking.** Readiness is tracked by
matching uploaded *filenames* against document titles. SurfSense dedups by content hash,
so a file whose content already exists under a different title won't match — its bar slot
stays unfilled until `TIMEOUT`. Fine for fresh, uniquely-named folders; if you expect to
re-ingest overlapping content, switch the tracking to a **before/after document-id diff**
(snapshot ids in the space before upload, treat any new id as this batch) which ignores
titles entirely.

**Hygiene:** the login `PASSWORD` has a default in the script — pass `PASSWORD=… ./ask.sh`
or edit it out before sharing the file (same note as §9 on keys in the DB).

**Operational lessons (all learned the hard way here):**

- **After editing/patching the script, make sure the file you run is the one you changed.**
  A stale on-disk copy will keep exhibiting a bug you already "fixed." Confirm with
  `grep -n -A5 'thread_alive()' ask.sh`.
- **Don't guess API response shapes — read them.** Every hard failure in building this
  traced to an assumption (`.items` vs `.threads`, `{"messages":[]}` on get-by-id, a
  bare-array vs wrapper). One `curl … | jq '.' | head -40` settles it.
- **Env vars vanish across shells.** Persist `SS`/`ORIGIN` in `~/.bashrc` (see §0). The
  script defaults them internally, but your manual probe commands won't.
