#!/usr/bin/env bash
# ask.sh — CLI knowledge base + Q&A against a self-hosted SurfSense backend.
#
#   ./ask.sh --email you@x.com --api-key sk-... --session research --folder ./docs
#   ./ask.sh --email you@x.com --api-key sk-... --session research \
#            --folder ./docs --folder ./more --reextract
#   ./ask.sh --email you@x.com --api-key sk-... --session research   # resume, no ingest
#
# Required: --email, --password (or prompt / $SS_PASSWORD), --api-key, --session
# Optional: --folder PATH (repeatable), --reextract, --model, --base-url, --url
#
# Isolation: every user gets their own search space named "cli:<email>". A search
# space is the backend's real, server-enforced access boundary, so user A can
# never see or delete user B's documents. This script never accepts a space id.
#
# A "session" is a chat thread whose title is your --session string; folders are
# tracked server-side as watched folders. Nothing is cached locally except the
# login cookie, so there is no session file to lose or corrupt.
set -uo pipefail

: "${SS:=http://localhost:8000}"
: "${ORIGIN:=http://localhost:3000}"

EMAIL=${SS_EMAIL:-}
PASSWORD=${SS_PASSWORD:-}
API_KEY=${DEEPSEEK_API_KEY:-}
SESSION=${SS_SESSION:-}
MODEL_ID=${SS_MODEL:-deepseek-chat}
BASE_URL=${SS_BASE_URL:-https://api.deepseek.com/v1}
REEXTRACT=0
FOLDERS=()
LABELS=()

# Tunables.
BATCH=${SS_BATCH:-20}          # files per folder-upload request
POLL=${SS_POLL:-3}             # seconds between readiness polls
TIMEOUT=${SS_TIMEOUT:-1800}    # seconds to wait for a folder to finish indexing
STABLE=${SS_STABLE:-2}         # consecutive unchanged polls that mean "settled"
PIN_DOCS=${SS_PIN_DOCS:-0}     # 1 = pin questions to this space's folder doc ids
FORCE_SEARCH=${SS_FORCE_SEARCH:-1}  # 1 = prepend a directive that compels retrieval

SPACE_ID=""
THREAD_ID=""
HTTP_CODE=0
BODY=""
RELOGGED=0

# ---------------------------------------------------------------- plumbing

die()  { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }
warn() { printf '\033[33mwarn:\033[0m %s\n' "$*" >&2; }
info() { printf '\033[36m%s\033[0m\n' "$*" >&2; }

usage() { sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

while [ $# -gt 0 ]; do
	case "$1" in
		--email)      EMAIL=${2:-}; shift 2 ;;
		--password)   PASSWORD=${2:-}; shift 2 ;;
		--api-key)    API_KEY=${2:-}; shift 2 ;;
		--session)    SESSION=${2:-}; shift 2 ;;
		--folder)     FOLDERS+=("${2:-}"); LABELS+=(""); shift 2 ;;
		--as)         [ "${#LABELS[@]}" -gt 0 ] || die "--as must follow a --folder"
		              LABELS[${#LABELS[@]}-1]=${2:-}; shift 2 ;;
		--model)      MODEL_ID=${2:-}; shift 2 ;;
		--base-url)   BASE_URL=${2:-}; shift 2 ;;
		--url)        SS=${2:-}; shift 2 ;;
		--origin)     ORIGIN=${2:-}; shift 2 ;;
		--reextract)  REEXTRACT=1; shift ;;
		--no-force-search) FORCE_SEARCH=0; shift ;;
		-h|--help)    usage 0 ;;
		*)            die "unknown argument: $1 (try --help)" ;;
	esac
done

command -v curl >/dev/null || die "curl is required"
command -v jq   >/dev/null || die "jq is required"

[ -n "$EMAIL" ]   || die "--email is required"
[ -n "$API_KEY" ] || die "--api-key is required (DeepSeek key; wires the model to your user)"
[ -n "$SESSION" ] || die "--session is required (names the conversation you resume)"

if [ -z "$PASSWORD" ]; then
	printf 'Password for %s: ' "$EMAIL" >&2
	read -rs PASSWORD; printf '\n' >&2
fi
[ -n "$PASSWORD" ] || die "password must not be empty"

