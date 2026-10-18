# `server/orchestrator/text_model.py`

**Responsibility.** The qwen3:14b client (Ollama `/api/chat`): `think: false`, temperature 0, num_ctx from config, and the command schema as `format`. Returns the parsed JSON with its timing and token counts.

**In → out.** Messages → (command dict, stats); or plain text for chat.

**Depends on.** command.py, config.py, httpx

**Used by.** pipeline/step.py, main.py
