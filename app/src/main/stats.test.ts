import { describe, expect, it } from "vitest";
import { MIN_BAR, chartSVG, durationParts, formatCount, formatDuration, layoutBars, longDate, niceMax, shortDate } from "./stats";

const day = (date: string, words: number, dictations = words > 0 ? 1 : 0) => ({ date, words, dictations });

describe("formatDuration", () => {
  it("reads seconds, minutes and hours", () => {
    expect(formatDuration(0)).toBe("0 秒");
    expect(formatDuration(45.4)).toBe("45 秒");
    expect(formatDuration(60)).toBe("1 分");
    expect(formatDuration(12 * 60 + 59)).toBe("12 分");
    expect(formatDuration(3 * 3600 + 12 * 60 + 5)).toBe("3 小時 12 分");
    expect(formatDuration(5 * 3600)).toBe("5 小時");
  });
  it("never goes below zero", () => {
    expect(formatDuration(-5)).toBe("0 秒");
  });
  it("splits into number and unit", () => {
    expect(durationParts(3 * 3600 + 12 * 60)).toEqual([
      ["3", "小時"],
      ["12", "分"],
    ]);
    expect(durationParts(30)).toEqual([["30", "秒"]]);
  });
});

describe("formatCount", () => {
  it("groups thousands", () => {
    expect(formatCount(12340)).toBe("12,340");
    expect(formatCount(7)).toBe("7");
    expect(formatCount(999.6)).toBe("1,000");
  });
});

describe("niceMax", () => {
  it("rounds up to 1, 2 or 5 times a power of ten", () => {
    expect(niceMax(0)).toBe(1);
    expect(niceMax(1)).toBe(1);
    expect(niceMax(3)).toBe(5);
    expect(niceMax(120)).toBe(200);
    expect(niceMax(500)).toBe(500);
    expect(niceMax(501)).toBe(1000);
    expect(niceMax(1999)).toBe(2000);
  });
});

describe("layoutBars", () => {
  it("places the days side by side and scales them to the top", () => {
    const { bars, max } = layoutBars([day("2026-10-01", 50), day("2026-10-02", 100)], 210, 100, 10);
    expect(max).toBe(100);
    expect(bars.map((b) => [b.x, b.w])).toEqual([
      [0, 100],
      [110, 100],
    ]);
    expect(bars[1]).toMatchObject({ y: 0, h: 100 });
    expect(bars[0]).toMatchObject({ y: 50, h: 50 });
  });
  it("gives a little a visible height and nothing no height", () => {
    const { bars } = layoutBars([day("2026-10-01", 1), day("2026-10-02", 1000), day("2026-10-03", 0)], 300, 100, 0);
    expect(bars[0].h).toBe(MIN_BAR);
    expect(bars[2].h).toBe(0);
  });
  it("copes with no days", () => {
    expect(layoutBars([], 100, 100, 4)).toEqual({ bars: [], max: 1 });
  });
});

describe("dates", () => {
  it("reads the date as written", () => {
    expect(shortDate("2026-10-05")).toBe("10/5");
    expect(longDate("2026-01-31")).toBe("1 月 31 日");
  });
});

describe("chartSVG", () => {
  const days = Array.from({ length: 14 }, (_, k) => day(`2026-10-${String(5 + k).padStart(2, "0")}`, k % 3 === 0 ? 0 : k * 40));
  const svg = chartSVG(days);
  it("has a bar or a stub for every day and a label for screen readers", () => {
    expect(svg.match(/<title>/g)).toHaveLength(14);
    expect(svg).toContain('role="img"');
    expect(svg).toContain("過去 14 天的聽寫字數");
    expect(svg).toContain('class="b0"');
    expect(svg).toContain('class="b today"');
  });
  it("labels the first day and today", () => {
    expect(svg).toContain(">10/5<");
    expect(svg).toContain(">今天<");
  });
  it("says what each day was", () => {
    expect(svg).toContain("<title>10 月 6 日：40 字，1 次聽寫</title>");
  });
});
