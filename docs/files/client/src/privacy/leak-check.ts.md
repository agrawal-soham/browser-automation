# `client/src/privacy/leak-check.ts`

**Responsibility.** The last look before a request leaves. It doesn't trust the redactor: it searches the cleaned body again for every vault value and runs the detectors again. Any finding blocks the request with `LeakBlocked`, whose message names kinds and lengths, never values.

**In → out.** A cleaned body → a list of leaks (empty means safe).

**Depends on.** detectors.ts, redactor.ts, image-registry.ts (type), vault.ts (type)

**Used by.** net-wrapper.ts

**Changed after the E2E runs.** Also runs the detectors on a decoded copy of each string.
