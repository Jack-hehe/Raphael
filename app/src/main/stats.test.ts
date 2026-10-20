import { describe, expect, it } from "vitest";
import { durationParts, formatCount, longDate, periodSpeed } from "./stats";


describe("period numbers", () => {
  it("speed is words per minute of speaking, or null without speech", () => {
    expect(periodSpeed({ words: 300, audio_seconds: 120 })).toBe(150);
    expect(periodSpeed({ words: 0, audio_seconds: 0 })).toBeNull();
  });
});

describe("durationParts", () => {
  it("splits into number and unit", () => {
    expect(durationParts(3 * 3600 + 12 * 60)).toEqual([
      ["3", "小時"],
      ["12", "分"],
    ]);
    expect(durationParts(30)).toEqual([["30", "秒"]]);
  });
  it("leaves out minutes that are not there, and never goes below zero", () => {
    expect(durationParts(5 * 3600)).toEqual([["5", "小時"]]);
    expect(durationParts(12 * 60 + 59)).toEqual([["12", "分"]]);
    expect(durationParts(-5)).toEqual([["0", "秒"]]);
  });
});

describe("formatCount", () => {
  it("groups thousands", () => {
    expect(formatCount(12340)).toBe("12,340");
    expect(formatCount(7)).toBe("7");
    expect(formatCount(999.6)).toBe("1,000");
  });
  it("writes what en-US writes, at every length", () => {
    const written: Record<number, string> = {
      0: "0", 999: "999", 1000: "1,000", 1001: "1,001", 99_999: "99,999", 100_000: "100,000",
      1_234_567: "1,234,567", 9_876_543_210: "9,876,543,210", [-1234.4]: "-1,234", 12.5: "13",
    };
    for (const [n, text] of Object.entries(written)) expect(formatCount(Number(n)), n).toBe(text);
  });
});

describe("dates", () => {
  it("reads the date as written", () => {
    expect(longDate("2026-01-31")).toBe("1 月 31 日");
  });
});
