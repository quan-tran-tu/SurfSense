# SurfSense mini

A single-file web client for a self-hosted SurfSense backend. It is
[`ask.sh`](../ask.sh) in a browser: same API contract, same isolation model, no
build step, no `node_modules`, no framework. One `index.html` — markup, styles
and logic — that you can read top to bottom.

Use it when you want a UI but not the full `surfsense_web` Next.js app.

## Run it

```sh
./serve.sh                      # http://localhost:3000
```

or, on any platform with Python:

```sh
python -m http.server 3000 --directory surfsense_backend/scripts/web
```

Then open <http://localhost:3000>, enter your email, password and DeepSeek API
key, and hit **Register** (first time) or **Sign in**.

The backend must be running (`http://localhost:8000` by default — change it
under *Advanced*), along with its Celery worker, or folders will never finish
indexing.

## Port 3000 is mandatory

The backend's CSRF middleware (`app/auth/csrf.py`) rejects any
cookie-authenticated `POST`/`PUT`/`DELETE` whose `Origin` is not allow-listed.
The allow-list comes from `NEXT_FRONTEND_URL`, `SURFSENSE_PUBLIC_URL` and
`CSRF_ALLOWED_ORIGINS`, which on a stock self-hosted backend means
`http://localhost:3000` — the same origin `ask.sh` sends by default.

Serve from another port and you get a working login followed by `403 CSRF origin
check failed` on everything else. The page detects this and warns you. To use a
different port, add it to `CSRF_ALLOWED_ORIGINS` in the backend's `.env`.

Opening `index.html` directly via `file://` does not work either: the browser
sends `Origin: null`.

## What it does

Everything `ask.sh` does, minus the REPL:

| `ask.sh` | here |
|---|---|
| `ss-register.sh` | **Register** button |
| login + cookie jar | **Sign in** (the cookie is httpOnly, set by the backend) |
| `ensure_space` | automatic — your private space is `cli:<email>` |
| `ensure_model` | automatic — model connection + chat role, from the API key |
| `--session` / `/switch` | the **Session** list in the sidebar |
| `/add`, `/reextract` | **+ Add** (a folder picker; re-picking an indexed folder offers a rebuild) |
| `/folders`, `/rm` | the **Folders** list |
| `/share`, `/unshare` | **Share** on a folder row; tokens are listed with a **Revoke** button |
| `/import` | paste a token under **Shares** |
| `FORCE_SEARCH` | the **Force retrieval** checkbox |

**Isolation is unchanged.** Every user gets their own search space named
`cli:<email>`; a search space is the backend's real, server-enforced access
boundary, so user A can never see or delete user B's documents. This client
never lets you name a space by id. Because the space name matches, `ask.sh` and
this page share one knowledge base — ingest from the CLI, ask from the browser.

## Things worth knowing

- **Force retrieval** prepends the same directive as `ask.sh`'s `build_query()`.
  Whether `search_knowledge_base` runs is the model's own choice, and weaker
  models (including `deepseek-chat`) will otherwise answer from memory without
  ever touching your documents. Leave it on unless you are debugging.
- **A folder's KB name is its basename**, never its full path. `Folder.name`
  becomes the agent's virtual path (`/documents/<name>/…`), so a full disk path
  would leak your directory layout into every prompt. Two folders with the same
  basename therefore collide onto one KB folder.
- **Uploads wait for indexing.** A folder cannot answer anything until Celery has
  finished; the progress bar tracks documents to `ready`, and holds until the
  counts stop moving (Celery keeps inserting rows after the upload returns).
- Dotfiles, dot-directories and files over 500 MB are skipped, matching
  `ask.sh`'s `collect_files`.
- **Share tokens are remembered locally.** The backend has no endpoint that lists
  the shares you minted (only create / revoke / redeem), so this page keeps them
  in `localStorage` to give you a one-click **Revoke**. Clearing site data loses
  the list, not the shares — a token you have written down still revokes.
- Your preferences, including the API key, are kept in `localStorage`. The key is
  also stored server-side on the model connection, which is how the backend calls
  the model at all. Your password is never persisted.
- The session cookie is short-lived (~1h). A 401 triggers one transparent
  re-login, exactly like `ask.sh`'s `api()`.

See [`docs/surfsense-backend-api-cookbook.md`](../../docs/surfsense-backend-api-cookbook.md)
for the underlying API contract.
