# `client/src/privacy/swap-back.ts`

**Responsibility.** Puts real values back into a tool call's arguments at the moment it runs. Strict: an unknown tag or a bent tag refuses the whole call with an error the server turns into `ask_user`.

**In → out.** Tool arguments → arguments with real values, or a refusal (`unknown-tag` / `near-miss`).

**Depends on.** tags.ts, vault.ts (type)

**Used by.** extension/privacy.ts