STATE_DIR=${SS_STATE_DIR:-$HOME/.surfsense}
mkdir -p "$STATE_DIR"; chmod 700 "$STATE_DIR" 2>/dev/null || true
COOKIES="$STATE_DIR/cookies-$(printf '%s' "$EMAIL@$SS" | tr -c 'a-zA-Z0-9' '_').txt"

# _raw METHOD PATH [curl args...] -> sets HTTP_CODE, BODY
_raw() {
	local method=$1 path=$2; shift 2
	local out
	out=$(curl -sS -w $'\n%{http_code}' -b "$COOKIES" -X "$method" "$SS$path" \
		-H "Origin: $ORIGIN" "$@" 2>/dev/null) || return 1
	HTTP_CODE=${out##*$'\n'}
	BODY=${out%$'\n'*}
	return 0
}

login() {
	rm -f "$COOKIES"
	local code
	code=$(curl -sS -o /dev/null -w '%{http_code}' -c "$COOKIES" -X POST "$SS/auth/jwt/login" \
		--data-urlencode "username=$EMAIL" --data-urlencode "password=$PASSWORD" 2>/dev/null) \
		|| die "cannot reach $SS"
	case "$code" in
		200|204) chmod 600 "$COOKIES" 2>/dev/null || true ;;
		400) die "bad credentials for $EMAIL — register first with ./ss-register.sh" ;;
		*)   die "login failed with HTTP $code" ;;
	esac
}

# api METHOD PATH [curl args...] -> prints body, dies on non-2xx.
# Transparently re-logs-in once if the ~1h session cookie expired.
api() {
	_raw "$@" || die "network error on $1 $2 (is $SS up?)"
	if { [ "$HTTP_CODE" = 401 ] || [ "$HTTP_CODE" = 403 ]; } && [ "$RELOGGED" = 0 ]; then
		RELOGGED=1; login
		_raw "$@" || die "network error on $1 $2"
	fi
	case "$HTTP_CODE" in
		2*) printf '%s' "$BODY" ;;
		*)  die "HTTP $HTTP_CODE on $1 $2 -> $BODY" ;;
	esac
}

session_ok() { _raw GET /users/me >/dev/null 2>&1 && [ "${HTTP_CODE:0:1}" = 2 ]; }

# ---------------------------------------------------------------- bootstrap

ensure_space() {
	local name="cli:$EMAIL" id
	id=$(api GET "/api/v1/searchspaces?limit=200&skip=0&owned_only=true" \
		| jq -r --arg n "$name" 'map(select(.name == $n)) | .[0].id // empty')
	if [ -z "$id" ]; then
		id=$(api POST "/api/v1/searchspaces" -H 'Content-Type: application/json' \
			-d "$(jq -nc --arg n "$name" '{name:$n, description:"Private CLI knowledge base"}')" \
			| jq -r '.id')
		info "created private search space #$id ($name)"
	fi
	[ -n "$id" ] && [ "$id" != null ] || die "could not resolve a search space"
	SPACE_ID=$id
}

