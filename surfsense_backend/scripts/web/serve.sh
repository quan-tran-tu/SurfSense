#!/usr/bin/env bash
# serve.sh — serve the mini web client on http://localhost:3000.
#
#   ./serve.sh              # then open http://localhost:3000
#   ./serve.sh 3000         # same; the port is the only argument
#
# Port 3000 is not cosmetic. The backend's CSRF middleware (app/auth/csrf.py)
# rejects every cookie-authenticated POST/PUT/DELETE whose Origin is not
# allow-listed, and the allow-list is built from NEXT_FRONTEND_URL /
# SURFSENSE_PUBLIC_URL / CSRF_ALLOWED_ORIGINS — which for a stock self-hosted
# backend means http://localhost:3000. Serving from any other port gets you a
# working login and a 403 on everything that matters.
#
# To use a different port, add it to CSRF_ALLOWED_ORIGINS in the backend's .env.
set -euo pipefail

PORT=${1:-3000}
DIR=$(cd "$(dirname "$0")" && pwd)

command -v python3 >/dev/null || { printf 'error: python3 is required\n' >&2; exit 1; }

printf 'SurfSense mini -> http://localhost:%s   (Ctrl-C to stop)\n' "$PORT"
exec python3 -m http.server "$PORT" --directory "$DIR" --bind 127.0.0.1
