# Privacy layer, screenshots and the black box: a phased plan (rev 3)

## Setup (rev 3)
```
Laptop A                                                   Laptop B
Chrome + Nanobrowser fork (privacy layer)                  vlm_worker.py (wss server)
   │  POST /v1/chat/completions (tags + masked image)        │
   ▼                                                         ▼
orchestrator (FastAPI) ──── wss://B:8765 (masked image) ──▶ Qwen2.5-VL-7B (llama-server/Ollama, local)
   │  perceive / verify jobs  ◀── JSON result ───────────────┘
   ▼
qwen3:14b (Ollama, local to A)  ← the decider
```
- **qwen3:14b decides** from the text: task, trimmed page outline with refs, last result, and the VLM's screen description.
  It is called through Ollama `/api/chat` with `think: false` (thinking mode adds seconds per step), `format: <command JSON schema>`
  (constrained output), `temperature: 0`, `options.num_ctx: 16384`.
- **The VLM perceives and verifies only.** It never chooses an action.
- **The link between the laptops is `wss`.** Only masked images and tags ever cross it.

## Context
The build plan PDF says the extension redacts text, masks the screenshot and sends
only clean data to our server. The spike log (`server/logging_proxy/logs/session-20261017-233833.jsonl`)
shows how the current Nanobrowser actually talks to the model:
- There is **one tool-calling agent with 19 tools** (`snapshot`, `click`, `set`, `fill_form`, `type`, `ask_user`, `done`, …).
  There are no Planner, Navigator and Validator stages. Nanobrowser expects OpenAI `tool_calls` back.
- **Page data arrives as text**: `snapshot` outlines with refs like `[e12]`, and `read` returns Markdown.
- **Vision is a capability that was off.** Every target schema already accepts `{x, y}`, described as
  "CSS pixels of the last screenshot (capability "vision")". With the setting off, no images were sent.
- **The prompt is mostly tool schemas.** The first agent call is 40.6k characters, and 35.4k of that
  is the 19 tool schemas (~9k tokens). By call 21, history adds another ~28k characters (tool results 11.9k).
- **The 7B run is invalid.** `prompt_tokens` stays at 4098 while the prompts grow to ~11k–16k tokens,
  so they were truncated. The run also produced invalid tool arguments (`handle_dialog` with nulls).

Everything below happens **in the extension, before the request leaves the browser**, unless it is
marked as server work. The server only ever sees tags and black-boxed images.

**The fork is not cloned on this machine.** I searched `/home/jayant` and found no Nanobrowser source and
no `captureVisibleTab`/`Page.captureScreenshot` call. Every hook below is named by its role, not by a
file path. Phase 0 finds the real paths. The list of assumptions is at the end.

---

## Phase 0: Find the hooks and measure (1 day, everyone)
1. **Clone the fork and find the hook points** (record the file paths in `docs/hooks.md`):
   - **(a)** where the model client is built and which `fetch` it uses,
   - **(b)** where the `vision` capability is switched on, and the function that takes the screenshot,
   - **(c)** the tool dispatcher, where tool-call arguments are executed,
   - **(d)** every step between capture and the request body that **resizes or re-encodes** the image
     (Nanobrowser code and the SDK). This decides how Phase 2 recognizes our masked images.
2. **Capture the request shape with vision on** through the logging proxy. Record where the image sits
   (user `image_url` or a tool result), its pixel size and format, the `devicePixelRatio` handling, and whether
   old screenshots are re-sent in later turns. Save real request/reply pairs in `contracts/examples/`.
3. **Measure both models at real context size.**
   - **qwen3:14b on Laptop A** (`num_ctx 16384`, `think: false`, `format` schema), using the compact server prompt on the
     Google Form task. Record the real `prompt_tokens` (must be above 4098), **seconds per step**, the
     **command validity rate** (the share of replies that parse and pass the schema), and peak VRAM (`nvidia-smi`).
     If 14B Q4_K_M (~9 GB weights) plus the cache doesn't fit, record what Ollama offloads to the CPU and the slowdown that causes.
   - **The VLM on Laptop B.** Record seconds per perceive and per verify job at a 1024 px image, and the round trip over `wss`.