# Create-or-update the DeepSeek connection, then point the chat role at its model row.
# provider=openai + explicit base_url keeps the key strictly per-connection.
ensure_model() {
	local conns cid mid roles cur
	conns=$(api GET "/api/v1/model-connections?search_space_id=$SPACE_ID")
	cid=$(printf '%s' "$conns" | jq -r --arg b "$BASE_URL" \
		'map(select(.base_url == $b and .search_space_id != null)) | .[0].id // empty')

	if [ -n "$cid" ]; then
		# Update the key in place rather than piling up dead connections.
		api PUT "/api/v1/model-connections/$cid" -H 'Content-Type: application/json' \
			-d "$(jq -nc --arg k "$API_KEY" '{api_key:$k, enabled:true}')" >/dev/null
		mid=$(printf '%s' "$conns" | jq -r --arg b "$BASE_URL" --arg m "$MODEL_ID" \
			'map(select(.base_url == $b)) | .[0].models | map(select(.model_id == $m)) | .[0].id // empty')
	fi

	if [ -z "${mid:-}" ]; then
		local payload resp
		payload=$(jq -nc --arg b "$BASE_URL" --arg k "$API_KEY" --arg m "$MODEL_ID" --argjson s "$SPACE_ID" '
			{provider:"openai", base_url:$b, api_key:$k, scope:"SEARCH_SPACE",
			 search_space_id:$s, enabled:true,
			 models:[{model_id:$m, display_name:$m, source:"MANUAL",
			          supports_chat:true, supports_tools:true, enabled:true}]}')
		if [ -n "$cid" ]; then
			# Connection exists but lacks this model id -> recreate cleanly.
			api DELETE "/api/v1/model-connections/$cid" >/dev/null
		fi
		resp=$(api POST "/api/v1/model-connections" -H 'Content-Type: application/json' -d "$payload")
		mid=$(printf '%s' "$resp" | jq -r --arg m "$MODEL_ID" \
			'.models | map(select(.model_id == $m)) | .[0].id // empty')
		[ -n "$mid" ] || die "connection created but model row for '$MODEL_ID' is missing: $resp"
		info "connected $MODEL_ID (model row #$mid)"
	fi

	roles=$(api GET "/api/v1/search-spaces/$SPACE_ID/model-roles")
	cur=$(printf '%s' "$roles" | jq -r '.chat_model_id // 0')
	if [ "$cur" != "$mid" ]; then
		api PUT "/api/v1/search-spaces/$SPACE_ID/model-roles" -H 'Content-Type: application/json' \
			-d "$(jq -nc --argjson m "$mid" '{chat_model_id:$m}')" >/dev/null
		info "assigned chat role -> model row #$mid"
	fi
}

ensure_thread() {
	local id
	# Thread list wraps under .threads (not .items, not a bare array).
	id=$(api GET "/api/v1/threads?search_space_id=$SPACE_ID" \
		| jq -r --arg s "$SESSION" '(.threads // .items // .)[]? | select(.title == $s) | .id' | head -1)
	if [ -n "$id" ]; then
		info "resumed session '$SESSION' (thread #$id)"
	else
		id=$(api POST "/api/v1/threads" -H 'Content-Type: application/json' \
			-d "$(jq -nc --argjson s "$SPACE_ID" --arg t "$SESSION" '{search_space_id:$s, title:$t}')" \
			| jq -r '.id')
		[ -n "$id" ] && [ "$id" != null ] || die "could not create thread"
		info "started session '$SESSION' (thread #$id)"
	fi
	THREAD_ID=$id
}

# ---------------------------------------------------------------- folders

abspath() {
	local p=$1
	[ -d "$p" ] || return 1
	(cd "$p" && pwd)
}

# The knowledge-base name for a folder. NEVER the absolute path: folder_name
# becomes Folder.name, which path_resolver sanitizes into the agent's virtual
# path (/documents/<name>/...). Sending "/raid/quentin/temp_laws/1_10" there
# leaks the server's directory layout into every LLM prompt. Basename only.
#
# Trade-off: two different directories sharing a basename collide onto one KB
# folder (folder-upload matches an existing root by name). Pass --as / a second
# argument to /add to disambiguate.
kb_label() {
	local dir=$1 label=${2:-}
	[ -n "$label" ] && { printf '%s' "$label"; return; }
	printf '%s' "${dir##*/}"
}

watched_folders() { api GET "/api/v1/documents/watched-folders?search_space_id=$SPACE_ID"; }

# watched_id <kb-label> -> folder id, or empty
watched_id() {
	watched_folders | jq -r --arg p "$1" \
		'map(select(.name == $p or (.metadata.folder_path // "") == $p)) | .[0].id // empty'
}

# Every folder id in the subtree rooted at $1 (folder_id filtering is exact, not recursive).
subtree_ids() {
	local root=$1
	api GET "/api/v1/folders?search_space_id=$SPACE_ID" | jq -c --argjson root "$root" '
		. as $all
		| def kids($ids): [ $all[] | . as $f
			| select($f.parent_id != null and (($ids | index($f.parent_id)) != null))
			| $f.id ];
		  def grow($acc): (kids($acc) - $acc) as $new
			| if ($new | length) == 0 then $acc else grow($acc + $new) end;
		  grow([$root])'
}

# docs_in <subtree-json> -> DocumentRead array for that subtree
# NB: bind the doc as $d first. Inside index(), `.` is the array being indexed,
# so a bare `.folder_id` there reads off $sub, not the document.
docs_in() {
	api GET "/api/v1/documents?search_space_id=$SPACE_ID&document_types=LOCAL_FOLDER_FILE&page_size=-1" \
		| jq -c --argjson sub "$1" \
			'[ .items[] | . as $d
			   | select($d.folder_id != null and (($sub | index($d.folder_id)) != null)) ]'
}

