// The privacy layer's one entry: the extension imports from here only.
export { createPrivacyContext } from './context';
export type { PrivacyContext } from './context';
export type { LearnedRule } from './detectors';
export { imageRegistry } from './image-registry';
export { LeakBlocked } from './leak-check';
export { privacyFetch, REDACTION_HEADER, REDACTION_SCHEME } from './net-wrapper';
export type { SentRecord } from './net-wrapper';
export { refusalText, swapIn } from './swap-back';
export { TAG_KINDS } from './tags';
export type { TagKind } from './tags';
