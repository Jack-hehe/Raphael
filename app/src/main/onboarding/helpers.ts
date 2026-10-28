// Small rules of the guide that do not belong to one step: the limit that was typed, the
// microphones to choose from, how the model bar reads, and asking the core again for what it has
// not written yet. All pure (the wait is given), so each is tested on its own.

import { parseLimit } from "../format";
import { type ModelPhase } from "../models";

export type LimitAction = { kind: "keep" } | { kind: "save"; value: number } | { kind: "invalid" };

/** What the text in the limit field means for the limit that is set now. */
export function limitAction(text: string, current: number): LimitAction {
  const value = parseLimit(text);
  if (value === null) return { kind: "invalid" };
  return value === current ? { kind: "keep" } : { kind: "save", value };
}


/** How loud the microphone has to be, and for how long, before the guide says it hears the person. */
export const HEARD_LEVEL = 0.05;
export const HEARD_MS = 300;

/**
 * Whether the person has been heard: the level stayed above `HEARD_LEVEL` for `HEARD_MS`. Give it the
 * time the level first rose (null: it is not up) and the new reading; it answers the new rise time
 * and whether that is long enough. A reading under the line starts over.
 */
export function heardAfter(since: number | null, level: number, now: number): { since: number | null; heard: boolean } {
  if (level <= HEARD_LEVEL) return { since: null, heard: false };
  const from = since ?? now;
  return { since: from, heard: now - from >= HEARD_MS };
}

/** How far the model download is, 0 to 100, or null while that is not known. */
export function modelPercent(phase: ModelPhase): number | null {
  if (phase.kind === "ready") return 100;
  if (phase.kind === "installing") return 100;
  if (phase.kind !== "downloading" || phase.total <= 0) return null;
  return Math.min(100, Math.round((phase.done / phase.total) * 100));
}

/** The pauses before each further look at the core for something that is being written. */
export const LOOKUP_DELAYS_MS: readonly number[] = [700, 1500];

/**
 * Asks `fetch` for the newest of something and gives every answer to `accept`, which says whether it
 * is the one that was waited for. What a notification announces may not be in the core's records
 * yet, so a look that finds nothing is repeated after each of `LOOKUP_DELAYS_MS`. `wait` is how the
 * time passes (a test gives its own). A core that does not answer counts as nothing found.
 * Resolves with the first answer that was accepted, or null.
 */
export async function lookUp<T>(
  fetch: () => Promise<T>,
  accept: (value: T) => boolean,
  wait: (ms: number) => Promise<void> = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  delays: readonly number[] = LOOKUP_DELAYS_MS,
): Promise<T | null> {
  for (let attempt = 0; attempt <= delays.length; attempt++) {
    if (attempt > 0) await wait(delays[attempt - 1]);
    try {
      const value = await fetch();
      if (accept(value)) return value;
    } catch {
      // the core did not answer; the next look may be luckier
    }
  }
  return null;
}
