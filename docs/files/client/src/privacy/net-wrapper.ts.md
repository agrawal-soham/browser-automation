# `client/src/privacy/net-wrapper.ts`

**Responsibility.** The `fetch` that the model client is built with. For each request it: replaces unregistered images with a 'screenshot withheld' text part, redacts all text, runs the leak check, records what was sent, and sends with `X-Redaction-Scheme: typed-tags-v1`. Replies pass through unchanged, so placeholders stay in the conversation.

**In → out.** Nanobrowser's request (JSON body) → the same request with tags only, or an exception (nothing sent).

**Depends on.** leak-check.ts, redactor.ts, image-registry.ts (type), context.ts (type)

**Used by.** extension/models.ts (through index.ts)