4. **Tag copy test.** Using 30 prompts on qwen3:14b, measure how often the model copies `[[EMAIL_1]]` exactly
   compared with `⟦EMAIL_1⟧` in its arguments. Use the better one. The default is `[[EMAIL_1]]` (plain ASCII, less tokenizer risk).

**Memory note.** These are estimates and must be measured in step 3.
- Laptop B (VLM): the 7B weights (~4.7 GB), the projector (~1.3 GB), the cache and the buffers take roughly all of 8 GB.
  The VLM's prompt is short (the image plus ~300 tokens), so its context can be 4–8k.
- Laptop A (qwen3:14b): ~9 GB of weights plus a 16k cache. Keeping the prompt small is what keeps this usable,
  so the target prompt size below is well under 16k.

**Checkpoint:** hook paths written down, the image shape and re-encode behavior known, and 7B numbers recorded at the real context size.

## Phase 1: Text privacy layer (Person 1)
All new code goes in the fork's `background/privacy/` folder (exact path from Phase 0).

1. **Trim** (`trim.ts`) runs first, so less leaves the browser and there is less to redact:
   - keep only the **latest** snapshot in full, and replace older ones with `[snapshot elided]`,
   - keep the last 2 tool results, and replace older ones with a one-line receipt (tool, ok or error),
   - from the latest snapshot, drop sections outside the viewport and nearby area that hold no actionable refs
     (navigation chrome, footers),
   - **target: ≤ 6k tokens per request** including the image (≤ 4k for text), with a hard cap at 8k (trim harder or drop the image).
2. **Vault** (`vault.ts`) holds tag ↔ real value in session memory only. Tags use the format chosen in Phase 0 and stay stable for the whole task.
3. **Redactor** (`redactor.ts`) runs on every outgoing string in this order:
   1. vault reverse lookup: any known real value becomes its tag,
   2. built-in rules (email, phone, card, address, name fields from the field label),
   3. learned rules.
   New hits get a new tag. Doing the vault step first stops a value the agent typed from leaking in the next snapshot.
4. **Network wrapper** (`netWrapper.ts`) passes a custom `fetch` into the model client at hook (a).
   It runs trim → redact → leak check and adds `X-Redaction-Scheme: typed-tags-v1`. It does not patch `globalThis.fetch`.
5. **Swap-back** happens at hook (c), at execution time. Arguments keep their tags through Nanobrowser's history.
   Real values never re-enter it. Mixed text like `"Hi [[NAME_1]]"` is allowed.
   **Strict matching:** any tag-shaped token (`[[A-Z_]+_\d+]]`, plus near-misses such as wrong case,
   single brackets or a missing number) that is **not an exact vault key** is refused. The step becomes an `ask_user`
   that shows the user the field and the unresolved tag. It is never guessed or fuzzily matched.
6. **Leak check** (`leakCheck.ts`) is the last step before `fetch`. If any vault value or detector hit is still in the body, the request is blocked and the user is told.

**Checkpoint:** a Google Form is filled end to end, the proxy log contains only tags, and every request is under the target size.

## Phase 2: Screenshot and black box
**Where:** rects are collected at hook (b), at the moment of capture. The page can scroll between capture and
fetch, so the rects must come from the same moment as the image.

```
captureScreenshot(tab)                        ← Nanobrowser's function (hook b)
  └─ ours: maskedCapture(tab)
       1. rectsBefore = rectCollector(tab)
       2. png         = original capture
       3. rectsAfter  = rectCollector(tab)
       4. stable?     = same scroll position, and every sensitive rect moved < 2 px, and no new hits
                        → no: retry from step 1, up to 3 times, 150 ms apart; then withhold
       5. masked      = imageMasker(png, rectsAfter, dpr, scale)
       6. register masked (see "Recognizing masked images")
       return masked
```

