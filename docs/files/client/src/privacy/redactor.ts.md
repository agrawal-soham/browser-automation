# `client/src/privacy/redactor.ts`

**Responsibility.** Replaces personal values with tags. It replaces vault values first, so a value typed earlier is caught again, and then runs the detectors. `redactBody` walks a whole parsed request body, redacts every string and collects images without changing them.

**In → out.** Text or a JSON body, plus the vault and rules → the redacted text or body, and the list of images.

**Depends on.** detectors.ts, vault.ts (type)

**Used by.** net-wrapper.ts, leak-check.ts

**Changed after the E2E runs.** Values are also matched URL- and form-encoded; detectors also run on a decoded copy; `redactBody` repeats (up to 4 passes) until a pass issues no new tag.
