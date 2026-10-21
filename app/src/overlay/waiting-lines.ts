// How the waiting lines change beside the emblem while Raphael works on a question, so that the
// wait does not look like a hang. Each is a short report of his on the analysis (《告。解析中……》):
// they take the place of a dictation's percentage and are written the same way, like every line
// on this page (overlay.css). The phrase book is fixed, no model is involved, and like every word
// on this page it comes from the app (notices.rs), in the interface language and in that one alone.
//
// Pure and browser-free, so that the tests run it in Node.

/** How long one line stays before the next one replaces it, in milliseconds. */
export const WAITING_LINE_MS = 1500;

/**
 * A line of `lines` (which must not be empty) picked by `random` (a number from 0 up to but not
 * including 1, like `Math.random`), never `previous`, so that the line always visibly changes. A
 * book of one line has no other to change to.
 */
export function nextWaitingLine(lines: readonly string[], previous: string | null, random: () => number = Math.random): string {
  const others = lines.filter((line) => line !== previous);
  const pool = others.length > 0 ? others : lines;
  const value = random();
  const at = Number.isFinite(value) ? Math.floor(value * pool.length) : 0;
  return pool[Math.min(pool.length - 1, Math.max(0, at))];
}