1. **Rect collector** (`content/rectCollector.ts`). It finds the boxes to hide for every vault value and detector hit:
   - first version: inputs, textareas and selects whose value matches (`getBoundingClientRect`), and plain text
     nodes that contain the value (`Range.getClientRects()` on just the substring),
   - fields whose label is sensitive (password, card, SSN, DOB): the whole field, even when empty,
   - **open shadow DOM**: walk `element.shadowRoot` recursively. Closed shadow roots can't be read, so
     box the host element if its text or label matched,
   - viewport only, plus 4 px padding,
   - **later step:** iframes (same-origin offsets; box cross-origin frames whole), `<img>` avatars, and face detection.
2. **Stability check, tuned so busy pages are not starved.** It compares *sensitive rect positions* before and after
   the capture. It does not count DOM mutations, so videos, ads, spinners and live counters (for example on YouTube)
   do not cause a withhold. A withhold sends the request text-only with "screenshot withheld".
   **The withheld rate per site is a reported eval metric.**
3. **Coordinate mapping.** screenshot px = CSS px × `devicePixelRatio` × `scale`, where `scale` is our downscale
   (long edge ≤ 1024 px, ~750 image tokens for Qwen2.5-VL).
4. **Black box** (`imageMasker.ts`) uses `createImageBitmap` + `OffscreenCanvas` in the service worker.
   It fills each rect with solid black (no blur), writes the tag in white inside the box, and re-encodes as PNG.
5. **Recognizing masked images** (the image leak check in `netWrapper`). An unrecognized image blocks the request.
   Which option to use depends on Phase 0 step 1(d):
   - **No resize or re-encode after capture:** an exact SHA-256 of the masked bytes, kept in `maskedSet`.
   - **Resize or re-encode happens.** Options:
     - **A. Mask last (recommended).** Store the rects with each capture record. The wrapper masks the image
       in the final outgoing body, rescaling the rects to its actual width and height. The bytes that leave
       are then always the bytes we masked. To find which capture an image belongs to, match on
       image size and capture order. If that is ambiguous, block.
     - **B. Perceptual hash** (dHash) of the masked image, matched within a small Hamming distance. It survives
       resizing and JPEG, but it is probabilistic.
     - **C. Pixel probe.** Check that every registered box region in the outgoing image is near-black.
       This checks that the masking is present rather than which image it is. It works well as a second check on top of A or B.
     - **D. Disable the re-encode** in the fork, so the exact hash works. This is the simplest, if the code path allows it.
     - Not recommended: an invisible watermark, because it is fragile under JPEG and resizing.
6. **Points back.** If a command uses `point`, swap-back divides by `scale` before execution. The validator refuses
   a point that lands inside a black box.
7. **Preview panel.** Shows the original and the masked image side by side, plus a "hide this" button that adds a learned rule and re-masks.

**Checkpoint:** with vision on, every image in the proxy log is masked. The fake-PII test pages (inputs, text, open
shadow DOM) show no readable PII under OCR. The withheld rate is recorded for each test site.

## Phase 3: Server (Person 2)
1. **Router.** The Planner/Navigator/Validator design is removed. What remains is ~30 lines in `main.py`
   (no separate `router.py`):
   - **connection probe** (a `report_status` tool, or a "thinking" probe): answer locally with a canned valid reply,
   - **done-only request** (only the `done` tool offered): ask the model for a summary and format it as a `done` call,
   - **agent turn**: everything else goes to the pipeline.
