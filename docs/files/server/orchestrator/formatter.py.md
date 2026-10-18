# `server/orchestrator/formatter.py`

**Responsibility.** Turns a command into Nanobrowser's tool call (click → click, type → set or type, scroll → scroll by pages, ask_user, done) and wraps it as an OpenAI chat completion, either as one JSON body or as an SSE stream ending with usage and [DONE].

**In → out.** Command → (tool name, args) → completion JSON or SSE lines.

**Depends on.** command.py

**Used by.** main.py

**Changed after the first use from Chrome.** `navigate` → Nanobrowser's `navigate {url}`.

**Changed (2026-10-18).** `dialog` maps to the layer's `handle_dialog {accept, promptText?}`.
