#!/usr/bin/env python3
"""serve.py - serve the OSINT web client, with the model config baked in.

    ./serve.py --deepseek sk-...                    # DeepSeek API, http://localhost:3000
    ./serve.py --vllm Qwen2.5-7B --vllm_port 8001   # local vLLM (OpenAI-compatible)
    ./serve.py --deepseek sk-... --port 39317       # odd port (see the CSRF note below)
    ./serve.py --backend https://api.osint.example  # login stops asking for the URL
    ./serve.py --backend same-origin                # ...and one proxy serves both
    DEEPSEEK_API_KEY=sk-... ./serve.py              # same, from the environment

An explicit flag picks the backend; DEEPSEEK_API_KEY / VLLM_MODEL from the
environment only fill in when neither flag was given. --deepseek wins when both
flags are given. --vllm points the page at the vLLM
container's OpenAI-compatible endpoint, http://localhost:<vllm_port>/v1 as seen
FROM THE BACKEND (the backend dials the model, not the browser), with the
placeholder key vLLM expects ("EMPTY") - so the login screen asks for nothing.

--backend bakes in the API's address AS THE BROWSER SEES IT, so the deployment
decides it instead of every user typing it under Advanced. `--backend same-origin`
is the special case for a reverse proxy that serves the page and the API on one
hostname: the page then uses relative URLs, which is both one less thing to
configure and the only layout with no cross-origin problem at all. It needs two
proxy rules, since the client calls two prefixes at the API's root: /auth/* and
/api/v1/* to the API, everything else to this server. A single rewriting
/something/* rule works too - pass that prefix as --backend - but nothing here
strips a prefix, so the proxy must.

With a key, the login screen stops asking for one: the page is served with the
key already in it, and it is never written to localStorage. Without a key this
is a plain static server and the login screen asks, so

    python3 -m http.server 3000

remains a valid way to run the page.

Port 3000 is the default for a reason. The backend's CSRF middleware
(app/auth/csrf.py) rejects every cookie-authenticated POST/PUT/DELETE whose
Origin is not allow-listed, and that list is built from NEXT_FRONTEND_URL /
SURFSENSE_PUBLIC_URL / CSRF_ALLOWED_ORIGINS - which on a stock self-hosted
backend means http://localhost:3000, the origin ask.sh already sends. To serve
on another port, either add that origin to CSRF_ALLOWED_ORIGINS in the backend's
.env, or leave the origin alone by tunnelling the odd port onto 3000 locally:

    ssh -L 3000:localhost:39317 you@server

The key is handed to any browser that can fetch the page, so this binds to
loopback only. --bind something else and you are publishing the key to that
network; the script makes you pass --i-know to do it.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import json
import os
import socketserver
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PAGE = HERE / "index.html"
MARKER = b"/*__SS_CONFIG__*/"


class Handler(http.server.SimpleHTTPRequestHandler):
    """Static handler that rewrites index.html's config marker on the way out."""

    def __init__(self, *args, config: dict | None = None, **kwargs):
        self.config = config or {}
        super().__init__(*args, directory=str(HERE), **kwargs)

    def do_GET(self):  # noqa: N802 - stdlib's casing
        if self.config and self.path.split("?")[0] in ("/", "/index.html"):
            self.serve_page()
            return
        super().do_GET()

    def serve_page(self):
        html = PAGE.read_bytes()
        if MARKER not in html:
            self.send_error(500, "index.html has no /*__SS_CONFIG__*/ marker to inject into")
            return

        # json.dumps gives a correctly escaped JS string literal for any value.
        assign = " ".join(
            f"window.SS_CONFIG.{k} = {json.dumps(v)};" for k, v in self.config.items()
        ).encode()
        body = html.replace(MARKER, assign, 1)

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # The page now carries a secret: never let a proxy or the browser keep it.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        sys.stderr.write("  %s\n" % (fmt % args))


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    p = argparse.ArgumentParser(
        description="Serve the SurfSense mini web client.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("\n", 2)[2],
    )
    p.add_argument("port", nargs="?", type=int, default=3000, help="default: 3000")
    p.add_argument("--port", dest="port_flag", type=int, help="same as the positional port")
    p.add_argument(
        "--deepseek",
        metavar="KEY",
        default="",
        help="DeepSeek API key to bake into the page "
        "(falls back to $DEEPSEEK_API_KEY when neither model flag is given)",
    )
    p.add_argument(
        "--vllm",
        metavar="MODEL",
        default="",
        help="serve against a local vLLM container instead: the model name as vLLM "
        "exposes it (falls back to $VLLM_MODEL when neither model flag is given). "
        "Ignored when --deepseek is given.",
    )
    p.add_argument(
        "--vllm_port",
        metavar="PORT",
        type=int,
        default=int(os.environ.get("VLLM_PORT", 0) or 0),
        help="port the vLLM OpenAI endpoint listens on, as seen from the backend "
        "(default: $VLLM_PORT). Required with --vllm.",
    )
    p.add_argument(
        "--vllm_host",
        metavar="HOST",
        default=os.environ.get("VLLM_HOST", "").strip() or "localhost",
        help="host the vLLM endpoint lives on, AS THE BACKEND SEES IT (default: "
        "$VLLM_HOST, else localhost). 'localhost' is right only while the backend "
        "shares a host with vLLM; when the backend runs in a container or pod, "
        "localhost is that container - pass the node's address instead.",
    )
    p.add_argument(
        "--backend",
        metavar="URL",
        default=os.environ.get("SURFSENSE_BACKEND_URL", "").strip(),
        help="API address AS THE BROWSER SEES IT, baked into the page so the login "
        "screen stops asking (default: $SURFSENSE_BACKEND_URL). Pass 'same-origin' "
        "when a proxy serves this page and the API on one hostname.",
    )
    p.add_argument("--bind", default="127.0.0.1", help="default: 127.0.0.1 (loopback only)")
    p.add_argument(
        "--i-know",
        action="store_true",
        help="allow --bind to a non-loopback address while serving a key",
    )
    args = p.parse_args()

    port = args.port_flag or args.port
    key = args.deepseek.strip()
    vllm_model = args.vllm.strip()

    # An explicit flag picks the backend; the environment only fills in when
    # neither flag was given, so a stray exported DEEPSEEK_API_KEY cannot
    # override an explicit --vllm.
    if not key and not vllm_model:
        key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        vllm_model = os.environ.get("VLLM_MODEL", "").strip()

    # --deepseek wins; --vllm only applies when no DeepSeek key was given.
    config: dict = {}
    mode = ""
    if key:
        config = {"apiKey": key}
        mode = "DeepSeek (key injected, login will not ask for it)"
    elif vllm_model:
        if not args.vllm_port:
            print(
                "error: --vllm needs --vllm_port <port> (or $VLLM_PORT) - the port the "
                "vLLM OpenAI endpoint listens on, as the backend sees it.\n"
                "       Without it the page would dial http://localhost:8000/v1, which "
                "is the SurfSense backend itself.",
                file=sys.stderr,
            )
            return 1
        # The backend dials the model, so the host here is the BACKEND's view of
        # vLLM - not the browser's and not this server's. It defaults to
        # localhost, which holds only while the backend runs on the same host;
        # a containerised backend needs --vllm_host <node address>.
        # vLLM ignores the Authorization header unless started with --api-key;
        # "EMPTY" is its documented placeholder.
        config = {
            "apiKey": "EMPTY",
            "model": vllm_model,
            "modelBase": f"http://{args.vllm_host}:{args.vllm_port}/v1",
        }
        mode = f"vLLM ({vllm_model} @ {config['modelBase']}, login will not ask for a key)"

    # The backend address is orthogonal to the model: it is injected whether or
    # not a key was. "same-origin" injects the empty string, which the page reads
    # as "prefix nothing", i.e. dial /auth and /api/v1 on whatever host served it.
    backend = args.backend.strip()
    if backend:
        config["backend"] = "" if backend == "same-origin" else backend.rstrip("/")

    if not PAGE.exists():
        print(f"error: {PAGE} not found", file=sys.stderr)
        return 1

    if key and args.bind not in ("127.0.0.1", "localhost", "::1") and not args.i_know:
        print(
            f"error: --bind {args.bind} would serve your API key to anyone who can reach "
            f"{args.bind}:{port}.\n"
            "       Bind to loopback and tunnel instead:  ssh -L 3000:localhost:"
            f"{port} you@server\n"
            "       If you really mean it, pass --i-know.",
            file=sys.stderr,
        )
        return 1

    handler = functools.partial(Handler, config=config)
    try:
        server = Server((args.bind, port), handler)
    except OSError as e:
        print(f"error: cannot bind {args.bind}:{port} - {e}", file=sys.stderr)
        return 1

    print(f"OSINT -> http://localhost:{port}   (Ctrl-C to stop)")
    print(f"  Model:    {mode or 'not set, login will ask for a key'}")
    if "backend" in config:
        print(f"  Backend:  {config['backend'] or 'same origin as the page'} (login will not ask)")
    else:
        print("  Backend:  not set, login will ask (default http://localhost:8000)")
    if port != 3000:
        print(
            f"  note:     the backend only trusts allow-listed origins, and :{port} is "
            "probably not one.\n"
            f"            Tunnel it:  ssh -L 3000:localhost:{port} you@server"
        )

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
