// The usage numbers of the home page (`stats.summary`), and the ways they are written.
// Everything here is local: the numbers come from this computer's history.

import { t } from "./i18n";

import type { StatsSummary } from "./types";

/** `12340` -> `12,340` */
export const formatCount = (n: number): string => Math.round(n).toLocaleString("en-US");

/** How long, in words: `45 秒`, `12 分`, `3 小時 12 分`, `5 小時`. */
export function formatDuration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return t("format.005", { p0: s });
  const minutes = Math.floor(s / 60);
  if (minutes < 60) return t("stats.001", { p0: minutes });
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? t("stats.002", { p0: hours, p1: rest }) : t("stats.003", { p0: hours });
}

/** Typing speed the time saved is measured against, as the core's TYPING_WPM (brain/stats.py). */
export const TYPING_WPM = 40;

/** The typing time a period's dictations saved: its words typed at TYPING_WPM, less the time spent
 * speaking them. The core's time_saved_seconds is for all time; the home page shows this month. */
export const periodTimeSaved = (p: { words: number; audio_seconds: number }): number =>
  Math.max(0, (p.words / TYPING_WPM) * 60 - p.audio_seconds);

/** Words (CJK characters count one each) per minute of speaking in a period; null without speech. */
export const periodSpeed = (p: { words: number; audio_seconds: number }): number | null =>
  p.audio_seconds > 0 ? p.words / (p.audio_seconds / 60) : null;

/** The same, split into number and unit parts so the numbers can be set larger: 3 小時 12 分 -> [["3","小時"],["12","分"]]. */
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
