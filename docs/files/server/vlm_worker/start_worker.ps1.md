# `server/vlm_worker/start_worker.ps1`

**Responsibility.** Starts the VLM worker on Windows (Laptop B): checks the TLS files in C:\privacy-agent\tls, the .venv and that Ollama has the model, sets VLM_MODEL / VLM_NUM_CTX, and runs the worker with --token-file.

**In → out.** -Model (Ollama tag, e.g. gemma4), -TlsDir, -Port, -NumCtx

**Depends on.** worker.py

**Used by.** docs/setup.md §1.5, the scheduled task in §1.6

**Notes and limits.** Not run on Windows yet (written and reviewed on Ubuntu).
