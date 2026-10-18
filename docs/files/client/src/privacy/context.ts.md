# `client/src/privacy/context.ts`

**Responsibility.** Builds the one privacy context (vault, image registry, rules, a log of the last 50 requests) and the `hideInImage` test the screenshot masking uses.

**In → out.** Options (rules getter, optional vault/registry) → a PrivacyContext.

**Depends on.** detectors.ts, image-registry.ts, vault.ts, net-wrapper.ts (type)

**Used by.** extension/privacy.ts, net-wrapper.ts (type)

**Changed (2026-10-18, real runs).** `hideInImage`: a field labelled as personal (name, email, phone…) that holds any value is painted over, even when the vault does not know the value (a page can pre-fill the user's own name).
