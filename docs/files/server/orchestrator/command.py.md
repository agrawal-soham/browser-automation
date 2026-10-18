# `server/orchestrator/command.py`

**Responsibility.** The command qwen3:14b answers with (action, ref/point, value, direction, message, outcome, summary, reason), plus the JSON schema sent to Ollama as `format`. Running it as a module prints `contracts/command.schema.json`.

**In → out.** —

**Depends on.** pydantic

**Used by.** text_model.py, pipeline/check.py, formatter.py, main.py

**Changed after the first use from Chrome.** New action `navigate` with `url` (the first version could not open a site: "open youtube.com" failed).

**Changed (2026-10-18, real runs).** `press` (server-only, never offered to the model) and `ask_kind` (input or takeover).

**Changed (2026-10-18).** `visual`: what to click as it looks, when the outline has no ref for it. `point` is set only by the server's grid. `contracts/command.schema.json` regenerated (it also lacked `press` and `ask_kind`).
