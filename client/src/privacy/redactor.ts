// Replaces personal values with tags in text and in a whole request body. The order matters: values the vault already
// knows are replaced first, so a value the agent typed is re-tagged when the page shows it again, even if no pattern
// would find it; then the detectors run and every new hit gets a tag. Images are collected, never changed.
import { detect } from './detectors';
import type { LearnedRule } from './detectors';
import type { Vault } from './vault';

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/**
 * A value as a pattern that ignores case and the width of the spaces in it, and also matches the value as a URL or a
 * submitted form encodes it (`jane.doe%40example.com`, `Jane+Doe`): pages echo submitted data back in that form.
 */
export function valuePattern(value: string): RegExp {
  const words = value.trim().split(/\s+/);
  const forms = new Set([words.map(escapeRegExp).join('\\s+')]);
  const uri = words.map(w => encodeURIComponent(w));
  forms.add(uri.map(escapeRegExp).join('(?:%20|\\+)'));
  return new RegExp([...forms].join('|'), 'gi');
}

/** The text with %-escapes and form-encoded spaces decoded; the text itself when it holds none or does not decode. */
export function decoded(text: string): string {
  if (!/%[0-9A-Fa-f]{2}/.test(text)) return text;
  try {
    return decodeURIComponent(text.replace(/\+/g, ' '));
  } catch {
    return text;
  }
}

export function redactText(text: string, vault: Vault, rules: readonly LearnedRule[] = []): string {
  // Values that appear only encoded are found in the decoded copy and put in the vault first; the vault step below
  // then replaces their encoded form in the text itself.
  const plain = decoded(text);
  if (plain !== text) for (const h of detect(plain, rules)) vault.tagFor(h.value, h.kind);
  let out = text;
  for (const { value, tag } of vault.entries()) out = out.replace(valuePattern(value), tag);
  const hits = detect(out, rules);
  // From the end, so earlier positions stay valid.
  for (let i = hits.length - 1; i >= 0; i--) {
    const h = hits[i];
    const tag = vault.tagFor(h.value, h.kind);
    if (tag) out = out.slice(0, h.start) + tag + out.slice(h.end);
  }
  return out;
}

const IMAGE_KEYS = new Set(['data', 'image', 'base64', 'url', 'image_url', 'bytes']);
const BASE64_LIKE = /^[A-Za-z0-9+/=\s]+$/;

/** Whether a string in a request body is an image: a data URL, or long base64 under an image-ish key. */
export function isImageString(value: string, key?: string): boolean {
  if (value.startsWith('data:image/')) return true;
  return !!key && IMAGE_KEYS.has(key) && value.length > 256 && BASE64_LIKE.test(value.slice(0, 512));
}

/** Calls `visit` on every string of a JSON value with its key; the returned string replaces it. */
export function mapStrings(value: unknown, visit: (s: string, key?: string) => string, key?: string): unknown {
  if (typeof value === 'string') return visit(value, key);
  if (Array.isArray(value)) return value.map(v => mapStrings(v, visit));
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [k, mapStrings(v, visit, k)]));
  }
  return value;
}

export interface RedactedBody {
  body: unknown;
  images: string[];
}

/** Passes over a body at most this many times; each pass after the first only applies tags a later string issued. */
const MAX_PASSES = 4;

/**
 * A copy of a parsed request body with every text redacted; images are returned untouched for the image check. A tag
 * issued while reading one string must also replace that value in strings read before it, so the body is redacted
 * again until a pass issues no new tag.
 */
export function redactBody(body: unknown, vault: Vault, rules: readonly LearnedRule[] = []): RedactedBody {
  let images: string[] = [];
  let current = body;
  for (let pass = 0; pass < MAX_PASSES; pass++) {
    const before = vault.size;
    images = [];
    current = mapStrings(current, (s, key) => {
      if (isImageString(s, key)) {
        images.push(s);
        return s;
      }
      return redactText(s, vault, rules);
    });
    if (vault.size === before) break;
  }
  return { body: current, images };
}
