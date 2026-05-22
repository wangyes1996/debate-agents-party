#!/usr/bin/env bash
# One-shot installer for debate-agents-party.
#
# Installs:
#   1. Backend Python venv (backend/venv) + requirements.txt
#   2. Frontend Node deps (web/node_modules)
#   3. Local SearXNG search engine (.searxng-venv) for agent web search
#   4. config.json from config.example.json if missing
#
# Idempotent — re-running skips anything already present.
#
# Requirements (you must have these on the system):
#   - python3 (>= 3.10) with venv module
#   - node + npm (>= 18)
#   - git, curl

set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '\033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '\033[33m!\033[0m %s\n' "$*"; }
die()  { printf '\033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

# ---- sanity ----
command -v python3 >/dev/null || die "python3 not found. Install Python >= 3.10."
command -v node    >/dev/null || die "node not found. Install Node >= 18."
command -v npm     >/dev/null || die "npm not found."
command -v git     >/dev/null || die "git not found."

PY_VER=$(python3 -c 'import sys;print(f"{sys.version_info.major}.{sys.version_info.minor}")')
bold "==> using python $PY_VER, node $(node -v), npm $(npm -v)"

# ---- 1) backend venv ----
if [ -d backend/venv ]; then
  ok "backend venv exists, skipping"
else
  bold "==> creating backend/venv"
  python3 -m venv backend/venv
  ok "venv created"
fi

bold "==> installing backend requirements"
backend/venv/bin/pip install --upgrade --quiet pip wheel
backend/venv/bin/pip install --quiet -r backend/requirements.txt
ok "backend deps installed"

# ---- 2) frontend deps ----
if [ -d web/node_modules ] && [ -f web/node_modules/.package-lock.json -o -d web/node_modules/express ]; then
  ok "web/node_modules exists, skipping"
else
  bold "==> npm install in web/"
  (cd web && npm install --silent --no-audit --no-fund)
  ok "frontend deps installed"
fi

# ---- 3) SearXNG (one-time clone + install ~80MB) ----
if [ -d .searxng-venv ] && [ -d .searxng-src ]; then
  ok "SearXNG already installed, skipping"
else
  bold "==> installing local SearXNG (one-time, ~80MB, ~30s)"
  ./scripts/run_searxng.sh --install-only 2>/dev/null || {
    # run_searxng.sh has no --install-only flag; fall back to triggering
    # the install path by running with --bg then immediately stopping.
    ./scripts/run_searxng.sh --bg
    ./scripts/run_searxng.sh --stop
  }
  ok "SearXNG installed"
fi

# ---- 4) config.json ----
if [ -f config.json ]; then
  ok "config.json exists, leaving alone"
elif [ -f config.example.json ]; then
  cp config.example.json config.json
  ok "copied config.example.json → config.json"
  warn "edit config.json or use the UI at http://localhost:3000/config to add your LLM API key"
else
  warn "no config.example.json found — you'll create config via the UI on first run"
fi

# ---- done ----
echo
bold "==> setup complete"
echo
echo "Next:"
echo "  ./start.sh        # start backend + frontend + SearXNG"
echo "  ./stop.sh         # stop everything"
echo
echo "Then open http://localhost:3000 and add your LLM API key under /config."