# List indexable files, skipping dotfiles/dotdirs and anything over the 500MB cap.
collect_files() {
	local dir=$1
	find "$dir" -type f -not -path '*/.*' -size -500M -print0 2>/dev/null
}

# Block until every document under $1 has settled. Returns 1 if anything failed.
wait_ready() {
	local root=$1 expected=$2
	local waited=0 stable=0 last="" sub docs ready failed processing total
	printf '\n'
	while [ "$waited" -lt "$TIMEOUT" ]; do
		sub=$(subtree_ids "$root")
		docs=$(docs_in "$sub")
		total=$(printf '%s' "$docs" | jq 'length')
		ready=$(printf '%s' "$docs"      | jq '[.[] | select(.status.state == "ready")]      | length')
		failed=$(printf '%s' "$docs"     | jq '[.[] | select(.status.state == "failed")]     | length')
		processing=$(printf '%s' "$docs" | jq '[.[] | select(.status.state == "processing" or .status.state == "deleting")] | length')

		# An empty value here means a jq/API failure, not "zero documents".
		# Never let that fall through and spin silently until TIMEOUT.
		case "$total$ready$failed$processing" in
			*[!0-9]*|"") printf '\n' >&2; die "could not read document status for folder #$root (see the jq/API error above)" ;;
		esac

		printf '\r  indexing: %s/%s ready, %s processing, %s failed  (%ss)   ' \
			"$ready" "$expected" "$processing" "$failed" "$waited" >&2

		if [ "$total" -gt 0 ] && [ "$processing" -eq 0 ]; then
			# Celery may still be inserting rows; require the count to hold steady.
			if [ "$total:$ready:$failed" = "$last" ]; then
				stable=$((stable + 1))
				[ "$stable" -ge "$STABLE" ] && break
			else
				stable=0
			fi
		fi
		last="$total:$ready:$failed"
		sleep "$POLL"
		waited=$((waited + POLL))
	done
	printf '\r\033[K' >&2

	if [ "$waited" -ge "$TIMEOUT" ]; then
		warn "timed out after ${TIMEOUT}s — is the Celery worker running?"
		return 1
	fi
	if [ "${ready:-0}" -lt "$expected" ]; then
		warn "$ready/$expected files indexed; $failed failed, $((expected - ready - failed)) produced no document (unsupported type, or deduplicated against identical content already in this space)"
	else
		info "indexed $ready file(s)"
	fi
	[ "${failed:-0}" -eq 0 ]
}

delete_folder() {
	local fid=$1 waited=0
	api DELETE "/api/v1/folders/$fid" >/dev/null
	while [ "$waited" -lt "$TIMEOUT" ]; do
		if [ -z "$(api GET "/api/v1/folders?search_space_id=$SPACE_ID" | jq -r --argjson f "$fid" '.[] | select(.id == $f) | .id')" ]; then
			return 0
		fi
		printf '\r  removing folder #%s (%ss)   ' "$fid" "$waited" >&2
		sleep "$POLL"; waited=$((waited + POLL))
	done
	printf '\r\033[K' >&2
	warn "folder #$fid still present after ${TIMEOUT}s"
	return 1
}

