// The fetch the model client is built with (src/extension/models.ts passes it to createModel). Every request to the
// model goes through it: the JSON body is redacted, checked for leaks and sent with the redaction header; a body that
// still leaks is not sent at all. The reply is passed through unchanged: tags in the model's tool calls stay tags in
// the conversation, and are swapped for real values only when a tool runs (swap-back.ts).
import { LeakBlocked, findLeaks } from './leak-check';
import type { Leak } from './leak-check';
import { isImageString, redactBody } from './redactor';
import type { ImageRegistry } from './image-registry';
import type { PrivacyContext } from './context';

export const REDACTION_HEADER = 'X-Redaction-Scheme';
export const REDACTION_SCHEME = 'typed-tags-v1';

/** What one request looked like before and after redaction, for the side panel's "sent vs. seen" view. */
export interface SentRecord {
  at: number;
  url: string;
  tagsIssued: number;
  images: number;
  /** Images replaced by a notice because the layer did not mask them (for example one restored after a restart). */
  withheld: number;
  blocked?: Leak[];
}

export const WITHHELD_IMAGE_TEXT = '[screenshot withheld: it was not masked by the privacy layer]';

function holdsUnknownImage(v: unknown, registry: ImageRegistry, key?: string): boolean {
  if (typeof v === 'string') return isImageString(v, key) && !registry.sourceOf(v);
  if (Array.isArray(v)) return v.some(x => holdsUnknownImage(x, registry));
  if (v && typeof v === 'object') return Object.entries(v).some(([k, x]) => holdsUnknownImage(x, registry, k));
  return false;
}

/**
 * Replaces every content part that carries an image the registry does not know with a text part saying so. The part is
 * dropped as a whole, so the request keeps a valid shape; the run goes on without that image.
 */
export function withholdUnknownImages(body: unknown, registry: ImageRegistry): { body: unknown; withheld: number } {
  let withheld = 0;
  const walk = (v: unknown): unknown => {
    if (Array.isArray(v)) {
      return v.map(item => {
        if (item && typeof item === 'object' && !Array.isArray(item) && holdsUnknownImage(item, registry)) {
          const part = item as Record<string, unknown>;
          // A content part (it has a type): swap it for text. Anything else is walked into.
          if (typeof part.type === 'string') {
            withheld++;
            return { type: 'text', text: WITHHELD_IMAGE_TEXT };
          }
        }
        return walk(item);
      });
    }
    if (v && typeof v === 'object') return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, walk(x)]));
    return v;
  };
  return { body: walk(body), withheld };
}

export function privacyFetch(base: typeof fetch, ctx: PrivacyContext): typeof fetch {
  return async (input, init) => {
    if (typeof init?.body !== 'string') return base(input, init);
    let parsed: unknown;
    try {
      parsed = JSON.parse(init.body);
    } catch {
      // Not JSON: there is no model request without a JSON body, so nothing personal is assumed here; still checked.
      const leaks = findLeaks(init.body, ctx.vault, ctx.images, ctx.rules());
      if (leaks.length) throw new LeakBlocked(leaks);
      return base(input, init);
    }
    const rules = ctx.rules();
    const before = ctx.vault.size;
    const kept = withholdUnknownImages(parsed, ctx.images);
    const { body, images } = redactBody(kept.body, ctx.vault, rules);
    const leaks = findLeaks(body, ctx.vault, ctx.images, rules);
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
    ctx.record({
      at: Date.now(),
      url,
      tagsIssued: ctx.vault.size - before,
      images: images.length,
      withheld: kept.withheld,
      ...(leaks.length ? { blocked: leaks } : {}),
    });
    if (leaks.length) throw new LeakBlocked(leaks);
    const headers = new Headers(init.headers);
    headers.set(REDACTION_HEADER, REDACTION_SCHEME);
    return base(input, { ...init, headers, body: JSON.stringify(body) });
  };
}
