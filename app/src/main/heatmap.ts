// The activity grid of the home page: one square a day, a column a week (Sunday on top), tinted by
// how many words were dictated that day. Days the history no longer knows (before the first day it
// has, or pruned by the retention) are hatched; days still to come in this week are outlines.

import { formatCount, longDate } from "./stats";
import type { StatsSummary } from "./types";
import { esc } from "./ui";

export type CellState = "future" | "nodata" | 0 | 1 | 2 | 3 | 4;

export interface Cell {
  date: string;
  words: number;
  dictations: number;
  state: CellState;
}

/** `2026-10-05` -> a Date at noon UTC that day (noon: no time zone can move it to another day). */
const parse = (date: string): Date => new Date(`${date}T12:00:00Z`);
const iso = (d: Date): string => d.toISOString().slice(0, 10);
const addDays = (d: Date, n: number): Date => new Date(d.getTime() + n * 86_400_000);

/** The tint of a day with `words` words when the busiest day shown has `max`: 1 to 4, 0 for none. */
export function level(words: number, max: number): 0 | 1 | 2 | 3 | 4 {
  if (words <= 0 || max <= 0) return 0;
  const share = words / max;
  if (share > 0.75) return 4;
  if (share > 0.5) return 3;
  if (share > 0.25) return 2;
  return 1;
}

export interface Grid {
  /** Columns of seven days, Sunday first, oldest column first. */
  weeks: Cell[][];
  /** Whether there are older weeks to page back to (within what `by_day` holds). */
  older: boolean;
}

/**
 * Lays `byDay` (oldest first, today last) out in `count` week columns, the newest of them `back`
 * columns before the one of today. The tints are measured against the busiest day in view.
 */
export function buildGrid(byDay: StatsSummary["by_day"], count: number, back = 0): Grid {
  if (byDay.length === 0 || count <= 0) return { weeks: [], older: false };
  const known = new Map(byDay.map((d) => [d.date, d]));
  const today = parse(byDay[byDay.length - 1].date);
  const firstKnown = parse(byDay[0].date);
  // The first day with a dictation: days before it hold no data, even when `by_day` lists them.
  const firstUsed = byDay.find((d) => d.dictations > 0)?.date;
  const thisSunday = addDays(today, -today.getUTCDay());
  const lastSunday = addDays(thisSunday, -7 * back);
  const firstSunday = addDays(lastSunday, -7 * (count - 1));

  const weeks: Cell[][] = [];
  for (let w = 0; w < count; w++) {
    const column: Cell[] = [];
    for (let d = 0; d < 7; d++) {
      const day = addDays(firstSunday, 7 * w + d);
      const date = iso(day);
      const entry = known.get(date);
      let state: CellState;
      if (day > today) state = "future";
      else if (!entry || !firstUsed || date < firstUsed) state = "nodata";
      else state = 0;
      column.push({ date, words: entry?.words ?? 0, dictations: entry?.dictations ?? 0, state });
    }
    weeks.push(column);
  }
  const max = Math.max(0, ...weeks.flat().filter((c) => c.state === 0).map((c) => c.words));
  for (const cell of weeks.flat()) if (cell.state === 0) cell.state = level(cell.words, max);
  return { weeks, older: firstSunday > firstKnown };
}

/** The months that begin in the view: the column where each first shows up, and its name. */
export function monthLabels(weeks: Cell[][]): { column: number; text: string }[] {
  const labels: { column: number; text: string }[] = [];
  let last = -1;
  weeks.forEach((column, k) => {
    const month = Number(column[0].date.slice(5, 7));
    if (month !== last) {
      // a month that starts in the last days of the first column is named from the next one
      if (k > 0 || Number(column[0].date.slice(8, 10)) <= 7) labels.push({ column: k, text: `${month} 月` });
      last = month;
    }
  });
  return labels;
}

const DAY_NAMES = ["日", "一", "二", "三", "四", "五", "六"];
export const CELL = 13;
export const GAP = 3;
const PITCH = CELL + GAP;
const LEFT = 20;
const BOTTOM = 18;

/** How many weeks fit in `width` px. */
export const weeksThatFit = (width: number): number => Math.max(6, Math.min(53, Math.floor((width - LEFT + GAP) / PITCH)));

/** The grid as inline SVG; each day has a title for the mouse, and the whole a label for a screen reader. */
export function heatmapSVG(grid: Grid): string {
  const weeks = grid.weeks;
  const width = LEFT + weeks.length * PITCH - GAP;
  const height = 7 * PITCH - GAP + BOTTOM;
  const days = weeks.flat().filter((c) => c.state !== "future" && c.state !== "nodata");
  const words = days.reduce((sum, c) => sum + c.words, 0);
  const active = days.filter((c) => c.dictations > 0).length;
  const label =
    weeks.length > 0
      ? `${longDate(weeks[0][0].date)}到${longDate(weeks[weeks.length - 1][6].date)}，有 ${active} 天聽寫，共 ${formatCount(words)} 字`
      : "沒有資料";
  const cells = weeks
    .map((column, w) =>
      column
        .map((c, d) => {
          const x = LEFT + w * PITCH;
          const y = d * PITCH;
          const cls = typeof c.state === "number" ? `h${c.state}` : c.state;
          const tip =
            c.state === "future" ? "" : c.state === "nodata" ? `${longDate(c.date)}：沒有紀錄` : `${longDate(c.date)}：${formatCount(c.words)} 字`;
          return `<rect class="${cls}" x="${x}" y="${y}" width="${CELL}" height="${CELL}" rx="3">${tip ? `<title>${esc(tip)}</title>` : ""}</rect>`;
        })
        .join(""),
    )
    .join("");
  const rows = DAY_NAMES.map((name, d) =>
    d % 2 === 1 ? `<text class="ax" x="0" y="${d * PITCH + CELL - 2}">${name}</text>` : "",
  ).join("");
  const months = monthLabels(weeks)
    .map((m) => `<text class="ax" x="${LEFT + m.column * PITCH}" y="${7 * PITCH - GAP + 14}">${esc(m.text)}</text>`)
    .join("");
  return (
    `<svg class="heat" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(label)}">` +
    `<defs><pattern id="hatch" width="4" height="4" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">` +
    `<rect width="4" height="4" class="hatch-bg"/><line x1="0" y1="0" x2="0" y2="4" class="hatch-line"/></pattern></defs>` +
    rows +
    cells +
    months +
    `</svg>`
  );
}
