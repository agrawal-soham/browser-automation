# `client/src/agent/attachments.ts`

**Responsibility.** (Fork file.) Turns the user's attached files into message parts.

**In → out.** **Our change:** attached images are registered as `user-attachment` in the image registry, so the wrapper lets them through (the user chose to send them).

**Depends on.** privacy/index.ts (added)

**Used by.** agent loop (unchanged)
