# `server/tests/test_pipeline.py`

**Responsibility.** Tests: real spike requests are classified right; snapshot and ref/tag tracking; check rejections and risk; formatter shapes and SSE; the endpoint with a fake qwen3:14b (probe, snapshot, retry with a note, giving up with ask_user, refused tag → ask_user).

**In → out.** —

**Depends on.** orchestrator

**Used by.** `pytest`
