# `client/src/privacy/detectors.ts`

**Responsibility.** Finds personal values in text: email, phone (only with `+` or separators, so tab ids are left alone), card numbers (Luhn), SSN, Aadhaar, PAN, the value of any form field whose label names personal data (read from the layer's outline lines `textbox «Label» = «value»`), and the user's learned rules (literal, regex, or field label).

**In → out.** Text and the learned rules → a list of hits (start, end, value, kind) with overlaps removed.

**Depends on.** luhn.ts, tags.ts (type)

**Used by.** redactor.ts, leak-check.ts, context.ts

**Notes and limits.** Plain names in running text are not detected unless they are in the vault or a rule.

**Changed after the E2E runs.** New: a name the user states in plain text ("my name is Jane Doe", "Name: …").

**Changed (2026-10-18, real runs).** Reads the snapshot's real field format, `textbox "Full name" = "Jane Doe"` (perception/outline.ts quotes JSON-style). The first version only matched «…», which the layer uses elsewhere, so a name in a labelled field was caught only when the vault or a rule already knew it. `labelKind` is exported for the screenshot rule.
