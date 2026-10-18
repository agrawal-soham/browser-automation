// The worker's privacy context, wired into the three places that need it: the model client's fetch (models.ts), the
// tool-call swap-back (agent-session.ts) and the layer's screenshot masking (the session's `imagePrivacy`). The user's
// learned rules ("hide this") live in chrome.storage.local under one key and are kept in memory, updated on change.
import { createPrivacyContext, refusalText, swapIn } from '@src/privacy';
import type { LearnedRule, PrivacyContext } from '@src/privacy';
import type { ImagePrivacy, Result } from '@src/browser-control';

export const RULES_KEY = 'privacyRules';

let cachedRules: readonly LearnedRule[] = [];

function loadRules(): void {
  if (typeof chrome === 'undefined' || !chrome.storage?.local) return;
  void chrome.storage.local.get(RULES_KEY).then(stored => {
    const list = stored[RULES_KEY];
    cachedRules = Array.isArray(list) ? (list as LearnedRule[]) : [];
  });
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== 'local' || !(RULES_KEY in changes)) return;
    const list = changes[RULES_KEY].newValue;
    cachedRules = Array.isArray(list) ? (list as LearnedRule[]) : [];
  });
}

loadRules();

export const privacy: PrivacyContext = createPrivacyContext({ rules: () => cachedRules });

/** The session hook: personal nodes are painted over, and each final image is allowed to leave. */
export const imagePrivacy: ImagePrivacy = {
  hide: (name, value) => privacy.hideInImage(name, value),
  onMasked: base64 => privacy.images.allow(base64, 'masked-screenshot'),
};

/**
 * Swaps tags in a tool call's arguments for real values. A refused call returns a layer-shaped error result, with
 * nothing dispatched, in place of the call.
 */
export function swapToolArgs(
  args: Record<string, unknown>,
): { ok: true; args: Record<string, unknown> } | { ok: false; result: Result } {
  const swap = swapIn(args, privacy.vault);
  if (swap.ok) return { ok: true, args: swap.args };
  const text = refusalText(swap.reason, swap.text);
  return {
    ok: false,
    result: {
      ok: false,
      text,
      error: { code: 'INVALID_ARGUMENT', message: text, delivered: false, hint: 'Ask the user with ask_user.' },
    },
  };
}

/** Short replies that carry no personal value and that the model must read as they are. */
const PLAIN_REPLIES = new Set(['yes', 'no', 'ok', 'okay', 'done', 'continue', 'skip', 'cancel', 'stop']);

/**
 * A value the user typed into an ask_user card (often the very value a refused tag stood for) goes into the vault, so
 * the next request carries its tag rather than the value. Plain replies such as "yes" stay readable.
 */
export function rememberAnswer(tool: string, result: Result): void {
  if (tool !== 'ask_user' || !result.ok) return;
  const answer = (result.data as { data?: unknown } | undefined)?.data;
  if (typeof answer !== 'string' || PLAIN_REPLIES.has(answer.trim().toLowerCase())) return;
  privacy.vault.tagFor(answer, 'PERSONAL');
}
