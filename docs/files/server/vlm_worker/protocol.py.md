# `server/vlm_worker/protocol.py`

**Responsibility.** The messages on the wss link: Auth, AuthOk, Job (perceive / verify / chat, with an optional masked PNG), JobResult, plus the Perception and Verdict shapes the VLM must answer with. Shared by both laptops.

**In → out.** —

**Depends on.** pydantic

**Used by.** orchestrator/vlm_link.py, vlm_worker/worker.py, vlm_worker/vlm_model.py

**Notes and limits.** MAX_MESSAGE_BYTES = 8 MB (the websockets default of 1 MB is too small for a PNG).

**Changed (2026-10-18).** Job kind `locate` (result: `Point`, fractions of the image); `AuthOk.capabilities`.
