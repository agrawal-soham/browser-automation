# Server: orchestrator (Laptop A) and VLM worker (Laptop B)

**Two laptops:** `server/setup/setup_laptop_b.ps1` (Windows) and `server/setup/setup_laptop_a.sh` (Ubuntu); see [`../docs/setup.md`](../docs/setup.md). Plain-English overview: [`../docs/how-it-works.md`](../docs/how-it-works.md). Per-file notes: [`../docs/files/`](../docs/files/README.md).

## Install (both laptops)

```bash
cd server
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Laptop B: the VLM worker

```bash
ollama pull qwen2.5vl:7b
# A certificate for Laptop B's LAN address (mkcert, or self-signed with openssl):
openssl req -x509 -newkey rsa:2048 -nodes -days 30 -subj "/CN=laptop-b" \
  -addext "subjectAltName=IP:192.168.1.20,DNS:laptop-b" -keyout key.pem -out cert.pem
export VLM_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"   # share with Laptop A
.venv/bin/python -m vlm_worker.worker --cert cert.pem --key key.pem --port 8765
```

Copy `cert.pem` (never `key.pem`) and the token to Laptop A. Open port 8765 to the LAN only.

## Laptop A: the orchestrator

```bash
ollama pull qwen3:8b
export VLM_URL=wss://192.168.1.20:8765 VLM_TOKEN=... VLM_CA_FILE=/path/to/cert.pem
.venv/bin/uvicorn orchestrator.main:app --host 0.0.0.0 --port 8000
curl localhost:8000/health        # {"ok":true,"text_model":"qwen3:8b","vlm":"qwen2.5vl:7b"}
curl localhost:8000/health/vlm    # the VLM reads a random number off a test image: "correct": true, and how long it took
```

Without `VLM_URL`, the orchestrator runs text-only. To test on one machine without TLS, start the worker with
`--insecure` and set `VLM_URL=ws://localhost:8765 VLM_ALLOW_INSECURE=1`.

Settings (environment variables, see `orchestrator/config.py`): `OLLAMA_URL`, `TEXT_MODEL` (default `qwen3:8b`),
`TEXT_NUM_CTX`, `VLM_TIMEOUT_S`, `MAX_RETRIES`, `SNAPSHOT_CHARS`, `LOG_DIR`, and for site memory `SITE_MEMORY`
(`replay` | `hints` | `off`), `MEMORY_DIR`, `MEMORY_MATCH`, `EMBED_MODEL` (default `nomic-embed-text`; without it,
playbooks are ranked by word overlap).

### Site memory

A run that ends in a success the VLM confirmed is kept as a playbook for that site (`orchestrator/memory.py`,
`memory/memory.sqlite3`): the task as a template and each step by element name, never your values. A later run of a
task that fits the template repeats the steps while the page matches, without asking the text model; otherwise the
steps go into the prompt as hints. `python -m orchestrator.memory list | show <host> | forget <host>`.

### UI-TARS on Laptop B

Start the worker with `VLM_MODEL=hf.co/mradermacher/UI-TARS-1.5-7B-GGUF:Q4_K_M` (now the default). At start-up it
checks which coordinates the model answers in (`vlm_worker/grounding.py`) and then offers `locate` jobs: a click by
description takes one call instead of the two-step grid. `VLM_COORDS` skips the check (`off` disables locate).

## Point Nanobrowser at it

In the fork's Settings → Providers, add an **OpenAI-compatible** provider:
- base URL `http://<laptop A>:8000/v1`
- any API key
- model `privacy-agent`

Then run "Test". The orchestrator answers the tool-calling probe itself, and it answers the image probe with the VLM
when the link is up.

## Tests

```bash
.venv/bin/python -m pytest                      # unit tests + real wss/TLS link tests (needs openssl)
.venv/bin/python -m tests.smoke_text_model 5    # real qwen3:8b: seconds per step and validity
```

Every request is logged to `logs/requests-<date>.jsonl` (without images).
