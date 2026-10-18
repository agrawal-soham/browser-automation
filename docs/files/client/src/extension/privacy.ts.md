# `client/src/extension/privacy.ts`

**Responsibility.** Connects the privacy layer to the extension: the one worker-wide context, learned rules loaded from `chrome.storage.local['privacyRules']` and kept up to date, the `imagePrivacy` session hook for the layer, `swapToolArgs` (turns a refusal into a layer-shaped error result), and `rememberAnswer` (puts ask_user answers into the vault).

**In → out.** —

**Depends on.** privacy/index.ts, browser-control/index.ts (types)

**Used by.** extension/models.ts, extension/agent-session.ts
