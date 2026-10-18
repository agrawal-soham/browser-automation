// The placeholder format that stands in for a personal value in everything sent to the model: `[[EMAIL_1]]`.
// Plain ASCII, so a tokenizer keeps it whole and a model copies it exactly. The near-miss patterns catch a model that
// changed a tag (single brackets, lower case, the other bracket style, a bare kind), so a bent tag is refused rather
// than typed into a page as literal text.

/** The kinds of personal value the redactor knows. A learned rule names one of these. */
export const TAG_KINDS = ['NAME', 'EMAIL', 'PHONE', 'ADDRESS', 'CARD', 'ID', 'DATE', 'PERSONAL'] as const;
export type TagKind = (typeof TAG_KINDS)[number];

export function makeTag(kind: TagKind, n: number): string {
  return `[[${kind}_${n}]]`;
}

/** An exact tag, anywhere in a string. Global: use with matchAll or replace. */
export const TAG_PATTERN = /\[\[([A-Z]+)_(\d+)\]\]/g;

/**
 * Text that looks like a tag but is not exact: `[EMAIL_1]`, `[[email_1]]`, `⟦EMAIL_1⟧`, `{{EMAIL_1}}`, a bare `EMAIL_1`,
 * or a tag without its number. Checked after the exact tags are taken out of the string.
 */
const KIND_ALT = TAG_KINDS.join('|');
export const NEAR_MISS_PATTERN = new RegExp(
  [
    `\\[\\[?\\s*(?:${KIND_ALT})(?:_\\d+)?\\s*\\]?\\]`,
    `⟦\\s*(?:${KIND_ALT})_?\\d*\\s*⟧`,
    `\\{\\{\\s*(?:${KIND_ALT})_?\\d*\\s*\\}\\}`,
    `\\b(?:${KIND_ALT})_\\d+\\b`,
  ].join('|'),
  'i',
);

/** The exact tags in a string, in order. */
export function tagsIn(text: string): string[] {
  return [...text.matchAll(TAG_PATTERN)].map(m => m[0]);
}
