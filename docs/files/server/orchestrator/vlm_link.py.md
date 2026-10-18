# `server/orchestrator/vlm_link.py`

**Responsibility.** Laptop A's end of the wss link: one long-lived connection with reconnect and backoff, the token as its first message, TLS that trusts only VLM_CA_FILE, and jobs matched by id. `run()` returns None when the link is down or slow, so the step goes on text-only.

**In → out.** (kind, prompt, image) → JobResult or None.

**Depends on.** vlm_worker/protocol.py, config.py, websockets

**Used by.** main.py, pipeline/step.py, pipeline/verify.py

**Notes and limits.** Refuses a ws:// URL unless VLM_ALLOW_INSECURE=1.

**Changed (2026-10-18).** `last_error` says why the latest job got no answer (link down, timeout, worker error), so the step can report it instead of failing silently.
