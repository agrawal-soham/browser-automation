# `client/src/privacy/tags.ts`

**Responsibility.** Defines the placeholder format `[[KIND_N]]`, the list of kinds (NAME, EMAIL, PHONE, ADDRESS, CARD, ID, DATE, PERSONAL), the exact-tag pattern and the near-miss pattern that catches bent tags (`[EMAIL_1]`, `[[email_1]]`, `⟦EMAIL_1⟧`, bare `NAME_2`).

**In → out.** A kind and a number → a tag string. A string → the exact tags in it.

**Depends on.** nothing

**Used by.** vault.ts, detectors.ts (type), swap-back.ts

**Notes and limits.** Phase 0 still has to run the tag copy test against `⟦⟧`. Change the format only here.
