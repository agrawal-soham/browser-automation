# Setup: Laptop A (Ubuntu: browser + decider) and Laptop B (Windows: vision model)

```
Laptop A · Ubuntu                                       Laptop B · Windows
Chrome + Nanobrowser fork (client/dist)                 Ollama + Gemma 4 (vision)
orchestrator  :8000 (localhost only)  ── wss :8765 ──▶   vlm_worker (TLS + token)
Ollama + qwen3:14b
```

Both laptops must be on the **same local network**. Only masked images and placeholders cross the link. Laptop B
never sees real personal data, and it never talks to anything except Laptop A.

Both laptops `git pull` this repo. Laptop B uses only `server/`. Laptop B makes its own certificate, so its private
key never leaves it; only the certificate and the shared token are copied to Laptop A.

---

## Fast path: the setup scripts (after `git pull` on both laptops)

**Laptop B (Windows):** in PowerShell, in the repo folder. Use Laptop A's address:
```powershell
git pull
powershell -ExecutionPolicy Bypass -File server\setup\setup_laptop_b.ps1 -Model gemma4 -LaptopAIp 192.168.1.10
```
It asks for administrator rights once. Then it installs Python 3.12 and Ollama if they're missing, builds `server\.venv`,
pulls the model and checks that it takes images, and makes the certificate and token in `C:\privacy-agent\tls`. The
private key stays on B. It also sets the network to Private and opens port 8765 to Laptop A only. Add `-AutoStart` to
start the worker at log-in. At the end it prints B's address and the two files to copy to A.

Start the worker (leave the window open):
```powershell
cd server; powershell -ExecutionPolicy Bypass -File vlm_worker\start_worker.ps1 -Model gemma4
```

**Copy two files from B to A** (USB stick, or `scp` from A if B runs OpenSSH):
`C:\privacy-agent\tls\cert.pem` → `~/privacy-agent/tls/laptop-a/laptop-b.pem`, and
`C:\privacy-agent\tls\token` → `~/privacy-agent/tls/laptop-a/token`. Never copy `key.pem`.

**Laptop A (Ubuntu):**
```bash
git pull
server/setup/setup_laptop_a.sh --laptop-b-ip 192.168.1.20    # B's address, as B's setup printed it
server/run_orchestrator.sh                                    # add LOG_IMAGES=1 in front to keep the masked screenshots
curl localhost:8000/health                                    # "vlm": "gemma4" means the link to B is up
```
`setup_laptop_a.sh` installs what's missing, builds `client/dist`, writes `~/privacy-agent/laptop-a.env` and checks TLS
to B. Run it again any time: after a `git pull`, or after copying the files. Then load `client/dist` in Chrome and add
the provider (2.5).

The sections below are the same steps by hand, with the details and the fixes when something fails.

---

## 0. Before you start

1. **Find the LAN addresses and write them down.**
   - Laptop A (Ubuntu): `ip -4 addr show | grep inet`
   - Laptop B (Windows, PowerShell): `ipconfig`, then read the "IPv4 Address" of the Wi-Fi or Ethernet adapter.

   These steps use `192.168.1.10` (A) and `192.168.1.20` (B); replace them with yours. Give B a fixed address (a DHCP
   reservation in the router), because the certificate names it.
2. **Make Windows treat the network as Private.** Open Settings → Network & internet → Wi-Fi (or Ethernet) → the
   network → *Private network*. On a Public network Windows blocks incoming connections even with a rule.
3. **Check that A can reach B:** run `ping 192.168.1.20` on A. If the ping fails, it can still work: Windows blocks ping
   by default. The real test is in step 2.3.

---

## 1. Laptop B (Windows): the vision model

Every command here is for **PowerShell**. "Admin PowerShell" means: Start → type PowerShell → *Run as administrator*.

### 1.1 Install Ollama, Python and the model

1. Install **Ollama for Windows** from https://ollama.com/download. It runs in the background and listens on
   `localhost:11434`.
2. Install **Python 3.12** from https://www.python.org/downloads/windows/ and tick *Add python.exe to PATH*. Or, in
   PowerShell: `winget install Python.Python.3.12`
3. Pull the Gemma 4 vision model and check its exact name:
   ```powershell
   ollama pull gemma4            # use the tag you chose, e.g. a size variant such as gemma4:<size>
   ollama list                   # copy the NAME column exactly; it is the -Model value below
   ollama run gemma4 "say ok"    # first load; check it answers
   ```
   Gemma 4 must be a variant that takes images; check that `ollama show <tag>` lists *vision* under Capabilities. On
   an 8 GB GPU choose a size that fits. `ollama ps` shows a `CPU/GPU` split when it doesn't.

### 1.2 Get the server code onto B

On Laptop B, make the folders:
```powershell
mkdir C:\privacy-agent\tls
```

