# `client/src/browser-control/core/tools/observe.ts`

**Responsibility.** (Fork file, the layer.) snapshot, find, read and screenshot.

**In → out.** **Our changes:** `screenshot` also paints over every outline node for which `imagePrivacy.hide(name, value)` is true, using the layer's existing `blankBoxes` with the same fail-closed rules, and calls `imagePrivacy.onMasked` with the final image.

**Depends on.** core/controller.ts (type ImagePrivacy)

**Used by.** the layer's tool table

**Changed (2026-10-18).** A screenshot of the visible page records `TabState.shot` (document, scroll position, size): the page a point target refers to.
