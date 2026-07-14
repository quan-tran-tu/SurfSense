# SurfSense mini

A single-file web client for a self-hosted SurfSense backend. It is
[`ask.sh`](../ask.sh) in a browser: same API contract, same isolation model, no
build step, no `node_modules`, no framework. One `index.html` — markup, styles
and logic — that you can read top to bottom.

Use it when you want a UI but not the full `surfsense_web` Next.js app.

## Run it

```sh
./serve.py --deepseek sk-...          # http://localhost:3000
DEEPSEEK_API_KEY=sk-... ./serve.py    # same, from the environment
```

Then open <http://localhost:3000>, enter your email and password, and hit
**Register** (first time) or **Sign in**.

Given a key, `serve.py` bakes it into the page as it serves it, so the login
screen never asks for one and the key is never written to `localStorage`. Since
that means the page carries a secret, `serve.py` binds to loopback only; pointing
`--bind` at a real interface would hand the key to anyone who can reach the port,
so it refuses unless you pass `--i-know`. Tunnel instead (see below).

Without a key it is an ordinary static server and the login screen asks for one —
so this still works, and needs nothing but Python:

```sh
python3 -m http.server 3000 --directory surfsense_backend/scripts/web
```

The backend must be running (`http://localhost:8000` by default — change it
under *Advanced*), along with its Celery worker, or folders will never finish
indexing.

## The origin must be `http://localhost:3000`

The backend's CSRF middleware (`app/auth/csrf.py`) rejects any
cookie-authenticated `POST`/`PUT`/`DELETE` whose `Origin` is not allow-listed.
The allow-list comes from `NEXT_FRONTEND_URL`, `SURFSENSE_PUBLIC_URL` and
`CSRF_ALLOWED_ORIGINS`, which on a stock self-hosted backend means
`http://localhost:3000` — the same origin `ask.sh` sends by default.

Serve from another origin and you get a working login followed by `403 CSRF
origin check failed` on everything else. Opening `index.html` via `file://`
fails the same way: the browser sends `Origin: null`.

**If ports 3000/8000 are taken on the server**, note that what the backend checks
is the origin your *browser* sends — the remote port is invisible to it. So run
on any free ports remotely and map them back to the expected ones locally:

```sh
# on the server
./serve.py 39317 --deepseek sk-...

# from your machine, mapping the odd remote ports onto the ones the app expects
ssh -L 3000:localhost:39317 -L 8000:localhost:8123 you@server
```

Then browse <http://localhost:3000>. No backend config changes, and nothing to
type into *Advanced*. (`8123` here stands for whatever port the backend actually
listens on.)

**To hit the server directly instead**, the two ports differ:

- the **backend** port is unconstrained — just set *Advanced → Backend URL*;
- the **frontend** port becomes your `Origin`, so it must be allow-listed in the
  backend's `.env`, which needs a restart:

  ```
  CSRF_ALLOWED_ORIGINS=http://localhost:3000,http://server:39317
  NEXT_FRONTEND_URL=http://server:39317
  ```

  `NEXT_FRONTEND_URL` matters only when the host isn't `localhost`/`127.0.0.1`:
  CORS (`app/app.py`) already allows any *port* on those two by regex, but a bare
  hostname or IP falls outside it.

The page warns when it isn't on port 3000. That check is a heuristic — if you
allow-listed a different origin properly, ignore it.

## What it does

Everything `ask.sh` does, minus the REPL:

| `ask.sh` | here |
|---|---|
| `ss-register.sh` | **Register** button |
| `--api-key` / `$DEEPSEEK_API_KEY` | `serve.py --deepseek` (or the login field) |
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
- Your preferences are kept in `localStorage`. A key injected by
  `serve.py --deepseek` is *not* — it lives only in the page that served it. A key
  typed into the login field is. Either way it also ends up server-side on the
  model connection, which is how the backend calls the model at all. Your password
  is never persisted.
- The session cookie is short-lived (~1h). A 401 triggers one transparent
  re-login, exactly like `ask.sh`'s `api()`.

See [`docs/surfsense-backend-api-cookbook.md`](../../docs/surfsense-backend-api-cookbook.md)
for the underlying API contract.
