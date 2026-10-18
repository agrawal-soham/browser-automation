import { describe, expect, it } from 'vitest';
import { settleTracker } from '../tools/wait';

describe('settleTracker', () => {
  it('waits for the document to load', () => {
    const settle = settleTracker(500);
    expect(settle('loading', 3, 0)).toBe('the page is still loading (loading)');
    expect(settle('interactive', 3, 1_000)).toBe('the page is still loading (interactive)');
  });

  it('needs the mutation count unchanged for the quiet time', () => {
    const settle = settleTracker(500);
    expect(settle('complete', 10, 0)).toBe('the page is still changing');
    expect(settle('complete', 10, 400)).toBe('the page is still changing');
    expect(settle('complete', 10, 500)).toBeNull();
  });

  it('starts the quiet time again after each change', () => {
    const settle = settleTracker(500);
    settle('complete', 10, 0);
    expect(settle('complete', 12, 450)).toBe('the page is still changing');
    expect(settle('complete', 12, 900)).toBe('the page is still changing');
    expect(settle('complete', 12, 950)).toBeNull();
  });
});
