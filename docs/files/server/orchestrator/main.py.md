# `server/orchestrator/main.py`

**Responsibility.** The FastAPI app on Laptop A: `/v1/chat/completions` (answers probe, done_only, chat and agent requests), `/v1/models`, `/health`. CORS allows chrome-extension origins. It starts and stops the VLM link.

**In → out.** HTTP from the extension → tool-call replies.

**Depends on.** turn.py, pipeline/step.py, text_model.py, formatter.py, vlm_link.py, request_log.py, config.py

**Used by.** uvicorn

**Changed after the first use from Chrome.** `/v1/models` declares `input_modalities: [text, image]` and `context_length`, so Nanobrowser offers the screenshot tool without a separate Test; the log records the tools offered and whether `screenshot` is one.

**Changed after a slow real run.** Streamed replies start at once and send an SSE comment heartbeat every 5 s while the step works: Chrome stops an extension's service worker when a fetch response takes over 30 s to arrive, and a Google Forms run stopped after 60-90 s steps. A run the extension abandons cancels its step. Nanobrowser's own stream code was tested to accept the heartbeats.

**Added (2026-10-18).** `GET /health/vlm`: sends a PNG with a random number over the wss link and checks that the VLM reads it back (`correct`, `ms`, `worker_ms`, the error when it fails).

**Changed (2026-10-18).** The log entry has `grid` for a click found on the grid.

**Changed (2026-10-18).** Creates the `SiteMemory`; a verified `done success` is recorded as a playbook
(`entry["memory"]` in the request log).
