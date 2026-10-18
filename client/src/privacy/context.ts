// One privacy context for the whole service worker: the vault, the image registry, the user's learned rules and a
// short log of what was sent. The extension builds it once (src/extension/privacy.ts) and hands it to the network
// wrapper, the tool-call swap-back and the layer's screenshot masking, so all three agree on the same values.
import { detect, labelKind } from './detectors';
import type { LearnedRule } from './detectors';
import { imageRegistry } from './image-registry';
import type { ImageRegistry } from './image-registry';
import type { SentRecord } from './net-wrapper';
import { normalize, Vault } from './vault';

const MAX_RECORDS = 50;

export interface PrivacyContext {
  vault: Vault;
  images: ImageRegistry;
  rules(): readonly LearnedRule[];
  record(entry: SentRecord): void;
  recent(): readonly SentRecord[];
  /**
   * Whether an element of the page must be painted over in a screenshot: its name or value holds a value the vault
   * knows, or a detector finds personal data in it. The layer calls it for every node of the outline.
   */
  hideInImage(name: string, value: string | undefined): boolean;
}

export function createPrivacyContext(options: {
  rules?: () => readonly LearnedRule[];
  vault?: Vault;
  images?: ImageRegistry;
}): PrivacyContext {
  const vault = options.vault ?? new Vault();
  const images = options.images ?? imageRegistry;
  const rules = options.rules ?? (() => []);
  const records: SentRecord[] = [];
  const holdsPersonal = (text: string): boolean => {
    if (!text) return false;
    const n = normalize(text);
    for (const { value } of vault.entries()) if (n.includes(normalize(value))) return true;
    return detect(text, rules()).length > 0;
  };
  return {
    vault,
    images,
    rules,
    record(entry) {
      records.push(entry);
      if (records.length > MAX_RECORDS) records.shift();
    },
    recent: () => records,
    // A field labelled as personal ("Full name", "Email") hides any value it holds, known to the vault or not: a
    // page can pre-fill the user's own name, which no pattern would find.
    hideInImage: (name, value) =>
      holdsPersonal(name) || holdsPersonal(value ?? '') || (!!value?.trim() && labelKind(name, rules()) !== null),
  };
}