# ingest <path> [force] [label]
ingest() {
	local raw=$1 force=${2:-0} label=${3:-} dir kb fid files n root i
	dir=$(abspath "$raw") || { warn "not a directory: $raw"; return 1; }
	kb=$(kb_label "$dir" "$label")

	fid=$(watched_id "$kb")
	if [ -n "$fid" ] && [ "$force" = 0 ]; then
		info "already extracted: '$kb' (folder #$fid) — skipping. use --reextract / /reextract to rebuild"
		return 0
	fi
	if [ -n "$fid" ] && [ "$force" = 1 ]; then
		info "re-extracting '$kb' — dropping folder #$fid and its documents"
		delete_folder "$fid" || return 1
	fi

	mapfile -d '' -t files < <(collect_files "$dir")
	n=${#files[@]}
	[ "$n" -gt 0 ] || { warn "no indexable files under $dir"; return 1; }
	info "uploading $n file(s) from $dir as '$kb'"

	root=""
	i=0
	while [ "$i" -lt "$n" ]; do
		local batch=() rels=() args=() f rel resp
		while [ "$i" -lt "$n" ] && [ "${#batch[@]}" -lt "$BATCH" ]; do
			f=${files[$i]}
			rel=${f#"$dir"/}
			batch+=("$f"); rels+=("$rel")
			# Quote the path so curl does not eat commas/semicolons in filenames.
			args+=(-F "files=@\"$f\"")
			i=$((i + 1))
		done
		rel_json=$(jq -nc '$ARGS.positional' --args "${rels[@]}")

		if [ -n "$root" ]; then
			resp=$(api POST "/api/v1/documents/folder-upload" \
				-F "folder_name=$kb" -F "search_space_id=$SPACE_ID" \
				-F "relative_paths=$rel_json" -F "root_folder_id=$root" "${args[@]}")
		else
			resp=$(api POST "/api/v1/documents/folder-upload" \
				-F "folder_name=$kb" -F "search_space_id=$SPACE_ID" \
				-F "relative_paths=$rel_json" "${args[@]}")
			root=$(printf '%s' "$resp" | jq -r '.root_folder_id // empty')
			[ -n "$root" ] || die "folder-upload did not return a root_folder_id: $resp"
		fi
		printf '\r  uploaded %s/%s   ' "$i" "$n" >&2
	done
	printf '\r\033[K' >&2

	# Drop documents for files that no longer exist on disk.
	all_json=$(jq -nc '$ARGS.positional' --args "${files[@]#"$dir"/}")
	api POST "/api/v1/documents/folder-sync-finalize" -H 'Content-Type: application/json' \
		-d "$(jq -nc --arg f "$kb" --argjson s "$SPACE_ID" --argjson r "$root" --argjson p "$all_json" \
			'{folder_name:$f, search_space_id:$s, root_folder_id:$r, all_relative_paths:$p}')" >/dev/null

	# A folder must be fully indexed before it can answer anything.
	wait_ready "$root" "$n"
}

# Accepts either the KB label or a path (whose basename is the label).
remove_folder() {
	local raw=$1 kb fid
	kb=${raw%/}; kb=${kb##*/}
	fid=$(watched_id "$kb")
	[ -n "$fid" ] || { warn "not a tracked folder: '$kb' (see /folders)"; return 1; }
	delete_folder "$fid" && info "removed '$kb'"
}

# ------------------------------------------------------- cross-user sharing
#
# All the logic lives in the backend: /share mints a token against a folder in
# this user's space, /import redeems one into it. The client never sees the
# other user's document ids, and never does retrieval itself.
#
# These use _raw rather than api() because a bad token is a normal outcome in a
# REPL — api() calls die() on non-2xx, which would kill the session.

# soft_api always prints the response body and returns 0 on 2xx, 1 otherwise.
# It must print the body on failure too: callers run it in a command
# substitution, so the HTTP_CODE/BODY globals it sets are lost with the subshell
# and the caller can only see what was written to stdout.
soft_api() {
	_raw "$@" || { warn "network error on $1 $2 (is $SS up?)"; return 1; }
	if { [ "$HTTP_CODE" = 401 ] || [ "$HTTP_CODE" = 403 ]; } && [ "$RELOGGED" = 0 ]; then
		RELOGGED=1; login
		_raw "$@" || { warn "network error on $1 $2"; return 1; }
	fi
	printf '%s' "$BODY"
	case "$HTTP_CODE" in
		2*) return 0 ;;
		*)  return 1 ;;
	esac
}

# Pull FastAPI's {"detail": ...} out of an error body, falling back to the raw
# body when it isn't JSON (a 500 HTML page, an empty response).
api_error() { printf '%s' "$1" | jq -r '.detail // .' 2>/dev/null || printf '%s' "$1"; }

# /share names a folder in the *knowledge base*, not on disk. But /add takes a
# local path, so a local path is exactly what people type here — accept it and
# map it to the KB label the way /rm already does.
kb_path() {
	local raw=$1
	raw=${raw%/}
	case "$raw" in
		/*|./*|../*|~*) printf '%s' "${raw##*/}" ;;
		*)              printf '%s' "$raw" ;;
	esac
}

