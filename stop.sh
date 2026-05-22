#!/usr/bin/env bash
# Stop all three services.
set -u
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

stop_pid() {
  local label="$1" pidfile="$2"
  if [ -f "$pidfile" ]; then
    local pid; pid=$(cat "$pidfile" 2>/dev/null || true)
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      echo "stopping $label (pid=$pid)"
      kill "$pid" 2>/dev/null || true
      sleep 1
      kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$pidfile"
  fi
}

stop_pid backend  .run/backend.pid

# SearXNG has its own stop logic
if [ -f .searxng.pid ]; then
  ./scripts/run_searxng.sh --stop || true
fi

# Any stray uvicorn / node from manual starts
PIDS=$(ps -eo pid,cmd | awk '/backend\.main:app/ && !/awk/ {print $1}' || true)
[ -n "$PIDS" ] && echo "killing stray uvicorn: $PIDS" && kill $PIDS 2>/dev/null || true

PIDS=$(ps -eo pid,cmd | awk '/web\/server\.js/ && !/awk/ {print $1}' || true)
[ -n "$PIDS" ] && echo "killing stray frontend: $PIDS" && kill $PIDS 2>/dev/null || true

echo "done."
