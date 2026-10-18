# `client/src/background/checks/eval.ts`

**Responsibility.** (Fork file.) Runs evaluation tasks with a real model through the real loop and layer.

**In → out.** **Our changes:** `privacy: true` sends requests through `privacyFetch` (as the product does) and reports what the privacy layer did per request; `vision` states the model's vision; `runTimeoutS` lifts the 5-minute limit.

**Depends on.** privacy/index.ts, extension/privacy.ts (added)

**Used by.** `pnpm autorun eval`
