// The last look at a request before it leaves the browser. It does not trust the redactor: it searches the redacted
// body again for every real value in the vault and runs the detectors once more, and it checks that every image is one
// the registry allowed. Any finding blocks the whole request.
import { detect } from './detectors';
import type { LearnedRule } from './detectors';
import type { ImageRegistry } from './image-registry';
import { decoded, isImageString, mapStrings, valuePattern } from './redactor';
import type { Vault } from './vault';

export interface Leak {
  reason: 'vault-value' | 'detector-hit' | 'unknown-image';
  /** What was found, for the user's notice: a kind and a length, never the value itself. */
  detail: string;
}

export class LeakBlocked extends Error {
  constructor(readonly leaks: Leak[]) {
    super(
      `Privacy check blocked the request: ${leaks.length} problem${leaks.length === 1 ? '' : 's'} (${leaks
        .map(l => l.detail)
        .join('; ')}). Nothing was sent.`,
    );
    this.name = 'LeakBlocked';
  }
}

export function findLeaks(
  body: unknown,
  vault: Vault,
  registry: ImageRegistry,
  rules: readonly LearnedRule[] = [],
): Leak[] {
  const leaks: Leak[] = [];
  const values = vault.entries();
  mapStrings(body, (s, key) => {
    if (isImageString(s, key)) {
      if (!registry.sourceOf(s)) leaks.push({ reason: 'unknown-image', detail: `an image not masked by the layer` });
      return s;
    }
    for (const { value, tag } of values) {
      const p = valuePattern(value);
      if (p.test(s)) leaks.push({ reason: 'vault-value', detail: `the value of ${tag}` });
    }
    // Encoded text is checked as it would read once decoded.
    const texts = decoded(s) === s ? [s] : [s, decoded(s)];
    for (const hit of texts.flatMap(t => detect(t, rules))) {
      leaks.push({ reason: 'detector-hit', detail: `a ${hit.kind} of ${hit.value.length} characters` });
    }
    return s;
  });
  return leaks;
}
