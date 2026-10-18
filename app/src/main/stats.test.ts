import { describe, expect, it } from "vitest";
import { durationParts, formatCount, formatDuration, longDate, periodSpeed, periodTimeSaved } from "./stats";


describe("period numbers", () => {
  it("time saved is the typing time of the words less the speaking time", () => {
    expect(periodTimeSaved({ words: 400, audio_seconds: 120 })).toBe(480); // 10 min typing - 2 min speaking
    expect(periodTimeSaved({ words: 10, audio_seconds: 60 })).toBe(0); // never below zero
  });
  it("speed is words per minute of speaking, or null without speech", () => {
    expect(periodSpeed({ words: 300, audio_seconds: 120 })).toBe(150);
    expect(periodSpeed({ words: 0, audio_seconds: 0 })).toBeNull();
  });
});

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

describe("dates", () => {
  it("reads the date as written", () => {
    expect(longDate("2026-01-31")).toBe("1 月 31 日");
  });
});
