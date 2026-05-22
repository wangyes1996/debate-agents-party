#!/usr/bin/env bash
# Start a local SearXNG instance on 127.0.0.1:8888 for web_search.py.
#
# First run installs SearXNG into ./.searxng-venv (one-time, ~80 MB).
# Subsequent runs just boot it. No docker, no redis, no API keys.
#
# Usage:
#   ./scripts/run_searxng.sh           # foreground
#   ./scripts/run_searxng.sh --bg      # background, writes ./.searxng.pid + .searxng.log
#   ./scripts/run_searxng.sh --stop    # stop the background instance
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VENV="$ROOT/.searxng-venv"
SRC="$ROOT/.searxng-src"
SETTINGS="$ROOT/.searxng-settings.yml"
PIDFILE="$ROOT/.searxng.pid"
LOGFILE="$ROOT/.searxng.log"
PORT="${SEARXNG_PORT:-8888}"

stop_bg() {
  if [ -f "$PIDFILE" ]; then
    PID=$(cat "$PIDFILE")
    if kill -0 "$PID" 2>/dev/null; then
      echo "stopping SearXNG pid=$PID"
      kill "$PID" || true
      sleep 1
      kill -0 "$PID" 2>/dev/null && kill -9 "$PID" || true
    fi
    rm -f "$PIDFILE"
  else
    echo "no pidfile at $PIDFILE"
  fi
}

if [ "${1:-}" = "--stop" ]; then
  stop_bg
  exit 0
fi

# 1) install on first run
if [ ! -d "$VENV" ] || [ ! -d "$SRC" ]; then
  echo ">> installing SearXNG (first run only) ..."
  python3 -m venv "$VENV"
  # shellcheck disable=SC1090
  source "$VENV/bin/activate"
  pip install --quiet --upgrade pip wheel
  if [ ! -d "$SRC" ]; then
    git clone --depth 1 https://github.com/searxng/searxng.git "$SRC"
  fi
  pip install --quiet --no-build-isolation -r "$SRC/requirements.txt"
  pip install --quiet --no-build-isolation -e "$SRC"
else
  # shellcheck disable=SC1090
  source "$VENV/bin/activate"
fi

# 2) settings file (idempotent)
if [ ! -f "$SETTINGS" ]; then
  SECRET=$(python3 -c "import secrets;print(secrets.token_hex(32))")
  cat > "$SETTINGS" <<YAML
use_default_settings: true
server:
  secret_key: "$SECRET"
  limiter: false
  public_instance: false
  image_proxy: false
  bind_address: "127.0.0.1"
  port: $PORT
search:
  safe_search: 0
  autocomplete: ""
  formats:
    - html
    - json
outgoing:
  request_timeout: 6.0
  max_request_timeout: 10.0
YAML
  echo ">> wrote $SETTINGS"
fi

export SEARXNG_SETTINGS_PATH="$SETTINGS"

# 3) run
if [ "${1:-}" = "--bg" ]; then
  if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    echo ">> already running pid=$(cat "$PIDFILE"). use --stop first."
    exit 0
  fi
  nohup "$VENV/bin/python" -m searx.webapp > "$LOGFILE" 2>&1 &
  echo $! > "$PIDFILE"
  sleep 4
  if curl -sf "http://127.0.0.1:$PORT/" -o /dev/null; then
    echo ">> SearXNG ready on http://127.0.0.1:$PORT (pid=$(cat "$PIDFILE"))"
  else
    echo ">> SearXNG failed to start, see $LOGFILE"; tail -20 "$LOGFILE"
    exit 1
  fi
else
  echo ">> running SearXNG on http://127.0.0.1:$PORT (Ctrl-C to stop)"
  exec "$VENV/bin/python" -m searx.webapp
fi
