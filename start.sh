#!/usr/bin/env bash
# Start all three services for debate-agents-party.
#   - SearXNG    on :8888   (local search engine, started in background)
#   - Backend    on :8000   (FastAPI / uvicorn, background)
#   - Frontend   on :3000   (Node/Express static + proxy, FOREGROUND)
#
# Logs go to ./.run/*.log. PIDs go to ./.run/*.pid.
# Ctrl-C in this terminal stops the frontend and then everything else.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
mkdir -p .run

BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${PORT:-3000}"
SEARXNG_PORT="${SEARXNG_PORT:-8888}"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '\033[32m✓\033[0m %s\n' "$*"; }
die()  { printf '\033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

[ -d backend/venv ]      || die "backend/venv missing — run ./setup.sh first"
[ -d web/node_modules ]  || die "web/node_modules missing — run ./setup.sh first"

# ---- SearXNG ----
if [ -f .searxng.pid ] && kill -0 "$(cat .searxng.pid)" 2>/dev/null; then
  ok "SearXNG already running (pid=$(cat .searxng.pid))"
else
  bold "==> starting SearXNG on :$SEARXNG_PORT"
  ./scripts/run_searxng.sh --bg
fi

# ---- Backend ----
if [ -f .run/backend.pid ] && kill -0 "$(cat .run/backend.pid)" 2>/dev/null; then
  ok "backend already running (pid=$(cat .run/backend.pid))"
else
  bold "==> starting backend on :$BACKEND_PORT"
  nohup backend/venv/bin/uvicorn backend.main:app \
    --host 0.0.0.0 --port "$BACKEND_PORT" \
    > .run/backend.log 2>&1 &
  echo $! > .run/backend.pid
  # wait for ready
  for i in $(seq 1 30); do
    if curl -sf "http://127.0.0.1:$BACKEND_PORT/api/health" -o /dev/null 2>/dev/null; then
      ok "backend ready (pid=$(cat .run/backend.pid))"
      break
    fi
    sleep 0.5
    [ "$i" = 30 ] && { tail -20 .run/backend.log; die "backend failed to start"; }
  done
fi

# ---- Frontend (foreground so Ctrl-C is intuitive) ----
trap 'echo; bold "==> stopping…"; ./stop.sh; exit 0' INT TERM

bold "==> starting frontend on :$FRONTEND_PORT (Ctrl-C to stop all)"
echo
echo "  Open  http://localhost:$FRONTEND_PORT"
echo "  Logs  .run/backend.log  .searxng.log"
echo
cd web && PORT="$FRONTEND_PORT" exec node server.js
