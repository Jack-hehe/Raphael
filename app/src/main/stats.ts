// The usage numbers of the home page (`stats.summary`), and the ways they are written.
// Everything here is local: the numbers come from this computer's history.

import { t } from "./i18n";

import type { StatsSummary } from "./types";

/**
 * `12340` -> `12,340`, as en-US groups it. Grouped by hand: the first locale-aware format in a
 * process loads the locale data (about 16 ms, against 0.02 ms for the next one), which on a busy
 * CI runner took the first test that draws a count past its timeout.
 */
export const formatCount = (n: number): string => String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g, ",");

/** Words (CJK characters count one each) per minute of speaking in a period; null without speech. */
export const periodSpeed = (p: { words: number; audio_seconds: number }): number | null =>
  p.audio_seconds > 0 ? p.words / (p.audio_seconds / 60) : null;

/** How long, in number and unit parts so the numbers can be set larger: 3 小時 12 分 -> [["3","小時"],["12","分"]]. */
export function durationParts(seconds: number): [string, string][] {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return [[String(s), t("unit.seconds")]];
  const minutes = Math.floor(s / 60);
  if (minutes < 60) return [[String(minutes), t("unit.minutes")]];
  const parts: [string, string][] = [[String(Math.floor(minutes / 60)), t("unit.hours")]];
  if (minutes % 60) parts.push([String(minutes % 60), t("unit.minutes")]);
  return parts;
}

/** `2026-10-05` -> `10 月 5 日` */
export function longDate(date: string): string {
  const [, m, d] = date.split("-").map(Number);
  return t("format.003", { p0: m, p1: d });
}

/** Whether there is anything to show yet. */
export const hasDictations = (s: StatsSummary): boolean => s.all.dictations > 0;
