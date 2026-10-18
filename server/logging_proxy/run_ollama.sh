#!/usr/bin/env bash
# Run the logging proxy in front of Ollama.
#   ./run_ollama.sh                 -> gemma3:4b-16k (default)
#   ./run_ollama.sh qwen2.5vl:7b    -> any model from `ollama list`
# Whatever model name Nanobrowser sends, the proxy forwards it as $MODEL.
set -euo pipefail
cd "$(dirname "$0")"
MODEL="${1:-gemma3:4b-16k}"

if ! curl -sf -m 2 http://127.0.0.1:11434/api/version >/dev/null; then
  echo "Ollama is not running on :11434. Start it with: sudo systemctl start ollama" >&2
  exit 1
fi
AVAIL="$(ollama list | awk 'NR>1{print $1}')"
MATCH="$(echo "$AVAIL" | grep -x "$MODEL" || echo "$AVAIL" | grep "^${MODEL}:" | head -n1 || echo "$AVAIL" | grep "^${MODEL}" | head -n1 || true)"

if [ -z "$MATCH" ]; then
  echo "Model '$MODEL' not found. Available models in Ollama:" >&2
  echo "$AVAIL" | sed 's/^/  - /' >&2
  exit 1
fi
MODEL="$MATCH"

echo "Using model: $MODEL"
echo "Warming up $MODEL (first load can take a while)..."
curl -s http://127.0.0.1:11434/api/generate -d "{\"model\":\"$MODEL\",\"prompt\":\"hi\",\"stream\":false,\"keep_alive\":\"30m\"}" >/dev/null
ollama ps

export UPSTREAM_URL="http://127.0.0.1:11434/v1"
export UPSTREAM_MODEL="$MODEL"
export TOOL_SHIM="${TOOL_SHIM:-auto}"

echo ""
echo "================================================================"
echo " Proxy ready for Nanobrowser!"
echo "   Anthropic Provider URL: http://localhost:8080"
echo "   OpenAI Provider URL:    http://localhost:8080/v1"
echo "   Viewer UI:              http://localhost:8080/"
echo "   Active Model:           $MODEL"
echo "================================================================"
echo ""
exec .venv/bin/python proxy.py
