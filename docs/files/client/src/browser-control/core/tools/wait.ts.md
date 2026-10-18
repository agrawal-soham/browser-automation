# `client/src/browser-control/core/tools/wait.ts`

**Responsibility.** (Fork file, the layer.) `wait`: for text to appear or go, a URL, an element state, the page to settle, or a fixed time. Read-only and bounded by `timeoutMs`.

**In → out.** **Our changes:** a `settled` condition. The page is settled when `document.readyState` is `complete` and the probe's page-wide `mutations()` count has not changed for 500 ms (`settleTracker`, unit-tested in `core/__tests__/settle.test.ts`). It answers `wait · met: page settled after N ms`, or `error TIMEOUT … the page is still changing`. The server asks for it before it looks at a page after an action, because the layer's own load wait stops at 5 s and Google Sheets and Forms were still building their page after that.

**Depends on.** probe/source.ts (`readyState`, `mutations`), protocol/schemas.ts (`WaitArgsSchema.settled`)

**Used by.** the layer's tool table; the orchestrator's `settle_call` (server/orchestrator/pipeline/step.py)
