# `server/run_orchestrator.sh`

**Responsibility.** Starts the orchestrator on 127.0.0.1:8000 with the settings from ~/privacy-agent/laptop-a.env; extra settings can be given in front (LOG_IMAGES=1, TEXT_NUM_CTX=8192, PORT).

**In → out.** —

**Depends on.** server/.venv, laptop-a.env

**Used by.** you, after setup_laptop_a.sh

**Notes and limits.** Tested: connects to the worker with the token file and /health shows the VLM.
