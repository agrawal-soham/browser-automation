# Dependency graph

_Update this whenever a file is added or an import changes. Arrows point from a file to what it uses._

## Runtime flow (who calls whom across machines)

```mermaid
flowchart LR
  subgraph Chrome["Laptop A · Chrome (client/)"]
    loop["agent/loop.ts<br/>Nanobrowser's agent"]
    layer["browser-control<br/>snapshot · screenshot · set · click"]
    sess["extension/agent-session.ts<br/>layerPort: swap-back"]
    models["extension/models.ts<br/>createModel(fetch)"]
    pf["privacy/net-wrapper.ts<br/>redact · withhold · leak check"]
  end
  subgraph A["Laptop A (Ubuntu) · server/orchestrator"]
    main["main.py<br/>/v1/chat/completions"]
    step["pipeline/step.py"]
    qwen[("qwen3:14b<br/>Ollama")]
  end
  subgraph B["Laptop B (Windows) · server/vlm_worker"]
    worker["worker.py (wss)"]
    vl[("Gemma 4 (vision)<br/>Ollama, Windows")]
  end
  loop --> sess --> layer
  loop --> models --> pf -->|"HTTP, tags only"| main
  main --> step --> qwen
  step -->|"wss: masked image"| worker --> vl
  main -->|"tool_calls"| pf
```

## Client: `client/src/privacy/` and the files that use it

```mermaid
flowchart TD
  index["privacy/index.ts"]
  ctx["privacy/context.ts"]
  nw["privacy/net-wrapper.ts"]
  red["privacy/redactor.ts"]
  leak["privacy/leak-check.ts"]
  det["privacy/detectors.ts"]
  luhn["privacy/luhn.ts"]
  vault["privacy/vault.ts"]
  tags["privacy/tags.ts"]
  reg["privacy/image-registry.ts"]
  swap["privacy/swap-back.ts"]

  index --> ctx & nw & swap & reg & leak & tags
  ctx --> det & reg & vault & nw
  nw --> leak & red & reg & ctx
  red --> det & vault
  leak --> det & reg & red & vault
  det --> luhn & tags
  vault --> tags
  swap --> tags & vault

  extp["extension/privacy.ts"] --> index
  extp --> bc["browser-control/index.ts<br/>(types ImagePrivacy, Result)"]
  models["extension/models.ts"] --> index & extp
  sess["extension/agent-session.ts"] --> extp
  att["agent/attachments.ts"] --> index
  obs["browser-control/core/tools/observe.ts"] --> ctrl["browser-control/core/controller.ts<br/>SessionConfig.imagePrivacy"]
```

The layer (`src/browser-control/`) still imports nothing from outside its folder. The privacy hook reaches it as a
`SessionConfig` value that `extension/agent-session.ts` passes in.

## Server: `server/`

```mermaid
flowchart TD
  main["orchestrator/main.py"]
  turn["orchestrator/turn.py"]
  step["orchestrator/pipeline/step.py"]
  check["orchestrator/pipeline/check.py"]
  verify["orchestrator/pipeline/verify.py"]
  prompt["orchestrator/prompt.py"]
  decide["orchestrator/prompts/decide.txt"]
  tm["orchestrator/text_model.py"]
  cmd["orchestrator/command.py"]
  fmt["orchestrator/formatter.py"]
  link["orchestrator/vlm_link.py"]
  img["orchestrator/imaging.py"]
  cfg["orchestrator/config.py"]
  log["orchestrator/request_log.py"]
  proto["vlm_worker/protocol.py"]
  worker["vlm_worker/worker.py"]
  vm["vlm_worker/vlm_model.py"]

  main --> turn & step & tm & fmt & link & log & cfg & cmd
  step --> check & verify & prompt & tm & link & cmd & cfg & turn
  check --> cmd & turn
  e2e["tests/e2e_report.py"] -. reads .-> log
  verify --> img & link & cmd & turn
  prompt --> decide & turn
  tm --> cmd & cfg
  fmt --> cmd
  link --> proto & cfg
  worker --> proto & vm
  ps1["vlm_worker/start_worker.ps1"] --> worker
  certpy["tools/make_worker_cert.py"] -. makes TLS files for .-> worker
  setupb["setup/setup_laptop_b.ps1"] --> certpy & ps1
  seta["setup/setup_laptop_a.sh"] -. writes laptop-a.env for .-> runa["run_orchestrator.sh"] --> main
  vm --> proto
  schema["contracts/command.schema.json"] -. generated from .-> cmd
```

## External packages

| Side | Package | Used by |
|---|---|---|
| client | `ai`, `@ai-sdk/openai` (already in the fork) | `extension/models.ts`; our `fetch` is passed to it |
| server | `fastapi`, `uvicorn` | `orchestrator/main.py` |
| server | `httpx` | `text_model.py`, `vlm_model.py` (Ollama HTTP API) |
| server | `websockets` | `vlm_link.py`, `worker.py` |
| server | `pydantic` | `command.py`, `protocol.py` |
| server | `pillow` | `imaging.py` |
