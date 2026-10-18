# `server/tests/smoke_text_model.py`

**Responsibility.** Phase 0 measurement with the real qwen3:14b: runs the same form step N times and prints seconds per step, prompt tokens, the validity rate and `ollama ps` (CPU/GPU split).

**In → out.** —

**Depends on.** orchestrator

**Used by.** run by hand: `python -m tests.smoke_text_model 5`
