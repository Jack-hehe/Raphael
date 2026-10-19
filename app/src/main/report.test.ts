import { afterEach, describe, expect, test } from "vitest";
import { setLocale } from "./i18n";
import { buildReport, coreDownReport } from "./report";
import type { Status } from "./types";

const now = Date.parse("2026-10-17T21:42:00+08:00");

afterEach(() => setLocale("zh-TW"));

/** A `status.get` result: how OpenRouter is, what this month cost and the monthly limit. */
function status(openrouter: Partial<Status["openrouter"]>, month: number, limit: number): Status {
  return {
    openrouter: {
      state: "ok",
      latency_ms: 280,
      checked_at_ms: now,
      error: null,
      key: null,
      usage: null,
      ...openrouter,
    },
    spend: { month_usd: month, today_usd: 0, limit_usd: limit, forecast_usd: 0, daily: [] },
    last: null,
  };
}

test("no key comes first", () => {
  expect(buildReport(status({ state: "no_key" }, 0, 10), now).text).toBe(
    "《告。尚未設定 OpenRouter 金鑰。設定前，聽寫只會貼上未整理的文字。》",
  );
});

test("no key: the action", () => {
  const r = buildReport(status({ state: "no_key" }, 0, 10), now);
  expect(r.action).toEqual({ label: "設定金鑰", target: "settings" });
});

test("no key beats everything else", () => {
  const r = buildReport(status({ state: "no_key", error: "network" }, 25, 10), now);
  expect(r.text).toContain("尚未設定 OpenRouter 金鑰");
});

test("offline offers a retry", () => {
  const r = buildReport(status({ state: "error", error: "network" }, 3.42, 20), now);
  expect(r.action).toEqual({ label: "重新連線", target: "retry" });
});

test("offline copy", () => {
  const r = buildReport(status({ state: "error", error: "network" }, 3.42, 20), now);
  expect(r.text).toBe("《告。無法連線到 OpenRouter。聽寫會先貼上未整理的文字。》");
});

test("offline beats the budget", () => {
  const r = buildReport(status({ state: "error", error: "network" }, 25, 20), now);
  expect(r.text).toContain("無法連線到 OpenRouter");
});

describe("errors other than network", () => {
  test("a key that does not work says so and points to the key", () => {
    const r = buildReport(status({ state: "error", error: "auth" }, 3.42, 20), now);
    expect(r.text).toBe("《告。OpenRouter 金鑰無效或無法讀取。聽寫會先貼上未整理的文字。》");
    expect(r.action).toEqual({ label: "設定金鑰", target: "settings" });
  });

  test.each(["timeout", "server", "rate_limit", "credits", "bad_response", "unexpected", null])(
    "%s uses the offline copy",
    (error) => {
      const r = buildReport(status({ state: "error", error }, 3.42, 20), now);
      expect(r.text).toBe("《告。無法連線到 OpenRouter。聽寫會先貼上未整理的文字。》");
      expect(r.action).toEqual({ label: "重新連線", target: "retry" });
    },
  );
});

test("a status that is not known yet is not an error", () => {
  const r = buildReport(status({ state: "unknown" }, 3.42, 20), now);
  expect(r.text).toBe("《告。一切正常。本月額度已使用 17%。》");
});

describe("the budget", () => {
  test("used up", () => {
    const r = buildReport(status({ state: "ok" }, 20, 20), now);
    expect(r.text).toBe("《告。本月額度已用完，聽寫先不整理。》");
    expect(r.action).toEqual({ label: "調整上限", target: "settings" });
  });

  test("being at the limit is being over it, as in the core", () => {
    expect(buildReport(status({ state: "ok" }, 10, 10), now).text).toContain("已用完");
    expect(buildReport(status({ state: "ok" }, 10.01, 10), now).text).toContain("已用完");
  });

  test("a limit of 0 is always used up", () => {
    expect(buildReport(status({ state: "ok" }, 0, 0), now).text).toContain("已用完");
  });

  test("a warning from 80 percent", () => {
    expect(buildReport(status({ state: "ok" }, 15.9, 20), now).text).toContain("一切正常");
    const r = buildReport(status({ state: "ok" }, 16, 20), now);
    expect(r.text).toContain("本月額度已使用 80%");
    expect(r.action).toEqual({ label: "調整上限", target: "settings" });
  });

  test("budget warning predicts the day", () => {
    expect(buildReport(status({ state: "ok" }, 17.2, 20), now).text).toBe(
      "《告。本月額度已使用 86%。照目前的速度，10 月 20 日會用完。》",
    );
  });

  test("the predicted day can be in the next month", () => {
    // 29.5 of 30 on the 31st: US$0.95 a day, 0.5 left, so the next day
    const lateOctober = Date.parse("2026-10-31T21:42:00+08:00");
    expect(buildReport(status({ state: "ok" }, 29.5, 30), lateOctober).text).toContain("11 月 1 日會用完");
  });
});

