// Puts real values back into a tool call's arguments at the moment it runs, and nowhere earlier: the conversation
// keeps the tags, so real values never re-enter what is sent to the model. Matching is strict. A tag the vault never
// issued, or text that looks like a bent tag (`[EMAIL_1]`, `[[email_1]]`, a bare `NAME_2`), refuses the whole call:
// typing a guessed value or the literal placeholder into a form is worse than asking the user.
import { NEAR_MISS_PATTERN, TAG_PATTERN } from './tags';
import type { Vault } from './vault';

export type SwapResult =
  | { ok: true; args: Record<string, unknown>; swapped: number }
  | { ok: false; reason: 'unknown-tag' | 'near-miss'; text: string };

/** The error a refused call returns to the agent; the server turns it into ask_user (see server pipeline). */
export function refusalText(reason: 'unknown-tag' | 'near-miss', text: string): string {
  return reason === 'unknown-tag'
    ? `error UNKNOWN_TAG (nothing dispatched)\nThe placeholder ${text} is not a known value. Ask the user with ask_user for the value to use.`
    : `error MALFORMED_TAG (nothing dispatched)\nThe text ${JSON.stringify(text)} looks like a changed placeholder. Copy placeholders exactly as [[KIND_N]], or ask the user with ask_user.`;
}

export function swapIn(args: Record<string, unknown>, vault: Vault): SwapResult {
  let swapped = 0;
  let refused: { reason: 'unknown-tag' | 'near-miss'; text: string } | null = null;
  const walk = (v: unknown): unknown => {
    if (refused) return v;
    if (typeof v === 'string') {
      const replaced = v.replace(TAG_PATTERN, tag => {
        const real = vault.valueOf(tag);
        if (real === undefined) {
          refused ??= { reason: 'unknown-tag', text: tag };
          return tag;
        }
        swapped++;
        return real;
      });
      // Exact tags are gone from `rest`; anything tag-like left over is a bent tag.
      const rest = v.replace(TAG_PATTERN, '');
      const bent = NEAR_MISS_PATTERN.exec(rest);
      if (bent) refused ??= { reason: 'near-miss', text: bent[0] };
      return replaced;
    }
    if (Array.isArray(v)) return v.map(walk);
    if (v && typeof v === 'object') return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, walk(x)]));
    return v;
  };
  const out = walk(args) as Record<string, unknown>;
  if (refused) return { ok: false, ...(refused as { reason: 'unknown-tag' | 'near-miss'; text: string }) };
  return { ok: true, args: out, swapped };
}