Copy `~/Code/harness/server/` from A to `C:\privacy-agent\server\` **without** `.venv`, `logs` and `__pycache__`.
Either way works:
- **USB stick or a shared folder:** on A, run
  `tar czf ~/server.tgz -C ~/Code/harness --exclude=.venv --exclude=logs --exclude=__pycache__ server`.
  Copy `server.tgz` to B, then in PowerShell: `tar -xzf server.tgz -C C:\privacy-agent` (Windows 10/11 include `tar`).
- **scp from B** (needs `sudo apt install openssh-server` on A once):
  `scp -r jayant@192.168.1.10:~/Code/harness/server C:\privacy-agent\`. Afterwards delete the copied
  `C:\privacy-agent\server\.venv` and `logs` if they came along.

Then create the Python environment on B:
```powershell
cd C:\privacy-agent\server
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

### 1.3 Certificate and token (made on Laptop B; only the public part goes to A)

```powershell
cd C:\privacy-agent\server       # or your repo's server folder
.venv\Scripts\python tools\make_worker_cert.py          # detects B's address; or add --ip 192.168.1.20
```
This writes `cert.pem`, `key.pem` and `token` to `C:\privacy-agent\tls`. Copy **cert.pem** and **token** to Laptop A
(`~/privacy-agent/tls/laptop-a/laptop-b.pem` and `.../token`). `key.pem` never leaves B. The certificate is valid for 90
days and names B's address. If the address changes, the script says so: run it with `--force` and copy cert.pem again.

### 1.4 Firewall: only Laptop A may connect

In **Admin PowerShell** on B:
```powershell
New-NetFirewallRule -DisplayName "privacy-agent VLM worker" -Direction Inbound -Protocol TCP `
  -LocalPort 8765 -RemoteAddress 192.168.1.10 -Action Allow -Profile Private
```
The first time Python opens the port, Windows may show its own firewall prompt: allow it for **Private** networks
only.

### 1.5 Start the worker

```powershell
cd C:\privacy-agent\server
.\vlm_worker\start_worker.ps1 -Model gemma4        # the exact NAME from `ollama list`
```
If PowerShell refuses to run scripts, allow local scripts for your user once:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.

The script checks that the three TLS files exist, that `.venv` exists and that Ollama has the model, then starts the
worker. Expected output:
```
VLM worker: model gemma4 on wss://0.0.0.0:8765
... vlm_worker INFO VLM worker on wss://0.0.0.0:8765 with gemma4
```
When A connects, it logs `orchestrator connected from ('192.168.1.10', …)`. Leave this window open.

### 1.6 Start it automatically at log-in (optional)

In **Admin PowerShell**:
```powershell
$action  = New-ScheduledTaskAction -Execute "powershell.exe" `
  -Argument "-NoProfile -ExecutionPolicy Bypass -File C:\privacy-agent\server\vlm_worker\start_worker.ps1 -Model gemma4" `
  -WorkingDirectory "C:\privacy-agent\server"
$trigger = New-ScheduledTaskTrigger -AtLogOn
Register-ScheduledTask -TaskName "privacy-agent VLM worker" -Action $action -Trigger $trigger -RunLevel Limited
```
Ollama must also be running at log-in; its installer sets that up by default.

---

## 2. Laptop A (Ubuntu): browser, orchestrator and the decider

### 2.1 Model

```bash
ollama pull qwen3:14b
```

On an 8 GB GPU, qwen3:14b at a 16k context is ~12 GB and runs about half on the CPU (measured: 8–13 s per step; see
`phase0-findings.md`). Try `TEXT_NUM_CTX=8192` for a smaller footprint.

### 2.2 Extension (the Nanobrowser fork)

Needs Node 24 and pnpm 10.34.7 (through corepack):
```bash
cd ~/Code/harness/client          # the fork, branch privacy-layer
corepack enable
corepack pnpm install
corepack pnpm build               # output: client/dist
```
In Chrome: open `chrome://extensions`, turn on **Developer mode**, click **Load unpacked** and choose
`~/Code/harness/client/dist`. After a rebuild, press **Reload** on the extension's card. If the store version of
Nanobrowser is installed, turn it off.

### 2.3 Check the link to Laptop B

Put the two files from B (step 1.3) in `~/privacy-agent/tls/laptop-a/` as `laptop-b.pem` and `token`. With the worker
running on B:
```bash
openssl s_client -connect 192.168.1.20:8765 -CAfile ~/privacy-agent/tls/laptop-a/laptop-b.pem </dev/null 2>/dev/null \
  | grep "Verify return code"      # must say: 0 (ok)
```
- `Connection refused` or a hang: the Windows firewall (1.4), the network not set to Private (0.2), or the worker isn't running.
- `Verify return code` other than 0: the certificate was made for a different address, so redo 1.3.

### 2.4 Start the orchestrator

```bash
cd ~/Code/harness/server
VLM_URL=wss://192.168.1.20:8765 \
VLM_TOKEN=$(cat ~/privacy-agent/tls/laptop-a/token) \
VLM_CA_FILE=~/privacy-agent/tls/laptop-a/laptop-b.pem \
  .venv/bin/uvicorn orchestrator.main:app --host 127.0.0.1 --port 8000
```

