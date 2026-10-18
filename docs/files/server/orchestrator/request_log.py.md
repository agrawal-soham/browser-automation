# `server/orchestrator/request_log.py`

**Responsibility.** Appends one JSON line per request to `logs/requests-<date>.jsonl`: kind, redaction header, task, tags seen, the reply, attempts, notes, perception and timings. Images are never written. This is the evidence that only tags arrived.

**In → out.** dict → a line on disk.

**Depends on.** nothing

**Used by.** main.py

**Changed after the E2E runs.** `LOG_IMAGES=1` saves each received (masked) image to logs/images/.
