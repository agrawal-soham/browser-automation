# `server/orchestrator/pipeline/verify.py`

**Responsibility.** Gets the VLM's second opinion on a high-risk click over wss. It marks the point on the image, or asks whether the page is ready for the risky element. Skipped without a screenshot or a link.

**In → out.** Command + Turn → VerifyOutcome (ran, ok, reason).

**Depends on.** imaging.py, vlm_link.py, command.py, turn.py

**Used by.** pipeline/step.py

**Changed after the Google Forms run (2026-10-18).** `confirm_done`: the VLM says whether the final screenshot shows the request done (the worker's `verify` job, so Laptop B is unchanged). `VerifyOutcome.why_not` says why a check did not run.

**Changed (2026-10-18, Google Sheets runs).** `confirm_done` runs as a `chat` job with an evidence-first JSON (`evidence`, then `ok`), parsed by `recover.parse_location`; the `verify` schema made the VLM give its verdict before looking. It names the Name Box as not a cell. An unreadable answer is a check that did not run.
