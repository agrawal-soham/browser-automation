# `server/orchestrator/pipeline/step.py`

**Responsibility.** The server-side feedback loop for one step: a refused tag → ask_user; no fresh outline → snapshot; perceive over wss; then decide → check → verify, sending each problem back as a note, at most MAX_RETRIES retries, then ask_user.

**In → out.** Turn → StepResult (command or own snapshot call, attempts, notes, perception, timings).

**Depends on.** check.py, verify.py, prompt.py, text_model.py, vlm_link.py, config.py

**Used by.** main.py

**Changed after the E2E runs.** Requests a labelled `screenshot` after each new outline and before a risky click; perceives only a fresh screenshot; the VLM's no blocks only point clicks (ref clicks record it as `advisory`).

**Changed after a slow real run.** `wants_perception`: the VLM's screen description runs only on thin outlines (default under 8 refs); the click check before a risky click is unchanged.

**Changed after the Google Forms run (2026-10-18).** "done: success" with an action after the latest outline becomes a `snapshot` first; with vision on, `confirm_done` checks the claim on a fresh screenshot (its no is a note for the retry; no answer makes the outcome `partial`, with the reason in the summary). A wanted vision call that did not answer is recorded in `vision_missing`, the notes, and the command's reason.

**Changed (2026-10-18, Google Sheets runs).** Screenshots by purpose (`PAGE_SHOT`, `AFTER_SHOT`, `CHECK_SHOT`, `PLAIN_SHOT`), told apart by their intent. Every new page is described (`PERCEIVE=auto`; `thin` is the old rule). After an action `Turn.unsure` cannot confirm, `AFTER_SHOT` and a `chat` job asking what that action did. Before any server observation after an action, the layer's `wait {settled}` (`settle_call`, `SETTLE_TIMEOUT_S`). The done check uses an unlabelled screenshot. `warnings` (vision not answering, page not settled) replace `vision_missing` and go into the command's reason and the log.

**Changed (2026-10-18, grid).** A click with `visual` and no ref: a screenshot without labels (`PLAIN_SHOT`), `locate_on_grid`, the marker check (`verify` with the point; its no is a note for the retry), then a click at `{x, y}`. `grid` (cell, square, point, what the VLM saw) goes to the log.

**Changed (2026-10-18, dropdowns and dialogs).** The outline after an action that opened a list of choices is requested with `CHOICES_INTENT`.

**Changed (2026-10-18, site memory).** Asks `SiteMemory.next_step` before the page screenshot and description; a replayed
step skips both and the text model, and is checked like a decided one. Without a replay, the playbook's remaining steps
go into the prompt as hints. `run_step` takes the memory as an optional argument.
