# `server/orchestrator/prompt.py`

**Responsibility.** Builds the compact decide prompt: the task, user notes, browser state, the VLM's screen description, the snapshot cut to SNAPSHOT_CHARS, the last 4 results, and rejection notes from earlier attempts.

**In → out.** Turn + perception + notes → chat messages.

**Depends on.** turn.py, prompts/decide.txt

**Used by.** pipeline/step.py

**Changed after the E2E runs.** Adds "FIELDS YOU ALREADY FILLED AFTER THE OUTLINE".

**Changed after the first use from Chrome.** Shows CURRENT REQUEST, EARLIER REQUESTS and THE ACTIVE TAB in place of the whole chat as one task.

**Changed (2026-10-18).** An after-action check is shown as "WHAT THE SCREEN SHOWS AFTER YOUR LAST ACTION".

**Changed (2026-10-18, IRCTC run).** "A DIALOG IS OPEN ON TOP OF THE PAGE" with its controls, and "YOUR LAST ACTION WAS BLOCKED" after an `OBSCURED` error.

**Changed (2026-10-18, dropdowns and dialogs).** Adds "A LIST OF CHOICES IS OPEN" with the option refs, and "A BROWSER DIALOG IS WAITING" with the browser's report. `decide.txt` describes the `dialog` action and how to choose from selects, fields that suggest and dropdown menus.
