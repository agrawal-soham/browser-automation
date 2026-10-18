// `wait`: for text to appear or go, a URL, an element state, the page to settle, or a fixed time. Read-only.
import { isLayerError, LayerError } from '../../protocol/errors';
import type { WaitArgs } from '../../protocol/schemas';
import type { ProbeState } from '../../probe';
import type { ToolContext, ToolOutcome } from '../controller';
import { checkCancelled } from '../controller';
import { resolveTarget } from '../interaction/resolve';
import { withPageState } from './navigate';
import { quote } from '../text';

const POLL_MS = 100;
const DEFAULT_TIMEOUT_MS = 5_000;
/** How long the document must stay unchanged to count as settled. */
const QUIET_MS = 500;
/** A target that cannot be resolved is not in the page: the page has not rendered it yet, or has removed it. */
const ABSENT = new Set(['NO_MATCH', 'NODE_DETACHED', 'STALE_DOCUMENT', 'FRAME_DETACHED']);

/** Resolves after `ms`, or as soon as the call is cancelled: a cancelled wait lets go of the tab at once. */
function pause(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise(resolve => {
    if (signal?.aborted) return resolve();
    const done = () => {
      clearTimeout(timer);
      signal?.removeEventListener('abort', done);
      resolve();
    };
    const timer = setTimeout(done, ms);
    signal?.addEventListener('abort', done);
  });
}

/**
 * The `settled` condition across polls: the document has loaded (`complete`) and its mutation count has not changed
 * for `quietMs`. Returns what is still missing, or null once settled.
 */
export function settleTracker(quietMs = QUIET_MS) {
  let count = -1;
  let since = 0;
  return (readyState: string, mutations: number, now: number): string | null => {
    if (mutations !== count) {
      count = mutations;
      since = now;
    }
    if (readyState !== 'complete') return `the page is still loading (${readyState})`;
    if (now - since < quietMs) return 'the page is still changing';
    return null;
  };
}

/** Checks every condition once; returns the unmet ones with what was seen. */
async function unmet(ctx: ToolContext, args: WaitArgs, settle: ReturnType<typeof settleTracker>): Promise<string[]> {
  const out: string[] = [];
  const attachment = await ctx.attach();
  const probe = ctx.tab().probe!;
  const main = (await attachment.topology.frames())[0];
  if (args.url !== undefined) {
    const url = (await ctx.controller.host.tabs.get(ctx.tab().tabId))?.url ?? '';
    if (!url.includes(args.url)) out.push(`URL is ${url}`);
  }
  if (args.settled) {
    const [readyState, mutations] = await Promise.all([
      probe.callGlobal<string>(main, 'readyState'),
      probe.callGlobal<number>(main, 'mutations'),
    ]);
    const missing = settle(readyState, mutations, performance.now());
    if (missing) out.push(missing);
  }
  if (args.text !== undefined && !(await probe.callGlobal<boolean>(main, 'hasText', args.text))) {
    out.push(`${quote(args.text)} is not on the page`);
  }
  if (args.textGone !== undefined && (await probe.callGlobal<boolean>(main, 'hasText', args.textGone))) {
    out.push(`${quote(args.textGone)} is still on the page`);
  }
  if (args.target) {
    const wanted = args.state ?? 'visible';
    let state: ProbeState | null = null;
    let gone = false;
    try {
      // A semantic target is looked up in a fresh capture on every poll: the page changes without an action here.
      const target = await resolveTarget(ctx, args.target, { fresh: true });
      try {
        state = await probe.call<ProbeState>(target.frame, target.objectId, 'state');
      } finally {
        await probe.release(target.frame, target.objectId);
      }
    } catch (error) {
      if (!isLayerError(error) || !ABSENT.has(error.code)) throw error;
      gone = true;
    }
    const attached = !gone && !!state?.connected;
    const ok =
      wanted === 'attached'
        ? attached
        : wanted === 'detached'
          ? !attached
          : wanted === 'hidden'
            ? !attached || !state!.visible
            : wanted === 'enabled'
              ? attached && state!.enabled
              : attached && state!.visible;
    if (!ok)
      out.push(
        `the target is ${!attached ? 'not in the page' : state!.visible ? (state!.enabled ? 'visible' : 'disabled') : 'hidden'}`,
      );
  }
  return out;
}

export async function wait(ctx: ToolContext, args: WaitArgs): Promise<ToolOutcome> {
  const conditions = [args.text, args.textGone, args.url, args.target, args.settled || undefined].filter(
    c => c !== undefined,
  ).length;
  const settle = settleTracker();
  const start = performance.now();
  if (!conditions) {
    if (args.ms === undefined) throw new LayerError('INVALID_ARGUMENT', 'wait needs a condition or ms');
    await pause(args.ms, ctx.signal);
    checkCancelled(ctx);
    return withPageState(ctx, args.observe, { data: { met: 'time', ms: args.ms }, lines: [`wait · ${args.ms} ms`] });
  }
  const budget = args.timeoutMs ?? args.ms ?? DEFAULT_TIMEOUT_MS;
  let last: string[] = [];
  for (;;) {
    checkCancelled(ctx);
    last = await unmet(ctx, args, settle);
    const elapsed = Math.round(performance.now() - start);
    if (!last.length) {
      const what = [
        args.text !== undefined && `text ${quote(args.text)}`,
        args.textGone !== undefined && `text gone ${quote(args.textGone)}`,
        args.url !== undefined && `URL contains ${quote(args.url)}`,
        args.target && `target ${args.state ?? 'visible'}`,
        args.settled && 'page settled',
      ]
        .filter(Boolean)
        .join(', ');
      return withPageState(ctx, args.observe, {
        data: { met: what, ms: elapsed },
        lines: [`wait · met: ${what} after ${elapsed} ms`],
      });
    }
    if (elapsed + POLL_MS > budget) {
      throw new LayerError('TIMEOUT', `Not met within ${budget} ms: ${last.join('; ')}`, { delivered: false });
    }
    await pause(POLL_MS, ctx.signal);
  }
}
