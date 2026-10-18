# `server/vlm_worker/vlm_model.py`

**Responsibility.** Runs one job on the local VLM (Qwen2.5-VL 7B in Ollama). Perceive and verify answers are constrained to their JSON schema and validated; chat jobs return plain text.

**In → out.** A Job → a result dict.

**Depends on.** protocol.py, httpx

**Used by.** worker.py

**Notes and limits.** Env: OLLAMA_URL, VLM_MODEL (qwen2.5vl:7b), VLM_NUM_CTX (8192).

**Changed (2026-10-18).** Default model UI-TARS-1.5-7B. `locate` jobs and the start-up `calibrate()` (grounding.py); `VLM_COORDS` overrides.
