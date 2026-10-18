// The vault: the only place a real personal value and its tag are kept together. It lives in the service worker's
// memory and is never written to storage or sent anywhere. A value gets one tag for the life of the worker, so the
// model sees the same tag every turn and a value the agent typed is re-tagged when it shows up in the next snapshot.
import { makeTag } from './tags';
import type { TagKind } from './tags';

/** Values shorter than this are not tagged: two letters or digits would match by chance inside other words. */
export const MIN_VALUE_LENGTH = 3;

/** How a value is compared: case and runs of spaces do not make a different value. */
export function normalize(value: string): string {
  return value.replace(/\s+/g, ' ').trim().toLowerCase();
}

export class Vault {
  #byTag = new Map<string, string>();
  #byValue = new Map<string, string>();
  #counters = new Map<TagKind, number>();

  /** The tag for a value: the existing one, or a new one of this kind. Null for a value too short to tag. */
  tagFor(value: string, kind: TagKind): string | null {
    const key = normalize(value);
    if (key.length < MIN_VALUE_LENGTH) return null;
    const known = this.#byValue.get(key);
    if (known) return known;
    const n = (this.#counters.get(kind) ?? 0) + 1;
    this.#counters.set(kind, n);
    const tag = makeTag(kind, n);
    this.#byTag.set(tag, value.trim());
    this.#byValue.set(key, tag);
    return tag;
  }

  /** The real value of an exact tag, or undefined when the vault never issued it. */
  valueOf(tag: string): string | undefined {
    return this.#byTag.get(tag);
  }

  /** Every real value with its tag, longest first, so a value that contains another is replaced whole. */
  entries(): Array<{ value: string; tag: string }> {
    return [...this.#byTag.entries()]
      .map(([tag, value]) => ({ tag, value }))
      .sort((a, b) => b.value.length - a.value.length);
  }

  get size(): number {
    return this.#byTag.size;
  }
}
