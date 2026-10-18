# `server/orchestrator/turn.py`

**Responsibility.** Reads one Nanobrowser request into a Turn (task, browser state, user notes, tool steps with results, latest snapshot, known refs and tags, last image and its scale) and sorts it into probe / done_only / chat / agent. Decides when a fresh snapshot is needed and spots a refused placeholder.

**In → out.** OpenAI chat request JSON → Turn.

**Depends on.** nothing

**Used by.** main.py, prompt.py, pipeline/*

**Changed after the E2E runs.** Tracks the latest screenshot (`screenshot_at`), asks for a snapshot after `ask_user`, and lists fields filled since the outline (`filled_since_snapshot`).

**Changed after the first use from Chrome.** Never asks for two snapshots in a row: a page that cannot be observed (a new tab) goes to the model, which can navigate away.

**Changed after the first use from Chrome.** Splits the chat into the user's requests (`requests`); the last one is the current task, the earlier ones are shown as already handled. Reads the active tab's address (`active_url`).

**Fixed (2026-10-18).** `REF` accepts multi-letter epoch prefixes (`bl90`) and the 12-uppercase fallback. With the old one-letter pattern, every ref on a page looked invalid once the prefix grew past `z` (after extension reloads), so correct clicks were rejected and the VLM description ran on every page.

**Changed (2026-10-18, real runs).** Per-element failure tracking for the ladder (`failures_on`, `failures_on_page`, `steps_on_page`) and `element_label`, which reads the snapshot's real JSON-style quotes (`[a1] button "Add question"`) as well as «…», preferring the line that carries the name.

**Fixed (2026-10-18, Google Forms run).** `STALE` also matches the layer's page-change receipts (`navigation: `, `new document`, `URL →`), so a click that opens another page is followed by a snapshot. `acted_since_snapshot` tells the step whether the page was looked at after the last action.

**Changed (2026-10-18, Google Sheets runs).** `unsure(step)`: whether the receipt confirms an action (`expect: met`, a page change, an error) or a picture is needed. `needs_settle`, `settle_problem`, `describe`, `last_action_index`, `screenshot_labelled`. `needs_snapshot` looks past trailing settle `wait`s.

**Changed (2026-10-18, IRCTC run).** `open_dialog()`: the last `dialog`/`alertdialog` of the outline, its refs and controls, and whether it blocks (modal or alert). `steps_on_page` restarts after an `ask_user`, so the failure budget resets when the user takes over. An `OBSCURED` error counts as unsure (a picture shows what covers the page).

**Changed (2026-10-18, grid).** `failures_on` also counts a failed grid click (a point) whose intent names the element.

**Changed (2026-10-18, dropdowns and dialogs).** `needs_snapshot` also asks for an outline after a click or typing on an element that opens a list of choices (`may_have_opened_choices`: a combobox, listbox, menu, searchbox or an element shown as collapsed/expanded), since its options are only in a new outline; that outline carries `CHOICES_INTENT`, so `needs_screenshot` does not treat it as a new page. `open_choices()` finds the open list and its option refs; `js_dialog_open()` reads a waiting alert/confirm/prompt from the last result.
