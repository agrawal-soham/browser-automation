#!/usr/bin/env bash
# One-time setup of Laptop A (Ubuntu): the orchestrator, qwen3:14b and the Nanobrowser fork. Safe to run again after
# a git pull.
#
#   server/setup/setup_laptop_a.sh --laptop-b-ip 192.168.1.20 [--skip-model] [--skip-client]
#
# It installs what is missing, builds the extension, writes ~/privacy-agent/laptop-a.env (read by
# run_orchestrator.sh), checks the files copied from Laptop B and, when Laptop B's worker is running, checks the TLS
# link. Then: server/run_orchestrator.sh
set -euo pipefail

B_IP=""
SKIP_MODEL=0
SKIP_CLIENT=0
TEXT_MODEL="${TEXT_MODEL:-qwen3:14b}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --laptop-b-ip) B_IP="$2"; shift 2 ;;
    --skip-model) SKIP_MODEL=1; shift ;;
    --skip-client) SKIP_CLIENT=1; shift ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

SERVER="$(cd "$(dirname "$0")/.." && pwd)"
REPO="$(cd "$SERVER/.." && pwd)"
TLS="$HOME/privacy-agent/tls/laptop-a"
ENV_FILE="$HOME/privacy-agent/laptop-a.env"

step() { printf '\n\033[36m== %s\033[0m\n' "$1"; }
ok() { printf '   \033[32mOK\033[0m  %s\n' "$1"; }
warn() { printf '   \033[33m!!\033[0m  %s\n' "$1"; }

step "System packages"
missing=()
command -v python3 >/dev/null || missing+=(python3)
python3 -c 'import ensurepip' 2>/dev/null || missing+=(python3-venv)
command -v openssl >/dev/null || missing+=(openssl)
command -v curl >/dev/null || missing+=(curl)
if ((${#missing[@]})); then
  echo "   installing: ${missing[*]} (sudo)"
  sudo apt-get update -qq && sudo apt-get install -y -qq "${missing[@]}"
fi
ok "python3 $(python3 -c 'import platform; print(platform.python_version())'), openssl, curl"

if ! command -v ollama >/dev/null; then
  echo "   installing Ollama (official script, sudo)"
  curl -fsSL https://ollama.com/install.sh | sh
fi
ok "ollama $(ollama --version 2>/dev/null | awk '{print $NF}')"

step "Python packages in server/.venv"
cd "$SERVER"
[[ -x .venv/bin/python ]] || python3 -m venv .venv
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt
ok "packages installed"

step "Decision model $TEXT_MODEL"
if ((SKIP_MODEL)); then warn "skipped (--skip-model)"; else ollama pull "$TEXT_MODEL" && ok "$TEXT_MODEL ready"; fi

step "Extension (client/)"
if ((SKIP_CLIENT)); then
  warn "skipped (--skip-client)"
elif ! command -v node >/dev/null; then
  warn "Node is missing: install Node 24 (e.g. with nvm), then run this again to build client/dist"
else
  cd "$REPO/client"
  corepack enable >/dev/null 2>&1 || true
  corepack pnpm install --frozen-lockfile >/dev/null
  corepack pnpm build >/dev/null
  ok "built $REPO/client/dist  (Chrome: chrome://extensions -> Developer mode -> Load unpacked; Reload after rebuilds)"
fi

step "Link to Laptop B"
mkdir -p "$TLS"
chmod 700 "$HOME/privacy-agent" "$HOME/privacy-agent/tls" "$TLS"
if [[ -z "$B_IP" && -f "$ENV_FILE" ]]; then
  B_IP="$(sed -n 's|^VLM_URL=wss://\([^:]*\):.*|\1|p' "$ENV_FILE")"
fi
if [[ -z "$B_IP" ]]; then
  warn "No --laptop-b-ip given: the orchestrator will run text-only until you run this again with it."
fi
cat > "$ENV_FILE" <<EOF
# Written by server/setup/setup_laptop_a.sh; read by server/run_orchestrator.sh.
TEXT_MODEL=$TEXT_MODEL
VLM_URL=${B_IP:+wss://$B_IP:8765}
VLM_CA_FILE=$TLS/laptop-b.pem
VLM_TOKEN_FILE=$TLS/token
VLM_TIMEOUT_S=60
EOF
chmod 600 "$ENV_FILE"
ok "wrote $ENV_FILE"

have_files=1
for f in laptop-b.pem token; do
  if [[ ! -s "$TLS/$f" ]]; then have_files=0; fi
done
if ((have_files)); then
  chmod 600 "$TLS/token"
  ok "found laptop-b.pem and token from Laptop B"
  if [[ -n "$B_IP" ]]; then
    result="$(timeout 8 openssl s_client -connect "$B_IP:8765" -CAfile "$TLS/laptop-b.pem" </dev/null 2>/dev/null \
      | grep 'Verify return code' || true)"
    case "$result" in
      *"0 (ok)"*) ok "TLS to $B_IP:8765 verified" ;;
      "") warn "no answer from $B_IP:8765: is the worker running on B, the network Private, the firewall rule there?" ;;
      *) warn "TLS check failed ($result): the certificate does not match B's address; rerun setup on B and copy cert.pem again" ;;
    esac
  fi
else
  warn "Copy from Laptop B (made by its setup script):"
  echo "       C:\\privacy-agent\\tls\\cert.pem  ->  $TLS/laptop-b.pem"
  echo "       C:\\privacy-agent\\tls\\token     ->  $TLS/token"
fi

printf '\n\033[32m================ Laptop A is ready ================\033[0m\n'
echo "Start the orchestrator:   $SERVER/run_orchestrator.sh"
echo "Check it:                 curl localhost:8000/health"
echo "In the extension: Settings -> Providers -> OpenAI-compatible, base URL http://localhost:8000/v1, model privacy-agent, Test"
