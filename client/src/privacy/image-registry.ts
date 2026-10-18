// The list of images allowed to leave the browser: screenshots after the layer painted over every personal value, and
// images the user attached on purpose. The network wrapper blocks a request that carries any other image. The images
// travel unchanged from the layer to the request body (base64 PNG; nothing resizes or re-encodes them), so an exact
// fingerprint of the base64 text is enough to recognise them.

const MAX_KEPT = 200;

/** cyrb53: a fast 53-bit string hash. Not for secrets; it only tells our own images apart from any other. */
function fingerprint(base64: string): string {
  let h1 = 0xdeadbeef;
  let h2 = 0x41c6ce57;
  for (let i = 0; i < base64.length; i++) {
    const ch = base64.charCodeAt(i);
    h1 = Math.imul(h1 ^ ch, 2654435761);
    h2 = Math.imul(h2 ^ ch, 1597334677);
  }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
  return `${base64.length}:${(4294967296 * (2097151 & h2) + (h1 >>> 0)).toString(36)}`;
}

/** The base64 payload of a data URL, or the text itself when it is bare base64. */
export function base64Of(image: string): string {
  const comma = image.startsWith('data:') ? image.indexOf(',') : -1;
  return comma >= 0 ? image.slice(comma + 1) : image;
}

export type ImageSource = 'masked-screenshot' | 'user-attachment';

export class ImageRegistry {
  #allowed = new Map<string, ImageSource>();

  allow(image: string, source: ImageSource): void {
    const key = fingerprint(base64Of(image));
    this.#allowed.delete(key);
    this.#allowed.set(key, source);
    while (this.#allowed.size > MAX_KEPT) this.#allowed.delete(this.#allowed.keys().next().value!);
  }

  /** Where an outgoing image came from, or undefined for an image nobody registered (it must not leave). */
  sourceOf(image: string): ImageSource | undefined {
    return this.#allowed.get(fingerprint(base64Of(image)));
  }
}

/** The worker's one registry: the layer's screenshot path and the attachment loader both write to it. */
export const imageRegistry = new ImageRegistry();
