# `server/orchestrator/pipeline/recover.py`

**Responsibility.** The escalation ladder for clicks that keep failing on one element of the open page (the Google Forms "Add question" loop): after 1 failure, a fresh labelled screenshot and the VLM names the red label on the visible element (the click goes there); after 2, the keyboard (focus + Enter: the layer's `press` does not hit-test, so a cover does not stop it); after 3, or 6 failed actions on the page, the step is handed to the user (ask_user takeover) and retries stop.

**In → out.** Command + Turn + VLM link → None (go ahead), a replacement command, or a screenshot to take first

**Depends on.** turn.py (failures_on, element_label), vlm_link.py (a chat job: no change needed on Laptop B)

**Used by.** pipeline/step.py

**Notes and limits.** This Nanobrowser refuses {x, y} targets, so the ladder never clicks by position. Tested with a fixture button under an invisible layer (p-covered.html) and the real models: refused click → vision locate → Enter → done.

**Changed (2026-10-18).** A grid rung: 1 failure → label locate (now asks for a labelled screenshot when the latest has none), 2 → grid click (`visual`), 3 → keyboard, 4 → hand over (`MAX_TRIES_PER_ELEMENT = 4`). A failed grid click counts against its element through its intent ("target: the …").
