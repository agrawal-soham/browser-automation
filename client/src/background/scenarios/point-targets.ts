// Scenarios for point targets (capability "vision"): a point of the last screenshot names the element there. The layer
// turns it into that element, presses it at the point (a canvas's own drawn button), and applies the same checks and
// confirmations as to a ref. A point is valid only on the page as the screenshot showed it.
import type { Result } from '@src/browser-control';
import { FIXTURE_ORIGIN } from '../fixtures-config';
import { evaluate } from '../checks/helpers';
import { defineScenario, outcome } from './registry';
import type { ScenarioContext } from './registry';
import { decline, withUser } from './set-c-confirm';

const PAGE = `${FIXTURE_ORIGIN}/c-canvas-grid.html`;

/** Opens the page with the "vision" capability and takes the screenshot the points refer to. */
async function open(ctx: ScenarioContext, vision = true): Promise<number> {
  const before = [...ctx.session.capabilities].filter(c => c !== 'vision');
  ctx.session.setCapabilities(vision ? [...before, 'vision'] : before);
  const tabId = await ctx.open(PAGE);
  await ctx.call('screenshot', {});
  return tabId;
}

/** A point inside an element, in viewport CSS pixels: its left and top plus an offset. */
async function pointIn(tabId: number, id: string, dx: number, dy: number): Promise<{ x: number; y: number }> {
  return evaluate(
    tabId,
    undefined,
    `(() => { const r = document.getElementById(${JSON.stringify(id)}).getBoundingClientRect(); return { x: r.left + ${dx}, y: r.top + ${dy} }; })()`,
  );
}

const counts = (tabId: number) =>
  evaluate<number[]>(tabId, undefined, '[window.okClicks, window.otherClicks, window.orders]');
const said = (r: Result) => (r.ok ? (r.receipt?.target ?? 'ok') : `${r.error?.code}: ${r.error?.message}`);

defineScenario({
  id: 'C-point-canvas',
  set: 'C',
  title: 'A point on a button a canvas draws',
  covers: 'the canvas is the target and is pressed at the point; the receipt names the point',
  async run(ctx) {
    const tabId = await open(ctx);
    const r = await ctx.call('click', { target: await pointIn(tabId, 'grid', 240, 75) });
    const c = await counts(tabId);
    return outcome(
      'clicked the canvas at the point: OK counted once, nothing else; the receipt says "at (x, y)"',
      `${said(r)}; ok ${c[0]}, other ${c[1]}`,
      r.ok && /Drawing board.* at \(\d+, \d+\)/.test(r.receipt?.target ?? '') && c[0] === 1 && c[1] === 0,
    );
  },
});

defineScenario({
  id: 'C-point-needs-vision',
  set: 'C',
  title: 'A point without the "vision" capability',
  covers: 'CAPABILITY_DISABLED; nothing dispatched',
  async run(ctx) {
    const tabId = await open(ctx, false);
    const r = await ctx.call('click', { target: await pointIn(tabId, 'grid', 240, 75) });
    const c = await counts(tabId);
    return outcome(
      'CAPABILITY_DISABLED; no click',
      `${said(r)}; ok ${c[0]}`,
      r.error?.code === 'CAPABILITY_DISABLED' && c[0] === 0,
    );
  },
});

defineScenario({
  id: 'C-point-stale',
  set: 'C',
  title: 'A point after the page scrolled',
  covers: 'STALE_POINT; nothing dispatched',
  async run(ctx) {
    const tabId = await open(ctx);
    const point = await pointIn(tabId, 'grid', 240, 75);
    await evaluate(tabId, undefined, 'window.scrollBy(0, 60); 1');
    const r = await ctx.call('click', { target: point });
    const c = await counts(tabId);
    return outcome(
      'STALE_POINT (the page scrolled since the screenshot); no click',
      `${said(r)}; ok ${c[0]}, other ${c[1]}`,
      r.error?.code === 'STALE_POINT' && c[0] === 0 && c[1] === 0,
    );
  },
});

defineScenario({
  id: 'C-point-nothing-named',
  set: 'C',
  title: 'A point on a banner with no control under it',
  covers: 'NO_MATCH: nothing the outline names is there; nothing dispatched',
  async run(ctx) {
    const tabId = await open(ctx);
    await ctx.call('snapshot');
    const r = await ctx.call('click', { target: { x: 470, y: 180 } });
    const c = await counts(tabId);
    return outcome(
      'NO_MATCH; no click',
      `${said(r)}; ${c.join(', ')}`,
      r.error?.code === 'NO_MATCH' && c.every(n => n === 0),
    );
  },
});

defineScenario({
  id: 'C-point-submit-gated',
  set: 'C',
  title: 'A point on the text of a "Submit order" button; the scripted user declines',
  covers: 'the point names the button around the text; the gate asks as for a ref; USER_DECLINED',
  async run(ctx) {
    return withUser(decline, async seen => {
      const tabId = await open(ctx);
      const r = await ctx.call('click', { target: await pointIn(tabId, 'order', 20, 10) });
      const c = await counts(tabId);
      return outcome(
        'one confirmation for button "Submit order"; USER_DECLINED; the button saw no click',
        `${said(r)}; prompts ${seen.length}; orders ${c[2]}`,
        r.error?.code === 'USER_DECLINED' && seen.length === 1 && c[2] === 0,
      );
    });
  },
});
