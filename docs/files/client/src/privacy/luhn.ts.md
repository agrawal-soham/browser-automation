# `client/src/privacy/luhn.ts`

**Responsibility.** The card-number check: 13 to 19 digits that pass the Luhn checksum.

**In → out.** A string → true/false.

**Depends on.** nothing

**Used by.** detectors.ts

**Notes and limits.** A copy of the layer's rule, so src/privacy imports nothing from the layer.
