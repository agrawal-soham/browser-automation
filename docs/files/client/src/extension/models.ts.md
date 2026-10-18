# `client/src/extension/models.ts`

**Responsibility.** (Fork file.) Resolves the conversation's model into a callable client.

**In → out.** **Our change:** `createModel(...)` now gets `{ fetch: privacyFetch(globalThis.fetch, privacy) }`, so every model request of every provider is redacted and leak-checked.

**Depends on.** privacy/index.ts, extension/privacy.ts (added)

**Used by.** run-manager.ts (unchanged)
