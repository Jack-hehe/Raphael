import { afterEach, describe, expect, test } from "vitest";
import { setLocale } from "../i18n";
import type { StatsSummary } from "../types";
import { overviewHTML, usageHTML } from "./dictation-overview";

afterEach(() => setLocale("zh-TW"));

const period = { dictations: 4, words: 1200, audio_seconds: 300, cost_usd: 0.1 };
const stats: StatsSummary = {
  today: period,
  week: period,
  month: period,
  all: period,
  streak_days: 2,
  by_day: [{ date: "2026-10-20", dictations: 4, words: 1200 }],
  active_days: 3,
  longest_streak_days: 5,
  speed_wpm: 80,
  time_saved_seconds: 1800,
};

/** The page with the history's cards drawn into the main column, as `paint` does. */
const page = (s: StatsSummary = stats): string => overviewHTML().replace('<div class="usage"></div>', `<div class="usage">${usageHTML(s)}</div>`);

describe("the dictation overview", () => {
  test.each(["zh-TW", "en", "ja"] as const)("estimated totals have a quiet localized tooltip in %s", (locale) => {
    setLocale(locale);
    const html = usageHTML({ ...stats, estimated_words: 5000, estimated_dictations: 50 });
    const tip = html.match(/class="icards" title="([^"]*)"/)?.[1];
    expect(tip).toContain("5,000");
    expect(tip).toContain({ "zh-TW": "估算", en: "estimated", ja: "推定" }[locale]);
    expect(usageHTML(stats)).toContain('<div class="icards">');
  });

  test("is the four numbers, then the activity grid", () => {
    const html = page();
    const numbers = html.indexOf('class="icards"');
    const activity = html.indexOf('class="card activity"');
    expect(numbers).toBeGreaterThan(-1);
    expect(activity).toBeGreaterThan(numbers);
    // and all of it comes before the right column
    expect(html.indexOf('class="home-side"')).toBeGreaterThan(activity);
  });

  test("the first number names what the core counts: a CJK character or a Latin word is one", () => {
    // 字 is how Chinese counts mixed text already; English and Japanese call them words.
    const first = (): string => usageHTML(stats).match(/<div class="icard">.*?<\/div><\/div>/)![0];
    expect(first()).toBe('<div class="icard"><div class="v">1,200<small>字</small></div><div class="k">聽寫字數</div></div>');
    setLocale("en");
    expect(first()).toBe('<div class="icard"><div class="v">1,200<small>words</small></div><div class="k">Words dictated</div></div>');
    setLocale("ja");
    expect(first()).toBe('<div class="icard"><div class="v">1,200<small>語</small></div><div class="k">音声入力した語数</div></div>');
  });

  test("the numbers are four cards and the activity has its grid and its paging", () => {
    const html = usageHTML(stats);
    expect(html.match(/class="icard"/g)).toHaveLength(4);
    expect(html).toContain('class="heat-box"');
    expect(html).toContain('data-act="older"');
    expect(html).toContain('data-act="newer"');
  });

  test("has no personalization card", () => {
    const html = page();
    for (const gone of ["persona", "rose", "personaChart", "個人化", "App 類型"]) expect(html).not.toContain(gone);
  });

  test("keeps shortcuts without the old status strip or recent conversation card", () => {
    const html = overviewHTML();
    expect(html).toContain('class="card keys-card"');
    expect(html).not.toContain("home-stage");
    expect(html).not.toContain("turns-card");
  });
});
