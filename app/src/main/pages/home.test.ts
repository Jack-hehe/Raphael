import { afterEach, describe, expect, test } from "vitest";
import { setLocale } from "../i18n";
import type { StatsSummary } from "../types";
import { homeHTML, usageHTML } from "./home";

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
  by_app: [{ app: "Code.exe", dictations: 4, words: 1200 }],
  speed_wpm: 80,
  time_saved_seconds: 1800,
};

/** The page with the history's cards drawn into the main column, as `paint` does. */
const page = (s: StatsSummary = stats): string => homeHTML().replace('<div class="usage"></div>', `<div class="usage">${usageHTML(s)}</div>`);

describe("the main column of the home page", () => {
  test("is the four numbers, then the activity grid", () => {
    const html = page();
    const numbers = html.indexOf('class="icards"');
    const activity = html.indexOf('class="card activity"');
    expect(numbers).toBeGreaterThan(-1);
    expect(activity).toBeGreaterThan(numbers);
    // and all of it comes before the right column
    expect(html.indexOf('class="home-side"')).toBeGreaterThan(activity);
  });

  test("the numbers are four cards and the activity has its grid and its paging", () => {
    const html = usageHTML(stats);
    expect(html.match(/class="icard"/g)).toHaveLength(4);
    expect(html).toContain('class="heat-box"');
    expect(html).toContain('data-act="older"');
    expect(html).toContain('data-act="newer"');
  });

  test("has no personalization card, though the core still counts the programs", () => {
    const html = page();
    expect(stats.by_app).toBeDefined();
    for (const gone of ["persona", "rose", "personaChart", "個人化", "App 類型"]) expect(html).not.toContain(gone);
  });

  test("the page still has the strip, the shortcuts and Raphael's latest words", () => {
    const html = homeHTML();
    expect(html.indexOf('class="stage home-stage"')).toBeLessThan(html.indexOf('class="home-main"'));
    expect(html).toContain('class="card keys-card"');
    expect(html).toContain('class="card turns-card"');
  });
});
