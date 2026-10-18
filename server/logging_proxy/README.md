# Logging proxy (Nanobrowser ⇄ Local LLM)

A proxy that sits between Nanobrowser and local LLMs (Ollama or llama-server) so you can inspect:

- **what Nanobrowser sends in each stage**:
  - **Planner**: goal decomposition, high-level planning instructions, JSON schemas
  - **Navigator**: DOM interactive elements list (`[id]<tag>`), full screenshots, action tool schemas (`click_element`, `type_text`, `scroll`, `AgentOutput`)
  - **Validator**: validation criteria, screenshots, verification schemas (`ValidatorOutput`)
- **what the local LLM sends back**, and what Nanobrowser actually gets
- **multi-turn tool loop**: tool calls, execution results, and subsequent DOM/screenshot updates

```
Nanobrowser ──(OpenAI  /v1/chat/completions)──┐
                                              ├─▶ proxy :8080 ──(OpenAI)──▶ Ollama :11434/v1
Nanobrowser ──(Anthropic /v1/messages)────────┘        │                    (or llama-server :8000)
                                                       ├─ logs/session-*.jsonl   (one line per call)
                                                       ├─ logs/images/           (screenshots deduplicated)
                                                       └─ live viewer  http://localhost:8080/
```

- **OpenAI path**: pass-through with streaming SSE reassembly and logging.
- **Anthropic translation engine**:
  - Translates `/v1/messages` (system prompt, multimodal text + screenshots, tools, tool_use, tool_result) to OpenAI format.
  - Automatically handles tool emulation (`tool_shim`) for models without native tool calling (e.g., `qwen2.5vl:7b`, `gemma3:4b-16k`).
  - Translates local LLM replies back to Anthropic message format, replaying as standard Anthropic SSE streams when `stream: true`.
  - The live viewer lets you toggle between the **original Nanobrowser prompt** and the **translated prompt sent to the local LLM**.

## Quick Start with Ollama

```bash
cd server/logging_proxy

# 1. Start with Ollama (defaults to qwen2.5vl:7b or gemma3:4b-16k)
./run_ollama.sh qwen2.5vl:7b

# Or for dry runs without a GPU:
.venv/bin/python mock_upstream.py
.venv/bin/python proxy.py
```

### Recommended Local Models (Ollama)
| Model | Size | Capabilities | Notes |
|-------|------|--------------|-------|
| `qwen2.5vl:7b` | 6.0 GB | **Vision + Completion** | Recommended for Navigator & Validator screenshots |
| `gemma3:4b-16k` | 3.4 GB | **Vision + Completion** | Lightweight, fast, 16k context window |
| `qwen3:16k` | 9.3 GB | **Tools + Thinking** | Good for Planner stage |

## Point Nanobrowser at the Proxy

In Nanobrowser extension settings:

- **Anthropic Provider**:
  - Base URL: `http://localhost:8080` (or `http://localhost:8080/v1`)
  - API Key: `sk-local` (or anything non-empty)
  - Model: `claude-3-5-sonnet-20241022` or `qwen2.5vl:7b` (the proxy auto-maps Claude models to your local model)
- **OpenAI Provider**:
  - Base URL: `http://localhost:8080/v1`
  - API Key: `sk-local`
  - Model: `qwen2.5vl:7b` (or any name)

Assign the provider to **Planner**, **Navigator**, and **Validator**.

## Live Web Viewer

Open **http://localhost:8080/** in your browser:
- **Stage Filters**: filter traffic by `[All]`, `[Planner]`, `[Navigator]`, `[Validator]`.
- **Prompt Inspector**: toggle between the original Nanobrowser Anthropic prompt and the translated local LLM prompt.
- **Screenshot Viewer**: click any screenshot thumbnail to open a high-res lightbox view.
- **Output & Tools**: inspect JSON schemas, sampling parameters, and raw JSON payloads.
