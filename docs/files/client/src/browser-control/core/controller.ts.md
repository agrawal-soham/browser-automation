# `client/src/browser-control/core/controller.ts`

**Responsibility.** (Fork file, the layer.) Controller, sessions and their configuration.

**In → out.** **Our change:** a new `SessionConfig.imagePrivacy` (`hide(name, value)`, `onMasked(base64)`) stored on the Session. It is set by the host and absent from the protocol, like `handover`.

**Depends on.** nothing new

**Used by.** core/tools/observe.ts
