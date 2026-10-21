/** The longest a line of Raphael's stays on screen, in milliseconds. */
export const MAX_NOTICE_MS = 12_000;

/**
 * How long a line of Raphael's stays on screen, in milliseconds: 1.5 s plus 0.15 s for every
 * character of the line, and never more than 12 s. Characters are counted as code
 * points, so a rare character outside the Basic Multilingual Plane counts once.
 */
export function noticeDurationMs(line: string): number {
  return Math.min(MAX_NOTICE_MS, 1500 + 150 * [...line].length);
}
