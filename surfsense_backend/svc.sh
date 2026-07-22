#!/usr/bin/env bash
# svc.sh - start/stop/inspect the 4 background services.
#
#   ./svc.sh --start --deepseek sk-...
#   ./svc.sh --start --vllm Qwen2.5-7B --vllm_port 8001
#   ./svc.sh --stop
#   ./svc.sh --status
#   ./svc.sh --logs worker -f
#
# Services: worker, beat, main, serve

set -uo pipefail

# ---------------------------------------------------------------- config ----
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="${RUN_DIR:-$ROOT/.run}"
LOG_DIR="${LOG_DIR:-$ROOT/logs}"
ENV_FILE="$RUN_DIR/env"

DEFAULT_Q="${CELERY_TASK_DEFAULT_QUEUE:-surfsense}"
GPU="${CUDA_VISIBLE_DEVICES:-2}"
SERVE_PORT="${SERVE_PORT:-39317}"

# Locate surfsense_backend whether this script sits in the repo root or
# inside the backend itself. Override with BACKEND_DIR=... if neither fits.
if [[ -z "${BACKEND_DIR:-}" ]]; then
  if   [[ -f "$ROOT/main.py" ]];                    then BACKEND_DIR="$ROOT"
  elif [[ -f "$ROOT/surfsense_backend/main.py" ]];  then BACKEND_DIR="$ROOT/surfsense_backend"
  else BACKEND_DIR="$ROOT/surfsense_backend"
  fi
fi
WEB_DIR="${WEB_DIR:-$BACKEND_DIR/scripts/web}"

SERVICES=(worker beat main serve)

mkdir -p "$RUN_DIR" "$LOG_DIR"

# Working directory for each service.
dir_for() {
  case "$1" in
    worker|beat|main) echo "$BACKEND_DIR" ;;
    serve)            echo "$WEB_DIR" ;;
    *)                return 1 ;;
  esac
}

# The command line for each service.
cmd_for() {
  case "$1" in
    worker) echo "uv run celery -A celery_worker.celery_app worker --loglevel=info --concurrency=1 --pool=solo --queues=${DEFAULT_Q},${DEFAULT_Q}.connectors,${DEFAULT_Q}.gateway" ;;
    beat)   echo "uv run celery -A celery_worker.celery_app beat --loglevel=info" ;;
    main)   echo "uv run main.py" ;;
    serve)
      if [[ "${SERVE_MODE:-deepseek}" == "vllm" ]]; then
        echo "uv run python serve.py ${SERVE_PORT} --vllm ${VLLM_MODEL:-} --vllm_port ${VLLM_PORT:-}"
      else
        echo "uv run python serve.py ${SERVE_PORT} --deepseek ${DEEPSEEK_API_KEY:-}"
      fi ;;
    *)      return 1 ;;
  esac
}

# Services that need the GPU pinned.
needs_gpu() { [[ "$1" != "serve" ]]; }

# ----------------------------------------------------------------- utils ----
c_red=$'\033[31m'; c_grn=$'\033[32m'; c_yel=$'\033[33m'; c_dim=$'\033[2m'; c_off=$'\033[0m'
log()  { printf '%s\n' "$*" >&2; }
die()  { printf '%serror:%s %s\n' "$c_red" "$c_off" "$*" >&2; exit 1; }

pidfile() { echo "$RUN_DIR/$1.pid"; }
logfile() { echo "$LOG_DIR/$1.log"; }