test("normal report has no action", () => {
  const r = buildReport(status({ state: "ok" }, 3.42, 20), now);
  expect(r.text).toBe("《告。一切正常。本月額度已使用 17%。》");
  expect(r.action).toBeUndefined();
});

test("the percentage is rounded", () => {
  expect(buildReport(status({ state: "ok" }, 0.07, 10), now).text).toContain("已使用 1%");
  expect(buildReport(status({ state: "ok" }, 0, 10), now).text).toContain("已使用 0%");
});

test("when the core does not answer", () => {
  const r = coreDownReport();
  expect(r.text.startsWith("《告。")).toBe(true);
  expect(r.text.endsWith("》")).toBe(true);
  expect(r.action).toEqual({ label: "重新連線", target: "retry" });
});

describe("one language only: the language of the window", () => {
  const CJK_OR_KANA = /[぀-ヿ㐀-鿿]/;

  test("Japanese", () => {
    setLocale("ja");
    expect(buildReport(status({ state: "no_key" }, 0, 10), now).text).toBe(
      "《告。OpenRouter のキーが未設定です。整形前のテキストのみ貼り付けます。》",
    );
    expect(buildReport(status({ state: "error", error: "network" }, 3.42, 20), now).text).toBe(
      "《告。OpenRouter に接続できません。整形前のテキストを貼り付けます。》",
    );
    expect(buildReport(status({ state: "error", error: "auth" }, 3.42, 20), now).text).toBe(
      "《告。OpenRouter のキーが無効です。整形前のテキストを貼り付けます。》",
    );
    expect(buildReport(status({ state: "ok" }, 20, 20), now).text).toBe("《告。今月の予算を使い切りました。整形せずに貼り付けます。》");
    expect(buildReport(status({ state: "ok" }, 17.2, 20), now).text).toBe(
      "《告。今月の予算の 86% を使用。このペースでは 10月20日に上限です。》",
    );
    expect(buildReport(status({ state: "ok" }, 3.42, 20), now).text).toBe("《告。全システム正常。今月の予算は 17% 使用済みです。》");
    expect(coreDownReport().text).toBe("《告。コアが応答しません。音声入力は今は使えません。》");
  });

  test("English writes 《Notice. …》 and has no Chinese or Japanese in it", () => {
    setLocale("en");
    const reports = [
      buildReport(status({ state: "no_key" }, 0, 10), now),
      buildReport(status({ state: "error", error: "auth" }, 3.42, 20), now),
      buildReport(status({ state: "error", error: "network" }, 3.42, 20), now),
      buildReport(status({ state: "ok" }, 20, 20), now),
      buildReport(status({ state: "ok" }, 17.2, 20), now),
      buildReport(status({ state: "ok" }, 3.42, 20), now),
      coreDownReport(),
    ];
    for (const report of reports) {
      expect(report.text).toMatch(/^《Notice\. .+》$/);
      expect(report.text.replace(/[《》]/g, "")).not.toMatch(CJK_OR_KANA);
    }
    expect(reports[4].text).toBe("《Notice. 86% of this month's budget is used. At this pace it runs out on 10/20.》");
    expect(reports[5].text).toBe("《Notice. All is well. 17% of this month's budget is used.》");
    expect(reports[2].action).toEqual({ label: "Reconnect", target: "retry" });
  });
});
