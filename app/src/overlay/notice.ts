/**
 * How long Raphael's report stays on screen, in milliseconds: 1.5 s plus 0.15 s for every
 * character of the Chinese line (spec §12.5). Characters are counted as code points, so a rare
 * character outside the Basic Multilingual Plane counts once.
 *
 * Kept apart from `overlay.ts` so that it needs no browser (the tests run in Node).
 */
export function noticeDurationMs(zh: string): number {
  return 1500 + 150 * [...zh].length;
}