2. **Step pipeline** (`pipeline/step.py`) for each agent turn:
   1. If there is no fresh snapshot, return a `snapshot` call **without calling either model**.
   2. If a masked image is present, send a **perceive** job over `wss`. Laptop B returns
      `{page_kind, modal, errors[], visible_fields[], notes}`.
      A 10 s timeout means continue text-only and log it.
   3. Build the **compact prompt** for qwen3:14b: task, trimmed snapshot, last receipt, perceive JSON, failure notes from
      earlier attempts, and **our command schema only** (click, type, scroll, ask_user, done). Nanobrowser's
      prompt and its 19 schemas are not forwarded.
   4. qwen3:14b returns a command `{action, ref | point, value_ref?, reason}`, with constrained output through Ollama `format`.
3. **Server feedback loop** (`pipeline/check.py`, `pipeline/verify.py`):
   - Checks: the command matches the schema, the `ref` exists in the latest snapshot, and every tag appears in the incoming request.
     A failure becomes a short note fed back to qwen3:14b.
   - For **high-risk** actions or `point` targets: send a **verify** job (the masked image with a marker drawn by `imaging.py` on the
     target, plus the question) to the VLM, which answers `{ok, reason}`. If `ok` is false, the reason goes back to qwen3:14b.
   - At most **2 retries**, then `ask_user`.
   - On the next turn, the receipt Nanobrowser returns (effect, diff) is compared with the command's `expect`. A
     mismatch goes into the next prompt as "last action failed because …".
4. **Formatter.** `formatter.py` turns the command into the OpenAI `tool_calls` reply Nanobrowser expects. There is no dependence on native tool calling.
   The mapping: `click` → `click{target:{ref}}`, `type` → `set` (or `type` for key-listening widgets), `scroll`, `ask_user`, `done`.
   Exact argument names come from `contracts/examples/`.
5. **The `wss` link** (`vlm_link.py` on A, `vlm_worker.py` on B, using the Python `websockets` library):
   - **TLS:** a cert made with `mkcert` (or self-signed) for Laptop B's LAN IP. Laptop A pins that cert and does not turn off verification.
   - **Auth:** a shared token in the first message, rejected if missing. The port is open only on the LAN.
   - **Protocol:** request `{id, type: "perceive"|"verify", image_png_b64, prompt, deadline_ms}` → reply `{id, ok, result | error, ms}`.
     Replies are matched by `id`, so jobs can overlap.
   - Set `max_size` to 8 MB (the library default of 1 MB is too small for a PNG). A ping every 20 s, reconnect with backoff,
     and if Laptop B is down, run text-only.
   - Laptop B logs nothing to disk except timings. The orchestrator's request log on A is the evidence record.

## Phase 4: Stretch
Iframes, avatar and face boxes in the masker, a learned-rules editor, and recipe memory.

