# OSINT web client

React 19 + TypeScript, built with Vite. The deployed page is `dist/`, served by
[`../scripts/web/serve.py`](../scripts/web/serve.py), which injects the model and
backend config into `index.html`'s `/*__SS_CONFIG__*/` marker. How the client
behaves — sessions, scope, commands, admin — is documented in
[`../scripts/web/README.md`](../scripts/web/README.md).

```sh
npm ci
npm run build       # type-check, then dist/
npm run typecheck   # type-check only
```

## Developing

`npm run dev` serves on http://localhost:3000 and proxies the API under
`/__api`, so the page and the API share an origin: the session cookie is
first-party and the backend's CSRF check sees an allow-listed Origin (the proxy
sets it to the API's own). Point it at any backend and play serve.py's part with
environment variables:

```sh
SS_DEV_BACKEND=http://10.11.34.11:30800 \
SS_MODEL=qwen3.6-35b SS_MODEL_BASE=http://10.11.34.11:8014/v1 SS_API_KEY=EMPTY \
npm run dev
```

`SS_MODEL_BASE` is the model endpoint *as the backend sees it* — the backend
dials the model, not the browser.

## Layout

| Path | What |
| --- | --- |
| `src/store.ts` | App state (zustand) and the per-user localStorage prefs — same key and layout as the old single-file page, so templates, shares and session notes carry over |
| `src/queryClient.ts` | Server state (TanStack Query) and the keys writes invalidate |
| `src/api/` | Fetch wrapper (cookie auth, one silent re-login) and wire types |
| `src/features/session.ts` | Bootstrap: space, model connection, threads, history |
| `src/features/folders.ts` | Folder tree reads, upload/ingest, share/import |
| `src/features/chat/` | Streaming questions, slash commands, reports |
| `src/features/sidebar/` | Sessions, folder tree, templates, imports, shares |
| `src/features/admin/` | The system-admin page |
| `src/lib/Markdown.tsx` | Answer rendering (GFM, citations as chips; raw HTML is never rendered) |
