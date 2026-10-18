# `server/vlm_worker/worker.py`

**Responsibility.** The wss server on Laptop B: TLS with the given cert and key, closes the connection unless the first message is the right token (constant-time compare), runs jobs one at a time on the GPU, and answers each with its id.

**In → out.** wss connections → JobResults.

**Depends on.** protocol.py, vlm_model.py, websockets

**Used by.** run by hand on Laptop B

**Notes and limits.** Writes only timings to its log. `--insecure` (plain ws) is for a one-machine test only.

**Changed after the E2E runs.** A dropped connection is logged as a disconnect, not a traceback.

**Changed for Windows.** `--token-file` reads the token from a file (a Notepad byte-order mark and newline are stripped), in place of the VLM_TOKEN variable.

**Changed (2026-10-18).** Calibrates before serving and sends the model's capabilities in `AuthOk`.
