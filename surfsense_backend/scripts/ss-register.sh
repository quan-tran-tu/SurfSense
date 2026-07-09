#!/usr/bin/env bash
# ss-register.sh — create a SurfSense user via POST /auth/register.
#
#   ./ss-register.sh --email you@example.com --password 'hunter2'
#   ./ss-register.sh --email you@example.com            # prompts for password
#
# Env: SS (backend base URL, default http://localhost:8000)
#
# Registration is only reachable when the backend runs with AUTH_TYPE != GOOGLE
# and REGISTRATION_ENABLED is on; otherwise this returns 403 and says so.
set -euo pipefail

: "${SS:=http://localhost:8000}"

EMAIL=""
PASSWORD=""

die() { printf 'error: %s\n' "$*" >&2; exit 1; }

usage() {
	sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'
	exit "${1:-0}"
}

while [ $# -gt 0 ]; do
	case "$1" in
		--email)    EMAIL=${2:-}; shift 2 ;;
		--password) PASSWORD=${2:-}; shift 2 ;;
		--url)      SS=${2:-}; shift 2 ;;
		-h|--help)  usage 0 ;;
		*)          die "unknown argument: $1 (try --help)" ;;
	esac
done

command -v curl >/dev/null || die "curl is required"
command -v jq   >/dev/null || die "jq is required"

[ -n "$EMAIL" ] || die "--email is required"

if [ -z "$PASSWORD" ]; then
	printf 'Password for %s: ' "$EMAIL" >&2
	read -rs PASSWORD
	printf '\n' >&2
fi
[ -n "$PASSWORD" ] || die "password must not be empty"

body=$(jq -nc --arg e "$EMAIL" --arg p "$PASSWORD" '{email:$e, password:$p}')

out=$(curl -sS -w $'\n%{http_code}' -X POST "$SS/auth/register" \
	-H 'Content-Type: application/json' -d "$body") || die "cannot reach $SS"

code=${out##*$'\n'}
resp=${out%$'\n'*}

case "$code" in
	2*)
		printf 'registered %s (id %s)\n' \
			"$(printf '%s' "$resp" | jq -r '.email')" \
			"$(printf '%s' "$resp" | jq -r '.id')"
		;;
	400)
		detail=$(printf '%s' "$resp" | jq -r '.detail // empty' 2>/dev/null || true)
		case "$detail" in
			REGISTER_USER_ALREADY_EXISTS) printf 'user %s already exists — nothing to do\n' "$EMAIL"; exit 0 ;;
			*) die "register rejected (400): $resp" ;;
		esac
		;;
	403) die "registration is disabled on this backend (AUTH_TYPE=GOOGLE, or REGISTRATION_ENABLED off)" ;;
	422) die "invalid email or password (password is usually min 8 chars): $resp" ;;
	429) die "rate limited by the backend; wait and retry" ;;
	*)   die "unexpected HTTP $code: $resp" ;;
esac

# Prove the credentials actually work end-to-end.
jar=$(mktemp)
trap 'rm -f "$jar"' EXIT
lc=$(curl -sS -o /dev/null -w '%{http_code}' -c "$jar" -X POST "$SS/auth/jwt/login" \
	--data-urlencode "username=$EMAIL" --data-urlencode "password=$PASSWORD")
[ "$lc" = 204 ] || [ "$lc" = 200 ] || die "registered, but login returned HTTP $lc"

printf 'login verified. now run:\n  ./ask.sh --email %s --api-key sk-... --session my-session --folder ./docs\n' "$EMAIL"
