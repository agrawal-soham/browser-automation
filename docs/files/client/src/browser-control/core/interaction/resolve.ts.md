# `client/src/browser-control/core/interaction/resolve.ts`

**Responsibility.** (Fork file, the layer.) Resolves and revalidates a target at action time: a ref, a role and name, and now a point.

**In → out.** **Our changes:** point targets `{x, y}` (CSS pixels of the last screenshot of the visible page), behind the `vision` capability. `resolvePoint` refuses a point when the page scrolled, resized or loaded since that screenshot (`STALE_POINT`) or when it lies outside it; hit-tests it (`nodeAtViewportPoint`); takes a fresh capture and walks up from the element under the point (probe `parentOf`) to the nearest element the outline gives a ref, so the gate classifies it by its role and name as for a ref. `withPoint` keeps the point on the target (also after a confirmation, in gate.ts) and adds "at (x, y)" to its description, so every receipt names it.

**Depends on.** core/geometry.ts, core/tools/observe.ts (`observeTab`), probe/source.ts (`parentOf`), core/controller.ts (`TabState.shot`)

**Used by.** every tool that takes a target; core/interaction/gate.ts; core/interaction/actionability.ts (`checkAtPoint`: the click is pressed at the point, not the element's centre, and the page is not scrolled for a point)

**Tests.** `pnpm autorun scenarios --args '{"sets":["C"]}'`: `src/background/scenarios/point-targets.ts` on `fixtures/pages/c-canvas-grid.html` (a canvas's drawn button, no capability, a scrolled page, nothing named at the point, a "Submit order" button that still asks).
