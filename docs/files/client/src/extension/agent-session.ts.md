# `client/src/extension/agent-session.ts`

**Responsibility.** (Fork file.) Creates a conversation's layer session and the `layerPort` the agent loop calls tools through.

**In → out.** **Our changes:** sessions get `imagePrivacy` (screenshots also hide personal values); `layerPort.call` swaps tags for real values before dispatch and refuses unknown or bent tags; after `ask_user` it calls `rememberAnswer`.

**Depends on.** extension/privacy.ts (added)

**Used by.** run-manager.ts, checks and scenarios (unchanged)
