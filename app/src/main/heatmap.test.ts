import { describe, expect, test } from "vitest";
import { buildGrid, heatmapSVG, level, monthLabels, weeksThatFit } from "./heatmap";

/** `n` days up to 2026-10-14 (a Wednesday), oldest first, with words from `words(k)` for day k from today. */
function days(n: number, words: (back: number) => number = () => 0) {
  const out = [];
  for (let k = n - 1; k >= 0; k--) {
    const d = new Date(Date.UTC(2026, 9, 14, 12) - k * 86_400_000);
    const w = words(k);
    out.push({ date: d.toISOString().slice(0, 10), dictations: w > 0 ? 1 : 0, words: w });
  }
  return out;
}

describe("level", () => {
  test("four tints by the share of the busiest day, none for no words", () => {
    expect(level(0, 100)).toBe(0);
    expect(level(10, 100)).toBe(1);
    expect(level(30, 100)).toBe(2);
    expect(level(60, 100)).toBe(3);
    expect(level(100, 100)).toBe(4);
    expect(level(5, 0)).toBe(0);
  });
});

describe("buildGrid", () => {
  test("columns are weeks from Sunday; the last one holds today and the days to come", () => {
    const grid = buildGrid(days(30, (k) => (k === 0 ? 50 : 0)), 3);
    expect(grid.weeks).toHaveLength(3);
    const last = grid.weeks[2];
    expect(last[0].date).toBe("2026-10-11"); // Sunday
    expect(last[3]).toMatchObject({ date: "2026-10-14", words: 50, state: 4 });
    expect(last[4].state).toBe("future");
    expect(last[6].state).toBe("future");
  });

  test("days before the first dictation, or not in the list, have no data", () => {
    const grid = buildGrid(days(10, (k) => (k === 2 ? 5 : 0)), 3);
    const cells = grid.weeks.flat();
    const at = (date: string) => cells.find((c) => c.date === date)!;
    expect(at("2026-09-27").state).toBe("nodata"); // before the list
    expect(at("2026-10-11").state).toBe("nodata"); // before the first dictation (the 12th)
    expect(at("2026-10-12").state).toBe(4);
    expect(at("2026-10-13").state).toBe(0); // after it, a day without words
  });

  test("paging back shows older weeks, and says when there are none older", () => {
    const list = days(182, () => 10);
    expect(buildGrid(list, 10).older).toBe(true);
    const back = buildGrid(list, 10, 10);
    expect(back.weeks[9][0].date).toBe("2026-08-02");
    expect(back.weeks[9].every((c) => c.state === 4)).toBe(true);
    expect(buildGrid(list, 10, 20).older).toBe(false);
  });

  test("nothing to lay out", () => {
    expect(buildGrid([], 5)).toEqual({ weeks: [], older: false });
  });
});

describe("labels and drawing", () => {
  test("a month is named at the first column it shows up in", () => {
    const labels = monthLabels(buildGrid(days(60, () => 1), 9).weeks);
    expect(labels.map((l) => l.text)).toEqual(["8 月", "9 月", "10 月"].filter((t) => labels.some((l) => l.text === t)));
    expect(labels[labels.length - 1].text).toBe("10 月");
  });

  test("weeks that fit a width stay between 6 and 53", () => {
    expect(weeksThatFit(100)).toBe(6);
    expect(weeksThatFit(500)).toBe(22);
    expect(weeksThatFit(5000)).toBe(53);
  });

  test("the SVG has a square per day, a title per known day and a label", () => {
    const svg = heatmapSVG(buildGrid(days(14, (k) => (k < 3 ? 100 : 0)), 2));
    expect(svg.match(/<rect class="(h\d|future|nodata)"/g)).toHaveLength(14);
    expect(svg).toContain("<title>10 月 14 日：100 字</title>");
    expect(svg).toContain('aria-label="10 月 4 日到10 月 17 日，有 3 天聽寫，共 300 字"');
  });
});
