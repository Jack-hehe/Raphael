import type { Stage } from "./progress";

/** Processing stays below 100% until the native shell confirms delivery. */
export function progressPercent(stage: Stage, fraction: number): number {
  const value = Number.isFinite(fraction) ? fraction : 0;
  return stage === "done" ? 99 : Math.min(95, Math.max(0, Math.round(value * 100)));
}

/** The progress caption, compact: `《 42 % 》`. */
export const formatProgress = (percent: number): string => `《 ${percent} % 》`;

export const COMPLETION_MS = 900;
