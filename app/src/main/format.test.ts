import { describe, expect, test } from "vitest";
import { setLocale } from "./i18n";
import {
  dayLabel,
  describeError,
  formatClock,
  formatLimit,
  formatRetention,
  formatSeconds,
  formatUsd,
  keyReasonText,
  parseLimit,
  parseMisheard,
} from "./format";

test("formatUsd", () => {
  expect(formatUsd(3.42)).toBe("US$3.42");
  expect(formatUsd(0)).toBe("US$0.00");
});

test("formatUsd rounds to cents from a cent up", () => {
  expect(formatUsd(17.2)).toBe("US$17.20");
  expect(formatUsd(0.01)).toBe("US$0.01");
  expect(formatUsd(10)).toBe("US$10.00");
});

test("formatUsd shows small amounts to a hundredth of a cent", () => {
  expect(formatUsd(0.0037418)).toBe("US$0.0037");
  expect(formatUsd(0.004)).toBe("US$0.0040");
  expect(formatUsd(0.42)).toBe("US$0.42");
});

test("formatClock is the local time of day", () => {
  expect(formatClock(Date.parse("2026-10-17T21:42:00+08:00"))).toBe("21:42");
  expect(formatClock(Date.parse("2026-10-17T00:05:00+08:00"))).toBe("00:05");
  // the tests run in Taipei (vitest.config.ts)
  expect(formatClock(Date.parse("2026-10-17T13:42:00Z"))).toBe("21:42");
});

test("dayLabel", () => {
  const now = Date.parse("2026-10-17T21:42:00+08:00");
  expect(dayLabel(now - 3_600_000, now)).toBe("今天");
  expect(dayLabel(now - 86_400_000, now)).toBe("昨天");
  expect(dayLabel(Date.parse("2026-10-15T22:00:00+08:00"), now)).toBe("10 月 15 日");
});

describe("dayLabel goes by calendar days, not by 24 hours", () => {
  const now = Date.parse("2026-10-17T00:30:00+08:00");
  test("23:50 the evening before is yesterday", () => {
    expect(dayLabel(Date.parse("2026-10-16T23:50:00+08:00"), now)).toBe("昨天");
  });
  test("00:10 on the same day is today", () => {
    expect(dayLabel(Date.parse("2026-10-17T00:10:00+08:00"), now)).toBe("今天");
  });
  test("the day before yesterday has a date", () => {
    expect(dayLabel(Date.parse("2026-10-15T23:59:00+08:00"), now)).toBe("10 月 15 日");
  });
});

test("dayLabel has the words of today and yesterday for the middle of a sentence too", () => {
  const now = Date.parse("2026-10-17T12:00:00+08:00");
  expect(dayLabel(now - 3_600_000, now, true)).toBe("今天");
  setLocale("en");
  try {
    expect([dayLabel(now - 3_600_000, now), dayLabel(now - 3_600_000, now, true)]).toEqual(["Today", "today"]);
    expect([dayLabel(now - 86_400_000, now), dayLabel(now - 86_400_000, now, true)]).toEqual(["Yesterday", "yesterday"]);
  } finally {
    setLocale("zh-TW");
  }
});

test("dayLabel says the year when it is not this one", () => {
  const now = Date.parse("2026-01-02T10:00:00+08:00");
  expect(dayLabel(Date.parse("2025-12-31T10:00:00+08:00"), now)).toBe("2025 年 12 月 31 日");
  expect(dayLabel(Date.parse("2026-01-01T10:00:00+08:00"), now)).toBe("昨天");
});

test("formatSeconds", () => {
  expect(formatSeconds(1340)).toBe("1.34 秒");
  expect(formatSeconds(0)).toBe("0.00 秒");
  expect(formatSeconds(12_004)).toBe("12.00 秒");
});

test("formatRetention names the choices of the settings page", () => {
  expect(formatRetention(1)).toBe("24 小時");
  expect(formatRetention(7)).toBe("7 天");
  expect(formatRetention(30)).toBe("30 天");
  expect(formatRetention(365)).toBe("1 年");
  expect(formatRetention(36500)).toBe("一直保留");
  expect(formatRetention(90)).toBe("90 天");
});

describe("describeError says what went wrong in words the page can show", () => {
  test("the core is not there", () => {
    expect(describeError("Raphael's core is not running")).toBe("Raphael 的核心沒有在執行");
    expect(describeError(new Error("[-32000] the core exited"))).toBe("Raphael 的核心沒有在執行");
    expect(describeError("[-32000] the core is not running")).toBe("Raphael 的核心沒有在執行");
  });

  test("the core is slow", () => {
    expect(describeError("[-32001] history.list got no answer within 15s")).toBe("Raphael 的核心沒有及時回應");
  });

  test("an error of the core keeps its message but not its code", () => {
    expect(describeError("[-32602] Invalid params: id must be a whole number")).toBe(
      "Invalid params: id must be a whole number",
    );
  });

  test("anything else is shown as it is", () => {
    expect(describeError("設定已經生效，但無法存檔：disk full")).toBe("設定已經生效，但無法存檔：disk full");
    expect(describeError(new Error("boom"))).toBe("boom");
    expect(describeError({ code: 3 })).toBe('{"code":3}');
  });

  test("a rejection that JSON cannot write still has words, and never throws", () => {
    // It runs in catch blocks and while a page draws its error: throwing there would lose the message.
    const loop: Record<string, unknown> = {};
    loop.self = loop;
    expect(describeError(undefined)).toBe("undefined");
    expect(describeError(Symbol("gone"))).toBe("Symbol(gone)");
    expect(describeError(10n)).toBe("10");
    expect(describeError(loop)).toBe("[object Object]");
  });
});

test("formatLimit leaves out cents that are not there", () => {
  expect(formatLimit(20)).toBe("20");
  expect(formatLimit(10)).toBe("10");
  expect(formatLimit(7.5)).toBe("7.50");
  expect(formatLimit(0)).toBe("0");
});

describe("parseLimit reads what was typed in the monthly limit field", () => {
  test.each([
    ["20", 20],
    ["0", 0],
    [" 7.5 ", 7.5],
    ["10.25", 10.25],
  ])("%s is %d", (text, value) => {
    expect(parseLimit(text)).toBe(value);
  });

  test.each(["", " ", "-1", "abc", "1e3", "1,000", "10 USD", "US$10", ".5", "1.", "Infinity"])(
    "%j is not a limit",
    (text) => {
      expect(parseLimit(text)).toBeNull();
    },
  );
});

describe("keyReasonText says why the key was not taken", () => {
  test("auth and network, as the brief has them", () => {
    expect(keyReasonText("auth")).toBe("金鑰無效");
    expect(keyReasonText("network")).toBe("連不上 OpenRouter，請稍後再試");
  });

  test("the other reasons", () => {
    for (const reason of ["timeout", "server", "rate_limit", "bad_response", "bad_request", "unexpected"]) {
      expect(keyReasonText(reason)).toBe("連不上 OpenRouter，請稍後再試");
    }
    expect(keyReasonText("credits")).toBe("這把金鑰在 OpenRouter 上已經沒有額度");
  });
});

test("parseMisheard splits the spellings typed after 常被聽成", () => {
  expect(parseMisheard("拉菲爾")).toEqual(["拉菲爾"]);
  expect(parseMisheard("Lafia 先生、拉菲爾先生")).toEqual(["Lafia 先生", "拉菲爾先生"]);
  expect(parseMisheard("a, b，c;d；e")).toEqual(["a", "b", "c", "d", "e"]);
  expect(parseMisheard("  、 ,")).toEqual([]);
  expect(parseMisheard("")).toEqual([]);
});
