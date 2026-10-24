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
curl localhost:8000/health        # {"ok":true,"text_model":"qwen3:8b","vlm":"gemma4:latest"}
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

The same SQLite database also stores observed failures (`failure_memory.py`), including browser error codes,
invalid/repeated reads, and screenshots without pixels. Lessons survive restarts and match the exact URL and
snapshot state with target identity resolved from the current outline, never an old ref. Known failed reads are
blocked; known failed clicks use the recovery ladder instead of replaying the direct click. Missing screenshot
pixels skip vision/grid recovery and use the keyboard fallback. All actions still pass the extension's permissions.

Failure lessons contain only site hosts, hashed page/target/event identities, tool names, and error codes. They do
not store page text, contact names, typed values, images, or model explanations. Lessons expire after 24 hours and
are bounded to 1,000 entries (10,000 deduplication events); changed page states are evaluated afresh. A confirmed
success clears the matching failure. `python -m orchestrator.memory lessons <host>` inspects error summaries;
`forget <host>` clears both playbooks and failure lessons. `SITE_MEMORY=off` disables both stores.
An empty screenshot lesson is advisory across runs: it never suppresses a fresh capture in a later request. A
current-state capture without pixels still stops repeated capture/vision attempts until the observed state changes.

### Current-task page memory

`orchestrator/page_memory.py` rebuilds a ledger of visited URLs, observations, and selected-item context from the
current request's tool history. Listing evidence follows the clicked item onto its detail page; snapshots no longer
erase earlier evidence from the decision prompt. URLs retain path case, queries, and fragments. Old refs are removed
from historical excerpts, and failed reads are not evidence.

The decision model can checkpoint requirements with `progress.facts` (criterion, subject URL, source ID, exact quote),
`progress.target_url`, and `progress.complete`. Quotes and subject URLs are checked against recorded observations;
whether a quote actually satisfies a requirement remains a model judgment. Final success still uses the existing
vision verification when enabled. Returning to an inspected page, leaving a confirmed target, or continuing to browse
after complete progress requires `revisit_reason`; explicit returns and changed page states remain possible.

Progress checkpoints are process-local, expire after two hours, and are capped at 128 tasks. They are isolated by task
text, the first tool-call ID, and a matching history prefix. A restart or history compaction can lose checkpoints;
available observations are rebuilt without carrying unsupported claims forward. Logs include a `page_memory` summary
with source IDs and progress for diagnosing navigation decisions. No new database or dependency is needed.

If a screenshot returns without image data, the agent records a warning, continues with text evidence, and avoids
requesting that same screenshot indefinitely. Images attached to screenshot tool results are accepted as well as user
image messages. An older screenshot is cleared when a newer screenshot has no pixels. This does not bypass the
extension's masking or image-withholding policy; missing images cannot produce visually verified success.

### Read when needed

Search requests also have an explicit action checkpoint: `not_entered` → `query_entered` → `search_submitted` →
`results_observed`. Entry requires a confirmed input receipt; submission requires the search action to react;
completion requires a subsequent results snapshot. Suggestions alone do not count. A site replacing the original
query with a resolved place name does not undo the search. Search-only requests acknowledge success from this
browser evidence without requiring another model decision or unavailable screenshot. Requests with remaining
work retain the checkpoint and continue that work instead. Logs expose `action_checkpoint` on each decision and
`completion_checkpoint` when the search finishes. Checkpoints use only the current request's history.

`action_confidence` records what the browser has confirmed for recent actions: dispatched, page reacted, expected
state read back, or destination observed in a subsequent snapshot. Confidence attaches to that outcome, not to
every remaining requirement. Completed searches cannot be resubmitted with a click or Enter unless a reason for
revisiting is given. Recovery failures reset on navigation caused by clicks/keys as well as explicit `navigate`.

For YouTube search-and-play requests, the selected result has a separate checkpoint for destination opened,
paused, waiting for an ad, and content playing. Known first-result selection plus a matching video page and an
enabled Pause control in its player confirms playback; ad, buffering, and failure indicators prevent that claim.
Player actions are observed before another toggle. Non-skippable ads wait in five-second intervals with fresh
snapshots, capped at one minute; the agent stays on the confirmed video. Missing evidence stays unconfirmed.

