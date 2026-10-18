# Phase 0 findings (from the fork's source and real runs)

Fork: `https://github.com/nanobrowser/nanobrowser.git`, commit `f1e912d`, cloned into `client/`. Our work is on
branch `privacy-layer`.

## Hook points, confirmed in the source

| Hook | Where | What we did |
|---|---|---|
| (a) model client fetch | `client/src/extension/models.ts`: `createModel(spec, { fetch })` (`src/llm/providers.ts` already accepts a custom fetch) | pass `privacyFetch(...)` |
| (b) screenshot capture | `client/src/browser-control/core/tools/observe.ts`: `screenshot()` takes a raw `Page.captureScreenshot` and already paints over secrets with `blankBoxes` (fails closed) | added `SessionConfig.imagePrivacy` to hide personal nodes too and register the final image |
| (c) tool dispatch | `client/src/extension/agent-session.ts`: `layerPort.call`, which every layer tool goes through | swap tags for real values here |
| (d) resize / re-encode | none: the PNG base64 goes from `screenshot` to `agent/loop.ts` (`#screenshotImage`) to `agent/context.ts` to the AI SDK unchanged | exact fingerprint in `image-registry.ts` (option "no re-encode") |

## What the fork already does, which we reuse

- **One agent, 19 tools.** There are no Planner, Navigator or Validator stages (see `AGENTS.md`: "One agent… No model
  tiers"). The `screenshot` tool is offered only to models marked as vision models.
- **Secrets are already masked** in text and images: password fields, card numbers, one-time codes
  (`core/perception/masking.ts`, `core/redact.ts`). Our layer adds names, emails, phones, addresses and IDs as
  placeholders.
- **Consequential actions need a person.** Submit, pay and delete are confirmed in Ask mode, and payments and secrets
  are always handed to the user. We do not duplicate this. The server only adds a VLM check before risky clicks.
- **Provider test.** Nanobrowser sends one `report_status` tool request, then reasoning probes, then an image probe.
  The orchestrator answers all of them (`turn.classify`).
- **Only the newest image is kept** in the history (`agent/context.ts`). Old screenshots are not re-sent.

## qwen3:14b measured on this laptop (RTX 5050 Laptop, 8 GB)

`server/tests/smoke_text_model.py`, compact prompt (~420–450 tokens), `num_ctx 16384`, `think: false`, `format` schema:

| Measure | Result |
|---|---|
| warm seconds per step | 8.4 s (first run), 13.1 s (second run) |
| first call (model load) | 13.6–18.9 s |
| valid commands | 8 / 8 |
| memory | Ollama loads 12 GB at 16k context: **51% CPU / 49% GPU**, so it doesn't fit in 8 GB |
| bug found | the model copies the outline's `«…»` quotes into values (`«[[EMAIL_1]]»`). The check now strips them; the prompt rule alone did not stop it |

Next measurements to take:
- `TEXT_NUM_CTX=8192` (the compact prompt is small, so 8k may be enough and could fit more of the model on the GPU)
- a smaller quant
- a real Google Form run through the extension, for prompt size with a real snapshot

No decision on model tiers is made until those numbers exist.

## End-to-end runs (2026-10-18, one laptop, Chrome for Testing)

The real agent loop, layer and privacy fetch in Chrome → orchestrator → qwen3:14b, with Qwen2.5-VL 7B over TLS wss
(on the same machine). Task `privacy-contact`: fill a fake contact form (name, email, phone given in the task text), then
send it. Reproduce with `docs/setup.md` §3.5, then `python3 server/tests/e2e_report.py`.

| Run | Mode | Result | Real values in server log | What it taught us → fix |
|---|---|---|---|---|
| 1 | text | blocked at the end | 0 | The page echoed the submitted form URL-encoded (`jane.doe%40example.com`, `Jane+Doe`): neither the redactor nor the leak check knew encoded forms. The request was blocked only because a late tag tripped the leak check → encoded forms matched, detectors run on decoded text, redaction repeats until no new tag |
| 2 | text | task FAIL | 0 | qwen3:14b skipped the phone and submitted → the server refuses a submit while a task placeholder is untyped |
| 3 | text | **PASS**, 7 turns, 73 s | 0 | 9–12 s per decision, prompts 0.9k–2k tokens |
| 4 | vision | task FAIL (time) | 0 | The server never asked for a screenshot → it now requests one per new outline and a fresh one before a risky click |
| 5 | vision | task FAIL | 0 | The VLM called masked fields "empty", so qwen3:14b re-filled them; the VLM said a clearly visible button was "not visible" (9 of 9 verify jobs) → labelled screenshots, "a covered field is filled" in the prompts, a "fields you already filled" list, snapshot after ask_user, VLM verify advisory for ref clicks (blocking for point clicks) |
| 6 | vision | **PASS**, 9 turns, 143 s | 0 | perceive 10–12 s, verify ~9 s per job over wss |

Masked screenshots received by the server (with `LOG_IMAGES=1`) show name, email and phone as solid grey boxes, while
the message text and buttons stay readable. The layer reuses its own `blankBoxes` (grey, no tag text in the box).

**Open finding:** Qwen2.5-VL 7B is not a reliable verifier on this page: it said "not visible" for a visible, labelled
button in every verify job. Try a different verify prompt (ask it to read the label text first) or a larger VLM on
Laptop B before relying on it for anything blocking.

**Chrome for Testing on Ubuntu (AppArmor).** It crashes with `No usable sandbox!` until it has a profile like Ubuntu's
own for Chrome:
```
printf '%s\n' 'abi <abi/4.0>,' 'include <tunables/global>' '' 'profile chrome-for-testing /home/<user>/.cache/puppeteer/chrome/*/chrome-linux64/chrome flags=(unconfined) {' '  userns,' '  include if exists <local/chrome-for-testing>' '}' | sudo tee /etc/apparmor.d/chrome-for-testing
sudo apparmor_parser -r /etc/apparmor.d/chrome-for-testing
```
Only needed for the automated tests; your normal Chrome already has its profile.

## Two-laptop run with Gemma 4 (2026-10-18)

Laptop A (Ubuntu): Chrome, the extension, the orchestrator and qwen3:14b. Laptop B (Windows, `10.86.173.192`): the worker
and Gemma 4, set up with `setup_laptop_b.ps1`, over TLS wss.

| Result | Detail |
|---|---|
| Task | **PASS**: 9 turns, 160 s, form filled and sent, outcome success |
| Privacy | 0 real values in the server log; 7 masked screenshots sent, 0 withheld, 0 blocked |
| Gemma 4 perceive | 31.5 s for the first job (model load on B), then 13.8 s |
| Gemma 4 verify | **said yes, correctly**, for the Send click (10.5 s). Qwen2.5-VL 7B said "not visible" in 9 of 9 checks |
| qwen3:14b decide | 11–21 s per step (prompts 1.1k–2.1k tokens) |

The first perception, of the empty form, said the fields were "filled, indicated by solid black boxes". The prompt's
"a box over a field means it is filled" made Gemma 4 read the empty input borders as boxes. It did no harm, because
the decision follows the outline, but the perceive prompt should only mention boxes that are actually there.
Gemma 4's verify answered correctly, so its "no" could block ref clicks again. One run is not enough evidence; decide
after more runs.

## First real use from Chrome: why a Google Forms run stopped (2026-10-18)

Task: "open google form website and make a blank google form having field name and type short answer". The run
stopped after clicking "Blank form": the server answered every request, but Chrome sent no new one.

| Measure | Value |
|---|---|
| Gemma 4 perceive (Laptop B) | 37 s |
| qwen3:14b decide, 16k context | 54-60 s in the run, 113.8 s in a re-measure (load average ~7) |
| qwen3:14b decide, 8k context | **8 s** warm (46%/54% CPU/GPU instead of 55%/45%) |
| Prompt size | 1.7-1.9k tokens |

**Cause:** Chrome stops an extension service worker when a fetch response takes more than 30 s to arrive. The server
sent nothing until the step was decided, 60-90 s later.

**Fix:** streamed replies start at once, with a heartbeat every 5 s; 8k context; the VLM describes a screenshot only
when the outline is thin.

**Not the cause:** conversation memory. Each request is about 2k tokens, because the server builds its own short
prompt. A client-side vector DB would not help here; it could later serve "recipe memory" (how a site was done
before). The real limit is that qwen3:14b doesn't fit in 8 GB of VRAM. The option is qwen3:8b on Laptop A: it fits
whole and is several times faster, but it needs its own validity test.

## Click loops, the real outline format, and an end to retries (2026-10-18)

- **Loop:** on a new Google Form the model clicked "Add question" (refused, OBSCURED) and the element covering it in
  turn, 9 times. **Fix:** the per-element ladder in `server/orchestrator/pipeline/recover.py` (vision locate, then the
  keyboard, then hand-over). Fixture `p-covered.html`: PASS with the real models in 6 turns (refused click → locate
  → Enter → done).
- **Clicking by position does not exist in this Nanobrowser** (`{x, y}` → CAPABILITY_DISABLED). The earlier plan's
  "point + marker verify" path can never run; point commands are now refused at the server.
- **The snapshot quotes JSON-style:** `[e3] textbox "Email" = "x"` (perception/outline.ts), not «…». The first privacy
  detector for labelled fields and the server's element names assumed «…»; the unit tests used an invented format, so
  they passed. Both now read the real format. **Privacy impact:** before the fix, a name in a field labelled "Full
  name" was hidden only if the vault or a rule already knew it. Screenshots now also hide any value in a field labelled
  as personal.
- **False success:** a run answered `done: success` without typing anything, because the VLM called the empty fields
  "filled". `done` with success is now refused while a task placeholder is untyped. The perceive prompt says only solid
  grey boxes hide data.
- After the fixes: `privacy-contact` PASS (9 turns, 118 s) and `privacy-covered` PASS (6 turns, 97 s), both with Gemma
  4 on Laptop B and 0 real values at the server.
