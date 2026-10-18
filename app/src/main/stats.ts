// The usage numbers of the home page (`stats.summary`), and the 14-day chart drawn from them.
// Everything here is local: the numbers come from this computer's history.

import type { StatsSummary } from "./types";
import { esc } from "./ui";

/** `12340` -> `12,340` */
export const formatCount = (n: number): string => Math.round(n).toLocaleString("en-US");

/** How long, in words: `45 秒`, `12 分`, `3 小時 12 分`, `5 小時`. */
export function formatDuration(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} 秒`;
  const minutes = Math.floor(s / 60);
  if (minutes < 60) return `${minutes} 分`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours} 小時 ${rest} 分` : `${hours} 小時`;
}

/** The same, split into number and unit parts so the numbers can be set larger: 3 小時 12 分 -> [["3","小時"],["12","分"]]. */
export function durationParts(seconds: number): [string, string][] {
  const words = formatDuration(seconds).split(" ");
  const parts: [string, string][] = [];
  for (let k = 0; k < words.length; k += 2) parts.push([words[k], words[k + 1]]);
  return parts;
}

/** The smallest of 1, 2, 5, 10, 20, 50, ... that is at least `n` (and at least 1), the top of the chart. */
export function niceMax(n: number): number {
  if (!(n > 0)) return 1;
  const power = 10 ** Math.floor(Math.log10(n));
  for (const step of [1, 2, 5, 10]) if (step * power >= n) return step * power;
  return 10 * power;
}

export interface Bar {
  x: number;
  y: number;
  w: number;
  h: number;
  date: string;
  words: number;
  dictations: number;
}

export interface ChartLayout {
  bars: Bar[];
  /** The value at the top of the chart. */
  max: number;
}

/** The smallest height a day with dictations gets, so that a little is not drawn as nothing. */
export const MIN_BAR = 3;

/**
 * Places a bar for each day in a `width` x `height` box, the days side by side with `gap` between,
 * each as high as its words against the top of the chart. A day without words has no height.
 */
export function layoutBars(days: StatsSummary["by_day"], width: number, height: number, gap: number): ChartLayout {
  const max = niceMax(Math.max(0, ...days.map((d) => d.words)));
  const n = days.length;
  const w = n > 0 ? (width - gap * (n - 1)) / n : 0;
  const bars = days.map((d, k) => {
    const h = d.words > 0 ? Math.max(MIN_BAR, (d.words / max) * height) : 0;
    return { x: k * (w + gap), y: height - h, w, h, date: d.date, words: d.words, dictations: d.dictations };
  });
  return { bars, max };
}

/** `2026-10-05` -> `10/5` (read as written: no time zone moves the day). */
export function shortDate(date: string): string {
  const [, m, d] = date.split("-").map(Number);
  return `${m}/${d}`;
}

/** `2026-10-05` -> `10 月 5 日` */
export function longDate(date: string): string {
  const [, m, d] = date.split("-").map(Number);
  return `${m} 月 ${d} 日`;
}

const CHART_W = 560;
const CHART_H = 96;
const GAP = 6;
/** Space under the bars for the dates. */
const AXIS_H = 20;

/**
 * The chart of words per day as inline SVG. Each bar has a title (date and words) for the mouse; the
 * whole chart has a label that says the same for a screen reader. Days without dictation show a
 * flat stub on the baseline so the fourteen days still read as fourteen.
 */
export function chartSVG(days: StatsSummary["by_day"]): string {
  const { bars, max } = layoutBars(days, CHART_W, CHART_H, GAP);
  const total = days.reduce((sum, d) => sum + d.words, 0);
  const label = `過去 ${days.length} 天的聽寫字數，共 ${formatCount(total)} 字`;
  const rects = bars
    .map((b, k) => {
      const today = k === bars.length - 1;
      const tip = `${longDate(b.date)}：${formatCount(b.words)} 字，${b.dictations} 次聽寫`;
      const shape =
        b.h > 0
          ? `<rect class="b${today ? " today" : ""}" x="${b.x.toFixed(1)}" y="${b.y.toFixed(1)}" width="${b.w.toFixed(1)}" height="${b.h.toFixed(1)}" rx="3"/>`
          : `<rect class="b0" x="${b.x.toFixed(1)}" y="${CHART_H - 2}" width="${b.w.toFixed(1)}" height="2" rx="1"/>`;
      // the hit area is the whole column, so a short bar is as easy to point at as a tall one
      return `<g><title>${esc(tip)}</title><rect class="hit" x="${b.x.toFixed(1)}" y="0" width="${b.w.toFixed(1)}" height="${CHART_H}"/>${shape}</g>`;
    })
    .join("");
  const first = bars[0];
  const last = bars[bars.length - 1];
  const axis =
    first && last
      ? `<text x="0" y="${CHART_H + 15}" class="ax">${esc(shortDate(first.date))}</text>` +
        `<text x="${CHART_W}" y="${CHART_H + 15}" class="ax" text-anchor="end">今天</text>`
      : "";
  return (
    `<svg class="chart" viewBox="0 0 ${CHART_W} ${CHART_H + AXIS_H}" role="img" aria-label="${esc(label)}">` +
    `<line class="grid" x1="0" x2="${CHART_W}" y1="0.5" y2="0.5"/>` +
    `<line class="base" x1="0" x2="${CHART_W}" y1="${CHART_H + 0.5}" y2="${CHART_H + 0.5}"/>` +
    rects +
    axis +
    `</svg>` +
    `<div class="chart-max num" aria-hidden="true">${esc(formatCount(max))} 字</div>`
  );
}

/** Whether there is anything to show yet. */
export const hasDictations = (s: StatsSummary): boolean => s.all.dictations > 0;
