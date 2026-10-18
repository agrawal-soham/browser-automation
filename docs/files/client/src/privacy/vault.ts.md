# `client/src/privacy/vault.ts`

**Responsibility.** Keeps each real personal value together with its tag, in service-worker memory only. Gives the same tag for the same value every time (case and spacing ignored), so the model sees stable placeholders.

**In → out.** `tagFor(value, kind)` → tag (or null for values shorter than 3 characters); `valueOf(tag)` → real value; `entries()` → all pairs, longest value first.

**Depends on.** tags.ts

**Used by.** redactor.ts, leak-check.ts, swap-back.ts, context.ts

**Notes and limits.** Never persisted, so a worker restart forgets it. One vault is shared by all conversations.