share_folder() {
	local given=$1 path resp token
	path=$(kb_path "$given")
	[ "$path" = "$given" ] || info "sharing KB folder '$path' (from local path '$given')"

	resp=$(soft_api POST "/api/v1/search-spaces/$SPACE_ID/folder-shares" \
		-H 'Content-Type: application/json' \
		-d "$(jq -nc --arg p "$path" '{path: $p}')") || {
		warn "could not share '$path': $(api_error "$resp")"
		warn "note: /share takes a knowledge-base folder, not a disk path — see /folders"
		return 1
	}
	token=$(printf '%s' "$resp" | jq -r '.token')
	printf '\n  Share token for \033[1m%s\033[0m:\n\n    %s\n\n' "$path" "$token"
	printf '  The recipient runs:  /import %s\n' "$token" >&2
	printf '  Revoke any time:     /unshare %s\n\n' "$token" >&2
}

unshare_folder() {
	local token=$1 resp
	resp=$(soft_api DELETE "/api/v1/folder-shares/$token") || {
		warn "could not revoke: $(api_error "$resp")"
		return 1
	}
	info "revoked — importers lose access on their next query"
}

import_folder() {
	local token=$1 resp name
	resp=$(soft_api POST "/api/v1/search-spaces/$SPACE_ID/folder-links" \
		-H 'Content-Type: application/json' \
		-d "$(jq -nc --arg t "$token" '{token: $t}')") || {
		warn "could not import: $(api_error "$resp")"
		return 1
	}
	name=$(printf '%s' "$resp" | jq -r '.folder_name')
	info "imported '$name' — it is now searchable, and readable at /documents/_shared/$name (read-only)"
}

list_folders() {
	local wf
	wf=$(watched_folders)
	if [ "$(printf '%s' "$wf" | jq 'length')" -eq 0 ]; then
		printf '  (no folders)\n'; return
	fi
	printf '%s' "$wf" | jq -r '.[] | "  #\(.id)  \(.name)"'
}

# All document ids across this user's watched folders (for --pin).
all_doc_ids() {
	local roots ids='[]' r sub
	roots=$(watched_folders | jq -r '.[].id')
	for r in $roots; do
		sub=$(subtree_ids "$r")
		ids=$(jq -nc --argjson a "$ids" --argjson b "$(docs_in "$sub" | jq -c '[.[].id]')" '$a + $b | unique')
	done
	printf '%s' "$ids"
}

# ---------------------------------------------------------------- Q&A

# The backend has no "always retrieve" switch: whether search_knowledge_base runs
# is the model's own choice, and it must first delegate to the knowledge_base
# subagent. Weaker models skip that for non-English questions and answer from
# memory. (search_space.qna_custom_instructions looks like the right lever but is
# never read by the agent — it is only ever written.) So we compel it in-band.
build_query() {
	local q=$1
	[ "$FORCE_SEARCH" = 1 ] || { printf '%s' "$q"; return; }
	cat <<EOF
<instructions>
Before answering you MUST search the knowledge base: delegate to the knowledge_base
subagent and call search_knowledge_base at least once. Do not answer from prior
knowledge alone. Never tell the user to check their knowledge base — search it yourself.

The question may not be in English, and the indexed documents may be in a different
language than the question. Search using terms in the language of the documents; if
unsure, search once in the document language and once in English. Then answer in the
same language the user used, citing the retrieved passages.

Answer ONLY from passages returned by search_knowledge_base.

If the search returns nothing relevant, reply with a single short sentence saying the
knowledge base contains no information on the question, in the user's language. Then stop.
In that case you must NOT:
  - list, name, or describe any file, document title, folder, or directory path;
  - report what the knowledge base does or does not contain beyond that one sentence;
  - speculate about the subject from your own knowledge;
  - offer to search the web, or ask the user to upload anything.

Never reveal filenames, folder names, or paths from tool output — including from ls or
any filesystem tool — unless the user explicitly asked which documents exist. Cite
sources by their title only.
</instructions>

<question>
$q
</question>
EOF
}

