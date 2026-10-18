#!/usr/bin/env bash
# Starts the orchestrator on Laptop A with the settings setup_laptop_a.sh wrote (~/privacy-agent/laptop-a.env).
# Extra settings can be given in front, e.g.:  LOG_IMAGES=1 TEXT_NUM_CTX=8192 server/run_orchestrator.sh
set -euo pipefail
SERVER="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$HOME/privacy-agent/laptop-a.env"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
else
  echo "No $ENV_FILE: run server/setup/setup_laptop_a.sh first (running text-only for now)." >&2
fi
[[ -n "${VLM_URL:-}" ]] || echo "VLM_URL is empty: no Laptop B, so the orchestrator runs text-only." >&2
cd "$SERVER"
# 127.0.0.1 only: Chrome is on this laptop, so nothing else needs port 8000.
exec .venv/bin/uvicorn orchestrator.main:app --host 127.0.0.1 --port "${PORT:-8000}"