## User feedback loop (client, Person 4 panel + Person 1 hooks)
- The preview panel shows what the user sees next to what was sent. "Hide this" saves a rule locally and masks the image again before sending.
- The user confirms risky actions (the client decides the risk itself and does not trust the server's label).
- `ask_user` appears when retries run out or a tag doesn't match the vault.
- A correction from the user ("wrong field") goes into the next request as a user message.

## Team split (rechecked)
The rect collector is **not light work**. It covers a content script, Range rects, shadow DOM walking, DPR and scale
mapping, the stability and retry logic, and its own test pages. Proposed split:

| Person | Owns |
|---|---|
| 1 (heavy) | trim, vault, redactor, net wrapper, leak check (text and image), swap-back with strict tags |
| 2 (heavy) | orchestrator: probes, step pipeline, compact prompt, qwen3:14b client, checks + retries, formatter, `wss` link and `vlm_worker.py` |
| 3 (medium) | Phase 0 model and tag measurements, `imageMasker.ts` (a pure function: image + rects → image, easy to unit-test), eval pages, OCR checks, withheld rate, benchmark |
| 4 (medium) | `rectCollector.ts` + stability check, preview panel; README/license/demo script move to whoever finishes first |

## Known limits (to state openly)
- Text drawn in `<canvas>`, images or video is not in the DOM, so it is not boxed. The OCR eval measures how often this happens.
- The tag in a box shows the server the *type* of the data, never the value.
- Closed shadow DOM is boxed whole, or missed if neither its text nor its label is visible to us.

## Verification
- On the eval pages, run `grep` for every fake-PII string over the proxy's `logs/*.jsonl`. Expect 0 hits.
- Run OCR (`tesseract`) on `logs/images/` for the same strings. Expect 0 hits.
- `prompt_tokens` per request stays ≤ 6k (log it), and the step latency is recorded.
- Fail-closed tests: scroll during capture, and break the content script. Expect "screenshot withheld". On YouTube, check that the withheld rate stays low.
- Tag tests: send an unknown tag and a near-miss tag. Both should produce `ask_user`, not a typed value.
- Point test: force a `point` reply and check that the click lands on the right element after rescaling.
- `wss` tests: a wrong token is refused, a bad cert is refused, and killing Laptop B mid-task makes the orchestrator continue text-only.
- Retry test: force a wrong ref and check that it produces a check failure, a retry, and `ask_user` after 2 retries.

---

## Assumptions to confirm in the fork's source
1. The model client can be given a custom `fetch` at construction (hook a). If it can't, patch the provider module instead.
2. `vision` is a user or provider setting that adds screenshots, and there is one capture function (hook b) that every screenshot goes through.
3. Where screenshots are resized or re-encoded (in Nanobrowser, the SDK, or nowhere). This picks the option in Phase 2.5.
4. Whether old screenshots are re-sent in history (this affects the token budget and trimming).
5. There is one tool dispatcher (hook c) where arguments can be rewritten just before execution.
6. Content scripts can be injected into the agent's tabs with the permissions the fork already has.
7. Which Nanobrowser tool corresponds to "type a value" (`set` vs `type`), and the exact `target` argument shape (from `contracts/examples/`).
8. How `report_status` and the "thinking" probes are triggered, and what reply each one accepts.
9. Monorepo paths (expected: `chrome-extension/src/background/…`, `pages/content/…`, `pages/side-panel/…`). The PDF's `client/background/privacy/` layout is probably wrong.

## What changed in rev 3
- The decider is **qwen3:14b** on Laptop A (`think: false`, Ollama `format` schema). The VLM moves to Laptop B and only perceives and verifies.
- Added a `wss` link (pinned TLS, token, `id`-matched jobs, 8 MB frames, text-only fallback).
- Added an explicit step pipeline, server feedback loop and user feedback loop. The two-tier split is now the design, so it is no longer a stretch goal.

## What changed from rev 1
1. **Token budget:** added a trim step to Phase 1 (≤ 6k target, 8k cap) and a server-built compact prompt (no 19 schemas). Added the 8 GB memory note, to be measured.
2. **Tool calls:** the model emits our command JSON with constrained decoding. `formatter.py` builds `tool_calls`. There is no dependence on native tool calling.
3. **Image recognition:** Phase 0 checks for resize and re-encode. The hash check is now conditional, with options A–D (mask-last recommended).
4. **Fail closed:** the stability check compares sensitive rects instead of DOM mutations. Added 3 capture retries and the withheld rate as an eval metric.
5. **Tags:** `[[EMAIL_1]]` is the default, with a copy test against `⟦⟧`. Unknown or near-miss tags are refused and become `ask_user`.
6. **Rect collector:** starts with inputs and text, includes open shadow DOM, and moves iframes to a later step.
7. **Workload:** the rect collector is now medium. `imageMasker` moves to Person 3, and the README and demo go to whoever is free.
8. **Router:** all Planner/Navigator/Validator assumptions are removed. The router is reduced to three cases in `main.py`.
9. **7B re-run:** Phase 0 now measures prompt tokens, seconds per step, command validity and VRAM at 16k. The tier-split decision is deferred until those numbers exist.