ask() {
	local q payload pinned sink
	q=$(build_query "$1")
	if [ "$PIN_DOCS" = 1 ]; then
		pinned=$(all_doc_ids)
		payload=$(jq -nc --argjson c "$THREAD_ID" --argjson s "$SPACE_ID" --arg q "$q" --argjson d "$pinned" \
			'{chat_id:$c, search_space_id:$s, user_query:$q, mentioned_document_ids:$d}')
	else
		payload=$(jq -nc --argjson c "$THREAD_ID" --argjson s "$SPACE_ID" --arg q "$q" \
			'{chat_id:$c, search_space_id:$s, user_query:$q}')
	fi

	# Stream deltas live to the terminal while keeping a copy, so we can tell
	# "the model said nothing" apart from "the request errored before answering".
	sink=$(mktemp)
	curl -sN -b "$COOKIES" -X POST "$SS/api/v1/new_chat" \
		-H "Origin: $ORIGIN" -H 'Content-Type: application/json' -d "$payload" </dev/null \
		| sed -n 's/^data: //p' \
		| jq -Rrj --unbuffered 'fromjson? | select(.type == "text-delta") | .delta' \
		| tee "$sink"
	printf '\n'

	if [ ! -s "$sink" ]; then
		warn "no answer streamed — the stream carried no text-delta events."
		warn "re-run that request without the jq pipe to see the raw SSE."
		warn "usual causes: expired cookie (401), or --origin not matching the backend's trusted origin (CSRF)."
	fi
	rm -f "$sink"
}

help_text() {
	cat <<'EOF'
  /add <path> [label]        ingest a folder (skipped if already extracted)
  /reextract <path> [label]  wipe and rebuild a folder's documents
  /rm <label|path>           remove a folder and all its documents
  /folders                   list this user's ingested folders
  /share <kb-folder>         mint a token granting read access to that folder
                             subtree; hand it to another user. Names a KB folder
                             (a label from /folders, e.g. 1_10 or 1_10/sub), not
                             a disk path — though a disk path is accepted and
                             mapped to its basename.
  /unshare <token>           revoke a share; importers lose access next query
  /import <token>            redeem a token: the folder becomes searchable here
                             and readable (read-only) at /documents/_shared/<name>
  /session           show space / thread / model
  /switch <name>     resume or start another session (drops current history
                     from context — do this after /rm, since deleted filenames
                     can linger in the old thread's saved tool output)
  /help              this text
  /exit              quit (or Ctrl-D)
  anything else      asked against your knowledge base
EOF
}

# ---------------------------------------------------------------- main

session_ok || login
ensure_space
ensure_model
ensure_thread

for idx in ${FOLDERS+"${!FOLDERS[@]}"}; do
	ingest "${FOLDERS[$idx]}" "$REEXTRACT" "${LABELS[$idx]}" \
		|| warn "ingest of '${FOLDERS[$idx]}' finished with problems"
done

printf '\n\033[1mSurfSense\033[0m  space #%s  thread #%s  model %s\n' "$SPACE_ID" "$THREAD_ID" "$MODEL_ID"
printf 'Type /help for commands, /exit to quit.\n\n'

while :; do
	printf '\033[1m>\033[0m ' >&2
	IFS= read -r line || { printf '\n'; break; }
	[ -n "${line// /}" ] || continue

	cmd=${line%% *}
	arg=${line#"$cmd"}; arg=${arg# }

	case "$cmd" in
		/exit|/quit) break ;;
		/help)       help_text ;;
		/folders)    list_folders ;;
		/session)    printf '  space #%s  thread #%s (%s)  model %s\n' "$SPACE_ID" "$THREAD_ID" "$SESSION" "$MODEL_ID" ;;
		/switch)     if [ -n "$arg" ]; then SESSION=$arg; ensure_thread
		             else warn "usage: /switch <session-name>"; fi ;;
		/add)        if [ -n "$arg" ]; then ingest "${arg%% *}" 0 "$(printf '%s' "$arg" | cut -s -d' ' -f2-)"; else warn "usage: /add <path> [label]"; fi ;;
		/reextract)  if [ -n "$arg" ]; then ingest "${arg%% *}" 1 "$(printf '%s' "$arg" | cut -s -d' ' -f2-)"; else warn "usage: /reextract <path> [label]"; fi ;;
		/rm)         if [ -n "$arg" ]; then remove_folder "$arg"; else warn "usage: /rm <label|path>"; fi ;;
		/share)      if [ -n "$arg" ]; then share_folder "$arg"; else warn "usage: /share <kb-folder>  (a label from /folders, e.g. /share 1_10)"; fi ;;
		/unshare)    if [ -n "$arg" ]; then unshare_folder "$arg"; else warn "usage: /unshare <token>"; fi ;;
		/import)     if [ -n "$arg" ]; then import_folder "$arg"; else warn "usage: /import <token>"; fi ;;
		/*)          warn "unknown command: $cmd (try /help)" ;;
		*)           ask "$line" ;;
	esac
done
