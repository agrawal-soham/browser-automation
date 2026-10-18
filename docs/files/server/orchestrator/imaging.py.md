# `server/orchestrator/imaging.py`

**Responsibility.** Draws the verify marker (a ring and a cross) on the masked screenshot at a point given in CSS pixels times the screenshot's scale.

**In → out.** base64 PNG + point + scale → base64 PNG.

**Depends on.** pillow

**Used by.** pipeline/verify.py

**Added (2026-10-18).** `draw_grid` (16×9 chessboard grid, A1 at the top left, blue), `zoom_cell` (a cell and its neighbours, enlarged, with a numbered 6×6 sub-grid; `GridZoom.sub_center` maps a square back), `parse_cell`, `to_css` (image pixels to CSS pixels with the screenshot's scale).
