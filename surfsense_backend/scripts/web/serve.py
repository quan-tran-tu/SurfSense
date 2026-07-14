#!/usr/bin/env python3
"""serve.py - serve the OSINT web client, with the model API key baked in.

    ./serve.py --deepseek sk-...              # http://localhost:3000
    ./serve.py --deepseek sk-... --port 39317 # odd port (see the CSRF note below)
    DEEPSEEK_API_KEY=sk-... ./serve.py        # same, from the environment

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

    def __init__(self, *args, api_key: str = "", **kwargs):
        self.api_key = api_key
        super().__init__(*args, directory=str(HERE), **kwargs)

    def do_GET(self):  # noqa: N802 - stdlib's casing
        if self.api_key and self.path.split("?")[0] in ("/", "/index.html"):
            self.serve_page()
            return
        super().do_GET()

    def serve_page(self):
        html = PAGE.read_bytes()
        if MARKER not in html:
            self.send_error(500, "index.html has no /*__SS_CONFIG__*/ marker to inject into")
            return

        # json.dumps gives a correctly escaped JS string literal for any key.
        assign = f"window.SS_CONFIG.apiKey = {json.dumps(self.api_key)};".encode()
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
        default=os.environ.get("DEEPSEEK_API_KEY", ""),
        help="model API key to bake into the page (default: $DEEPSEEK_API_KEY)",
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

    handler = functools.partial(Handler, api_key=key)
    try:
        server = Server((args.bind, port), handler)
    except OSError as e:
        print(f"error: cannot bind {args.bind}:{port} - {e}", file=sys.stderr)
        return 1

    print(f"OSINT -> http://localhost:{port}   (Ctrl-C to stop)")
    print(f"  API key:  {'injected, login will not ask for it' if key else 'not set, login will ask'}")
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
