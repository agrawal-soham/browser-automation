# `client/src/privacy/image-registry.ts`

**Responsibility.** The list of images allowed to leave the browser: screenshots masked by the layer, and images the user attached. It recognises an image by a fast fingerprint of its base64 text, which works because nothing resizes or re-encodes screenshots on the way to the request.

**In → out.** `allow(image, source)`; `sourceOf(image)` → where it came from, or undefined.

**Depends on.** nothing

**Used by.** net-wrapper.ts, leak-check.ts, context.ts, extension/privacy.ts, agent/attachments.ts

**Notes and limits.** Keeps the last 200 images. Not cryptographic: it protects against our own mistakes, not against an attacker.