Keep `--host 127.0.0.1`: Chrome is on the same laptop, so nothing else needs to reach port 8000.

Expected log: `VLM link up (gemma4)` (the model name B reports). Then check:
```bash
curl localhost:8000/health
# {"ok":true,"text_model":"qwen3:14b","vlm":"gemma4"}     <- "vlm": null means the link is down
curl localhost:8000/health/vlm
# {"ok":true,"model":"gemma4","ms":20255,...,"expected":271,"answer":"271","correct":true}
#   <- the whole vision route: a test image with a random number goes to Laptop B and the VLM must read it back
```

Add `LOG_IMAGES=1` to save every (masked) screenshot the server receives to `server/logs/images/`: useful for the
demo. Optional settings: `TEXT_MODEL`, `TEXT_NUM_CTX` (8192), `VLM_TIMEOUT_S` (60), `MAX_RETRIES` (2), `SNAPSHOT_CHARS`,
and `LOG_DIR`.

### 2.5 Point Nanobrowser at the orchestrator

In the extension: **Settings → Providers → add provider**
- Kind: **OpenAI-compatible**
- Base URL: `http://localhost:8000/v1`
- API key: anything (for example `local`)
- Model: `privacy-agent`

Press **Test**. The tool-calling test should pass, and the image test is answered by Laptop B. If the image test
fails, the `screenshot` tool is not offered and the agent runs text-only. You can set vision on by hand in the
model's options. Then pick `privacy-agent` as the model in the side panel.

### 2.6 Your "hide this" values (recommended)

Plain names in running text are only hidden once the privacy layer knows them. Until the rules editor is built, add
your own values in the extension's service-worker console (`chrome://extensions` → the extension → "service worker"):
```js
chrome.storage.local.set({ privacyRules: [
  { id: '1', kind: 'NAME', literal: 'Your Full Name' },
  { id: '2', kind: 'ADDRESS', label: 'street' },
]});
```

---

## 3. Check that everything works

1. On A: `curl localhost:8000/health` shows both models.
2. Run a small task in the side panel, for example on a form with fake details.
3. Check that **only placeholders** reached the server:
   ```bash
   grep -c "your-real-email@example.com" ~/Code/harness/server/logs/requests-*.jsonl   # must print 0
   grep -o '\[\[[A-Z]*_[0-9]*\]\]' ~/Code/harness/server/logs/requests-*.jsonl | sort -u
   ```
4. On B, the worker window shows `perceive job … ok in N ms` lines when screenshots are used.
5. The automated end-to-end run (on A, after `corepack pnpm install-chrome` once):
   ```bash
   cd ~/Code/harness/client
   NB_LIVE=1 corepack pnpm autorun eval --args '{"kind":"openai-compatible","model":"privacy-agent",
     "baseUrl":"http://localhost:8000/v1","tasks":["privacy-contact"],"modes":["auto"],"privacy":true,"vision":true,
     "runTimeoutS":900}'
   python3 ~/Code/harness/server/tests/e2e_report.py      # PASS/FAIL, and a scan of the server log for real values
   ```
   On Ubuntu 23.10+ Chrome for Testing needs an AppArmor profile first (`No usable sandbox!`); see
   `phase0-findings.md` → the two commands used on 2026-10-18.

## 4. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| A logs `VLM link down: … CERTIFICATE_VERIFY_FAILED` | the certificate does not name B's current IP, or A has the wrong file | on B run `tools\make_worker_cert.py --force` (1.3) and copy the new cert.pem to A |
| A logs `VLM link down: … 4403 bad token` | the tokens differ | copy the token again; no spaces or newline at the end |
| A logs `… timed out` / `Connection refused` | Windows firewall, network set to Public, wrong IP, or worker not running | on B: `Get-NetFirewallRule -DisplayName "privacy-agent*"`, the network profile (0.2), `netstat -ano \| findstr 8765` |
| `/health` shows `"vlm": null` | the link is down; the agent still runs, text-only | see the log lines above |
| Extension: "Privacy check blocked the request" | the leak check found a value left after redaction | the run stops on purpose; report which kind it names |
| Steps are very slow | qwen3:14b partly on CPU, or Gemma 4 too big for B's GPU | `ollama ps` on each laptop; lower `TEXT_NUM_CTX` on A; a smaller Gemma 4 size on B |
| B: `start_worker.ps1 cannot be loaded … not digitally signed` | PowerShell script policy | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |
| B: `Ollama has no model 'gemma4'` | the tag differs | pass the exact NAME from `ollama list` to `-Model` |
| B: `[Errno 10048] … only one usage of each socket address` | the worker is already running (another window, or the scheduled task) | close it, or `Get-Process python` and stop it |
| A: perceive/verify jobs fail with a schema error | the Gemma 4 variant does not take images, or ignores the JSON format | `ollama show <tag>` must list *vision*; the step then goes on text-only |
| "Test" fails with a CORS / origin error | the base URL is wrong | it must be `http://localhost:8000/v1` |
