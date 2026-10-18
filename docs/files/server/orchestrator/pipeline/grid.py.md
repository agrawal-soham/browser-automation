# `server/orchestrator/pipeline/grid.py`

**Responsibility.** Clicking by position for what the outline has no ref for: `locate_on_grid(description, turn, link)` asks the VLM for the chessboard cell on a gridded screenshot (without labels), then for one of 36 squares of that cell zoomed in, and returns the point in CSS pixels (`GridHit`), or None and why.

**In → out.** A description ("cell A1") and the latest screenshot → `GridHit(x, y, cell, sub, seen)`. Both questions are free-text `chat` jobs, evidence first ("seen", then the cell or square).

**Depends on.** imaging.py (`draw_grid`, `zoom_cell`, `parse_cell`, `to_css`), recover.py (`parse_location`), vlm_link.py

**Used by.** pipeline/step.py (a `visual` click from the model or from the ladder's grid rung), which then checks the point with a marker (verify.py) before the click goes out.

**Changed (2026-10-18, UI-TARS).** When the worker offers `locate`, one grounding call gives the point (fractions of
the image, turned into CSS pixels); the chessboard grid is the fallback. `GridHit.how` says which was used.
