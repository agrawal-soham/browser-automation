# `server/setup/setup_laptop_a.sh`

**Responsibility.** One-command setup of Laptop A (Ubuntu), safe to rerun: it installs missing packages and Ollama, builds server/.venv, pulls qwen3:14b, builds client/dist, writes ~/privacy-agent/laptop-a.env (VLM_URL, VLM_CA_FILE, VLM_TOKEN_FILE) and checks TLS to Laptop B once the two files from B are in place.

**In → out.** --laptop-b-ip, --skip-model, --skip-client (TEXT_MODEL from the environment)

**Depends on.** apt, ollama, node/corepack, openssl

**Used by.** docs/setup.md fast path

**Notes and limits.** Tested on Ubuntu against a simulated Laptop B (both before and after the files were copied).