is_running() {
  local pf; pf="$(pidfile "$1")"
  [[ -f "$pf" ]] || return 1
  local pid; pid="$(cat "$pf" 2>/dev/null)"
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

valid_service() {
  local s
  for s in "${SERVICES[@]}"; do [[ "$s" == "$1" ]] && return 0; done
  return 1
}

# ----------------------------------------------------------------- start ----
start_one() {
  local name="$1"
  if is_running "$name"; then
    log "${c_yel}already running${c_off}  $name (pid $(cat "$(pidfile "$name")"))"
    return 0
  fi

  local cmd wd lf
  cmd="$(cmd_for "$name")" || die "unknown service: $name"
  wd="$(dir_for "$name")"
  lf="$(logfile "$name")"

  [[ -d "$wd" ]] || die "$name: working dir not found: $wd (set BACKEND_DIR=...)"

  {
    echo
    echo "=================================================================="
    echo "starting $name at $(date -Is)"
    echo "cwd: $wd"
    echo "cmd: $cmd"
    echo "=================================================================="
  } >>"$lf"

  # setsid puts the process in its own process group so we can signal the
  # whole tree (uv run spawns children) on stop.
  if needs_gpu "$name"; then
    CUDA_VISIBLE_DEVICES="$GPU" setsid nohup bash -c "cd '$wd' && exec $cmd" >>"$lf" 2>&1 &
  else
    setsid nohup bash -c "cd '$wd' && exec $cmd" >>"$lf" 2>&1 &
  fi
  local pid=$!
  echo "$pid" >"$(pidfile "$name")"

  sleep 1
  if is_running "$name"; then
    log "${c_grn}started${c_off}        $name (pid $pid) -> $lf"
  else
    log "${c_red}failed${c_off}         $name - last lines of $lf:"
    tail -n 15 "$lf" >&2
    rm -f "$(pidfile "$name")"
    return 1
  fi
}

# ------------------------------------------------------------------ stop ----
stop_one() {
  local name="$1" pf pid
  pf="$(pidfile "$name")"

  if ! is_running "$name"; then
    [[ -f "$pf" ]] && rm -f "$pf"
    log "${c_dim}not running${c_off}    $name"
    return 0
  fi

  pid="$(cat "$pf")"
  # Negative pid = whole process group.
  kill -TERM "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null

  local i
  for i in {1..30}; do
    is_running "$name" || break
    sleep 0.5
  done

  if is_running "$name"; then
    log "${c_yel}force killing${c_off}  $name (pid $pid)"
    kill -KILL "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null
    sleep 1
  fi

  rm -f "$pf"
  log "${c_grn}stopped${c_off}        $name"
}

# ---------------------------------------------------------------- status ----
status() {
  printf '%-10s %-12s %-8s %-10s %s\n' SERVICE STATUS PID UPTIME LOG
  local name pid up rss
  for name in "${SERVICES[@]}"; do
    if is_running "$name"; then
      pid="$(cat "$(pidfile "$name")")"
      up="$(ps -o etime= -p "$pid" 2>/dev/null | tr -d ' ')"
      printf '%-10s %b%-12s%b %-8s %-10s %s\n' \
        "$name" "$c_grn" running "$c_off" "$pid" "${up:-?}" "$(logfile "$name")"
    else
      printf '%-10s %b%-12s%b %-8s %-10s %s\n' \
        "$name" "$c_red" stopped "$c_off" - - "$(logfile "$name")"
    fi
  done
}

# ------------------------------------------------------------------ logs ----
show_logs() {
  local follow=0 lines=200 targets=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -f|--follow) follow=1; shift ;;
      -n) lines="$2"; shift 2 ;;
      *) valid_service "$1" || die "unknown service: $1"; targets+=("$1"); shift ;;
    esac
  done
  [[ ${#targets[@]} -eq 0 ]] && targets=("${SERVICES[@]}")

  local files=() t
  for t in "${targets[@]}"; do files+=("$(logfile "$t")"); done
  for t in "${files[@]}"; do [[ -f "$t" ]] || : >"$t"; done

  if [[ $follow -eq 1 ]]; then
    tail -n "$lines" -f "${files[@]}"
  else
    tail -n "$lines" "${files[@]}"
  fi
}

# ----------------------------------------------------------------- usage ----
usage() {
  cat <<EOF
usage: $(basename "$0") <command> [options] [service...]

commands:
  --start [model flags] [service...]      start services in the background
  --stop  [service...]                    stop them (SIGTERM, then SIGKILL)
  --restart [model flags] [service...]    stop + start
  --status | --list                       show what's running
  --logs [service...] [-f] [-n N]         tail logs (-f to follow)

model flags (pick one backend):
  --deepseek KEY                          DeepSeek API
  --vllm MODEL --vllm_port PORT           local vLLM container (OpenAI endpoint);
                                          --deepseek wins when both are given

services: ${SERVICES[*]}   (default: all)

working dirs:
  worker,beat,main  $BACKEND_DIR
  serve             $WEB_DIR
  (override with BACKEND_DIR=... / WEB_DIR=...)

notes:
  The model flags are only needed for 'serve'. They are cached in
  $ENV_FILE (chmod 600) so --restart works without repeating them.
  You can also export DEEPSEEK_API_KEY, or VLLM_MODEL + VLLM_PORT, instead.

examples:
  $(basename "$0") --start --deepseek sk-abc123
  $(basename "$0") --start --vllm Qwen2.5-7B-Instruct --vllm_port 8001
  $(basename "$0") --restart worker
  $(basename "$0") --logs serve -f
  $(basename "$0") --stop
EOF
}

# ------------------------------------------------------------------ main ----
[[ $# -eq 0 ]] && { usage; exit 1; }

CMD="$1"; shift

# Pull the model flags out of the args wherever they appear. A flag passed on
# this invocation also picks the serve mode; --deepseek wins over --vllm.
ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --deepseek) DEEPSEEK_API_KEY="${2:-}"; PASSED_MODE="deepseek"; shift 2 ;;
    --deepseek=*) DEEPSEEK_API_KEY="${1#*=}"; PASSED_MODE="deepseek"; shift ;;
    --vllm) VLLM_MODEL="${2:-}"; PASSED_VLLM=1; shift 2 ;;
    --vllm=*) VLLM_MODEL="${1#*=}"; PASSED_VLLM=1; shift ;;
    --vllm_port|--vllm-port) VLLM_PORT="${2:-}"; shift 2 ;;
    --vllm_port=*|--vllm-port=*) VLLM_PORT="${1#*=}"; shift ;;
    *) ARGS+=("$1"); shift ;;
  esac
