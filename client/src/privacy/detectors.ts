// Finds personal values in text: built-in patterns (email, phone, card, national ids), the value of any form field
// whose label names personal data, and the user's learned rules. Patterns that would also match the extension's own
// numbers are written so they do not: a tab id is a plain run of digits, so a phone needs a `+` or separators.
import { looksLikeCard } from './luhn';
import type { TagKind } from './tags';

export interface Hit {
  start: number;
  end: number;
  value: string;
  kind: TagKind;
}

/** A rule the user added ("hide this"): a literal value, a regular expression, or a field label. */
export interface LearnedRule {
  id: string;
  kind: TagKind;
  literal?: string;
  pattern?: string;
  /** A field whose label contains this text has its value hidden. */
  label?: string;
}

const EMAIL = /[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}/g;
// With a leading +, or a (area) code, or digit groups split by spaces, dots or dashes: never a bare digit run.
const PHONE =
  /(?:\+\d{1,3}[\s.-]?\(?\d{1,4}\)?(?:[\s.-]?\d{2,5}){2,4})|(?:\(\d{3}\)\s?\d{3}[\s.-]\d{4})|(?:\b\d{3}[.-]\d{3}[.-]\d{4}\b)|(?:\b[6-9]\d{4}[\s-]\d{5}\b)/g;
const CARD_CANDIDATE = /\b\d(?:[ -]?\d){12,18}\b/g;
const ID_PATTERNS: RegExp[] = [
  /\b\d{3}-\d{2}-\d{4}\b/g, // US SSN
  /\b\d{4}\s\d{4}\s\d{4}\b/g, // Aadhaar
  /\b[A-Z]{5}\d{4}[A-Z]\b/g, // PAN
];

/** Field labels that mark the field's value as personal, and the kind of tag it gets. Order matters: first wins. */
const LABEL_KINDS: Array<[RegExp, TagKind]> = [
  [/e-?mail/i, 'EMAIL'],
  [/phone|mobile|\btel\b|whatsapp/i, 'PHONE'],
  [/birth|\bdob\b/i, 'DATE'],
  [/address|street|city|\bzip\b|postal|pin ?code|apartment|\bstate\b|country/i, 'ADDRESS'],
  [/name|surname/i, 'NAME'],
  [/ssn|social security|passport|aadhaar|\bpan\b|national id|licen[cs]e/i, 'ID'],
];

// A name the user states in plain text: "my name is Jane Doe", "name: Jane Doe". Capitalised words only, at most four.
const STATED_NAME = /\b(?:[Mm]y name is|I am|I'm|[Nn]ame\s*[:=-])\s*([A-Z][\p{L}'-]+(?:\s+[A-Z][\p{L}'-]+){0,3})/gu;

// A field line of the layer's outline. The snapshot writes names and values as JSON-style strings
// (`[e12] textbox "Full name" (focused) = "Jane Doe"`, perception/outline.ts); the layer's other messages use «…».
const QUOTED = String.raw`(?:«([^«»\n]*)»|"((?:[^"\\\n]|\\.)*)")`;
const FIELD_LINE = new RegExp(
  String.raw`\b(?:textbox|searchbox|combobox|spinbutton)\s+${QUOTED}[^\n=]*?=\s+${QUOTED}`,
  'g',
);

export function labelKind(label: string, rules: readonly LearnedRule[]): TagKind | null {
  const lower = label.toLowerCase();
  for (const rule of rules) if (rule.label && lower.includes(rule.label.toLowerCase())) return rule.kind;
  for (const [pattern, kind] of LABEL_KINDS) if (pattern.test(label)) return kind;
  return null;
}

function collect(text: string, pattern: RegExp, kind: TagKind, out: Hit[], accept?: (v: string) => boolean): void {
  for (const m of text.matchAll(pattern)) {
    if (accept && !accept(m[0])) continue;
    out.push({ start: m.index, end: m.index + m[0].length, value: m[0], kind });
  }
}

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/** Every personal value found in a text, overlaps removed (the longer hit wins). */
export function detect(text: string, rules: readonly LearnedRule[] = []): Hit[] {
  const hits: Hit[] = [];
  collect(text, EMAIL, 'EMAIL', hits);
  collect(text, PHONE, 'PHONE', hits);
  collect(text, CARD_CANDIDATE, 'CARD', hits, looksLikeCard);
  for (const p of ID_PATTERNS) collect(text, p, 'ID', hits);

  for (const m of text.matchAll(STATED_NAME)) {
    const start = m.index + m[0].length - m[1].length;
    hits.push({ start, end: start + m[1].length, value: m[1], kind: 'NAME' });
  }

  for (const m of text.matchAll(FIELD_LINE)) {
    const label = m[1] ?? m[2] ?? '';
    const raw = m[3] ?? m[4];
    const kind = labelKind(label, rules);
    if (!kind || raw === undefined) continue;
    // The value as it is written in the text (escapes included), so the replacement covers exactly it.
    const value = raw.replace(/…$/, '');
    if (!value.trim()) continue;
    const start = m.index + m[0].length - raw.length - 1;
    hits.push({ start, end: start + value.length, value, kind });
  }

  for (const rule of rules) {
    if (rule.literal) {
      collect(text, new RegExp(escapeRegExp(rule.literal), 'gi'), rule.kind, hits);
    } else if (rule.pattern) {
      try {
        collect(text, new RegExp(rule.pattern, 'g'), rule.kind, hits);
      } catch {
        // A bad pattern from the editor is skipped, not fatal.
      }
    }
  }
  return dedupe(hits);
}

function dedupe(hits: Hit[]): Hit[] {
  const sorted = hits
    // A field that already shows a tag (`= «[[NAME_1]]»`) is not tagged again.
    .filter(h => h.value.trim().length > 0 && !/\[\[[A-Z]+_\d+\]\]/.test(h.value))
    .sort((a, b) => a.start - b.start || b.end - b.start - (a.end - a.start));
  const out: Hit[] = [];
  for (const h of sorted) {
    const last = out[out.length - 1];
    if (last && h.start < last.end) {
      if (h.end - h.start > last.end - last.start) out[out.length - 1] = h;
      continue;
    }
    out.push(h);
  }
  return out;
}
