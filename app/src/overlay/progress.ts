// How far a dictation has come once its key is released: while the speech that is left is
// transcribed (0 to 60 %), while the brain cleans the text (60 to 95 %, an estimate) and when the
// text is there (100 %). The overlay page writes it as a percentage beside the emblem
// (overlay.ts). It is called the arc from when it was drawn as one around the emblem.
//
// The core reports how many segments are transcribed; the cleanup has no such count, so the arc
// eases towards the end of its range over the time the cleanup is expected to take and stops
// short of it until the reply comes. The arc never goes back.
//
// Pure: no canvas, no clock of its own. The overlay controller advances it with a timer.

export type Stage = "transcribing" | "cleaning" | "done";

/** A `progress` event from overlay.rs. */
export interface ProgressReport {
  /** Localized by the native shell; absent until the first core report arrives. */
  label?: string;
  stage: Stage;
  /** Segments transcribed so far (transcribing). */
  done: number;
  /** Segments in all, as far as the core knows yet (transcribing). */
  total: number;
  /** Seconds of speech: the longer it is, the longer the cleanup is expected to take. */
  audioSeconds: number;
}

/** The part of the arc each stage fills, as fractions of the whole. */
export const ARC = {
  transcribing: [0, 0.6],
  cleaning: [0.6, 0.95],
} as const;

/**
 * How long the cleanup is expected to take, in seconds, for `audioSeconds` of speech. It is the
 * cleanup's timeout, min(15, 3 + audio / 20), scaled down to what a call usually takes.
 */
export const expectedCleanupSeconds = (audioSeconds: number): number =>
  1.5 + 0.02 * (Number.isFinite(audioSeconds) && audioSeconds > 0 ? audioSeconds : 0);

/**
 * How much of the cleaning range the estimate has filled `t` seconds into the cleanup: 90 % at
 * the expected time, closer and closer to all of it after that, never all of it.
 */
export function cleaningEstimate(t: number, expected: number): number {
  if (!(t > 0)) return 0;
  return 1 - Math.pow(0.1, t / expected);
}

/** How quickly the arc catches up with the counts, seconds (the time constant). */
const FOLLOW = 0.12;

const ORDER: Record<Stage, number> = { transcribing: 0, cleaning: 1, done: 2 };

export class ProgressArc {
  private stage: Stage | null = null;
  private counted = 0;
  private cleaningFor = 0;
  private expected = expectedCleanupSeconds(0);
  private value = 0;
  private stageSeconds = 0;

  get elapsedSeconds(): number {
    return this.stageSeconds;
  }

  /** Whether a dictation's progress is being followed. */
  get active(): boolean {
    return this.stage !== null;
  }

  /** The latest accepted stage, also used by the overlay's progress caption. */
  get currentStage(): Stage | null {
    return this.stage;
  }

  /** The fraction of the arc filled now, 0 to 1 (0 when inactive). */
  get fraction(): number {
    return this.stage === null ? 0 : this.value;
  }

  /** A new report from the core (or the stop's reply, `done`). Reports from an earlier stage than
   * the one shown come late and are ignored. */
  report(r: ProgressReport): void {
    if (this.stage !== null && ORDER[r.stage] < ORDER[this.stage]) return;
    const entering = this.stage !== r.stage;
    if (entering) this.stageSeconds = 0;
    this.stage = r.stage;
    if (r.stage === "transcribing") {
      const total = Number.isFinite(r.total) && r.total > 0 ? r.total : 0;
      const done = Number.isFinite(r.done) && r.done > 0 ? Math.min(r.done, total) : 0;
      const share = total === 0 ? 1 : done / total;
      this.counted = Math.max(this.counted, ARC.transcribing[0] + (ARC.transcribing[1] - ARC.transcribing[0]) * share);
    } else if (r.stage === "cleaning") {
      if (entering) {
        this.cleaningFor = 0;
        this.expected = expectedCleanupSeconds(r.audioSeconds);
      }
    } else {
      this.value = 1;
    }
  }

  /** No arc, at once. */
  reset(): void {
    this.stage = null;
    this.counted = 0;
    this.cleaningFor = 0;
    this.value = 0;
    this.stageSeconds = 0;
  }

  /** Where the arc is heading now. */
  private target(): number {
    switch (this.stage) {
      case null:
        return 0;
      case "transcribing":
        return this.counted;
      case "cleaning": {
        const share = cleaningEstimate(this.cleaningFor, this.expected);
        const [from, to] = ARC.cleaning;
        return Math.max(this.counted, from + (to - from) * share);
      }
      case "done":
        return 1;
    }
  }

  /** Moves the arc on by `dt` seconds. */
  advance(dt: number): void {
    if (this.stage === null || !(dt > 0)) return;
    this.stageSeconds += dt;
    if (this.stage === "cleaning") this.cleaningFor += dt;
    const target = this.target();
    const next = this.value + (target - this.value) * (1 - Math.exp(-dt / FOLLOW));
    this.value = Math.min(1, Math.max(this.value, next));
  }
}