done
[[ -z "${PASSED_MODE:-}" && -n "${PASSED_VLLM:-}" ]] && PASSED_MODE="vllm"

# Load the cached config, without letting it override what was just passed.
if [[ -f "$ENV_FILE" ]]; then
  _dk="${DEEPSEEK_API_KEY:-}" _vm="${VLLM_MODEL:-}" _vp="${VLLM_PORT:-}"
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  [[ -n "$_dk" ]] && DEEPSEEK_API_KEY="$_dk"
  [[ -n "$_vm" ]] && VLLM_MODEL="$_vm"
  [[ -n "$_vp" ]] && VLLM_PORT="$_vp"
fi
SERVE_MODE="${PASSED_MODE:-${SERVE_MODE:-}}"
if [[ -z "$SERVE_MODE" ]]; then
  # Legacy cache or env vars only: infer from what we have.
  if [[ -n "${DEEPSEEK_API_KEY:-}" ]]; then SERVE_MODE="deepseek"
  elif [[ -n "${VLLM_MODEL:-}" ]];    then SERVE_MODE="vllm"
  fi
fi

# Persist everything we now know, so --restart works without repeating flags.
if [[ -n "${DEEPSEEK_API_KEY:-}" || -n "${VLLM_MODEL:-}" ]]; then
  umask 077
  {
    echo "SERVE_MODE=${SERVE_MODE}"
    [[ -n "${DEEPSEEK_API_KEY:-}" ]] && echo "DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY}"
    [[ -n "${VLLM_MODEL:-}" ]] && echo "VLLM_MODEL=${VLLM_MODEL}"
    [[ -n "${VLLM_PORT:-}" ]] && echo "VLLM_PORT=${VLLM_PORT}"
  } >"$ENV_FILE"
fi

# Resolve target services (logs handles its own arg parsing).
TARGETS=()
if [[ "$CMD" == "--logs" || "$CMD" == "logs" ]]; then
  TARGETS=()
elif [[ ${#ARGS[@]} -gt 0 ]]; then
  for a in "${ARGS[@]}"; do
    valid_service "$a" || die "unknown service '$a' (valid: ${SERVICES[*]})"
    TARGETS+=("$a")
  done
else
  TARGETS=("${SERVICES[@]}")
fi

case "$CMD" in
  --start|start)
    for s in "${TARGETS[@]}"; do
      if [[ "$s" == "serve" ]]; then
        case "$SERVE_MODE" in
          deepseek)
            [[ -n "${DEEPSEEK_API_KEY:-}" ]] || \
              die "serve needs a key: pass --deepseek sk-... or export DEEPSEEK_API_KEY" ;;
          vllm)
            [[ -n "${VLLM_MODEL:-}" ]] || die "serve needs a model: pass --vllm <model_name>"
            [[ -n "${VLLM_PORT:-}" ]] || \
              die "serve needs the vLLM port: pass --vllm_port <port> (the vLLM container's OpenAI port)" ;;
          *)
            die "serve needs a model backend: --deepseek sk-... or --vllm <model_name> --vllm_port <port>" ;;
        esac
      fi
    done
    for s in "${TARGETS[@]}"; do start_one "$s"; done
    echo; status
    ;;
  --stop|stop)
    # reverse order
    for (( i=${#TARGETS[@]}-1; i>=0; i-- )); do stop_one "${TARGETS[i]}"; done
    ;;
  --restart|restart)
    for (( i=${#TARGETS[@]}-1; i>=0; i-- )); do stop_one "${TARGETS[i]}"; done
    for s in "${TARGETS[@]}"; do start_one "$s"; done
    echo; status
    ;;
  --status|status|--list|list|--ps)
    status
    ;;
  --logs|logs)
    show_logs "${ARGS[@]+"${ARGS[@]}"}"
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    die "unknown command '$CMD' (try --help)"
    ;;
esac