The planner acts from the current snapshot and requests `read` only when it needs more evidence. Requested reads
use 6,000-character Markdown chunks. The latest chunk is prioritized in the next decision prompt so its tail is
not lost behind short historical excerpts. The planner can retain source-quoted findings as progress before
requesting another chunk. There is no separate reading-review call that can terminate a task over invalid optional
facts. `find` obtains current refs for candidates discovered outside the snapshot window.

Each page logs a separate `reading` record: status (`not_read`, `partially_read`, `fully_read`), character count,
chunk count, continuation offset, and any problem. A new snapshot, navigation, or page action invalidates coverage.
Read counts and offsets are parsed outside the untrusted page-content boundary; scoped, truncated, gapped, or failed
reads cannot mark a page fully read. Failures do not retry indefinitely or justify an exhaustive negative result.
The server rejects an identical read in an unchanged observed state immediately, and continuation offsets must
come from an intact prior read's metadata, rather than character counts altered by redaction.
Reads are capped at 64 attempts per snapshot, including scoped or rejected chunks; a larger page retains partial
coverage. The model cannot restart reads after this budget is exhausted. If the active URL changes without a
navigation receipt, old observations stay bound to their original URL and the planner receives an incomplete-coverage
warning instead of restarting at offset zero. Browsers without `read` retain the existing
snapshot path. Reading covers the available document text, not lazy-loaded content, inaccessible frames, or ratings
encoded only in CSS/images; those still require additional observation or vision.
When a literal `find` returns no matches, the server requests a fresh labelled screenshot for task-specific visual
inspection if the VLM is connected and pixels are available. A repeated identical empty lookup is rejected. The
vision prompt asks for visible item names, ratings, prices, and labels rather than treating missing text as absence.

### Gemma 4 on Laptop B

The worker's default model is `gemma4` (`start_worker.ps1 -Model gemma4`; the worker reports the exact tag, e.g.
`gemma4:latest`, in the orchestrator's "VLM link up" log line). Measured on 2026-10-24 over the live link:

- A page description (`perceive`) takes 17-21 s whatever the image size (512-1600 px) or answer length; 512 px made it
  invent "filled" fields, so `shrink` stays at 1024. Plain `chat` answers take 4-10 s. Look at fewer pages, not at
  smaller pictures (`PERCEIVE=pages`, the default).
- `verify` of a marked point click was right on 5 of 5 cases (right spot, wrong button, wrong field, empty area).
- Pointing answers `{"point_2d": [x, y]}` in 0-1000 fractions: right in y, but 70-300 px off in x on three of four
  targets. At start-up the worker checks the answer on a picture with one button (`vlm_worker/grounding.py`, gemma
  accepts only `norm1000`); if it misses, no `locate` job is offered and clicks by description use the grid.
  `VLM_COORDS=norm1000` forces `locate` on, `off` disables it.

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
.venv/bin/python -m tests.smoke_search_flow    # real model over recorded-style YouTube states; no browser input
.venv/bin/python -m tests.smoke_failure_recovery # real model over failed reads/clicks and persistent memory; sends nothing
.venv/bin/python -m tests.smoke_confirmed_progress # real model over paused/ad/playing video states; no browser input
```

Every request is logged to `logs/requests-<date>.jsonl` (without images).
Logs also include the latest tool receipt and up to eight browser errors, each capped at 1,200 already-redacted
characters. This preserves actual click/read failures without dumping the entire conversation.

For listing comparison, collection, and attribute-based selection, the server builds a current-page JSON object
with viewport ranges, text excerpts, source-bound visual observations, and separate text/visual coverage gaps.
It scrolls with 20% overlap, refreshes the outline, and describes each section before asking the planner to choose.
Simple form actions and search submission do not require scanning the page. Scans stop after 12 scrolls or when
scrolling stops advancing; missing images, changing page heights, and truncated text remain explicit limitations,
not evidence of absence. Clicks, typing, and navigation invalidate coverage for the previous page state. Visual
evidence persists across requests in the existing task-scoped progress cache, with URL, state, and viewport binding.
The `page_coverage` log field records inspected ranges and remaining gaps without copying page text or images.
Every planner-issued scroll also refreshes the snapshot. A fresh snapshot at a document edge blocks further
scrolling in that direction and asks the planner to choose an item, retrieve an earlier control, or click the
actual pagination link. Once scanning is complete, a unique native sidebar category explicitly named in the task
can be found and clicked deterministically through the normal command checks. It uses current refs and skips a
category whose heading is already open. `.venv/bin/python -m tests.smoke_completed_scan` checks this handoff; the
local model remains available as a fallback when no unique native category is identified.
