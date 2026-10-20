import { afterEach, describe, expect, test } from "vitest";
import { setLocale } from "../i18n";
import { PANES } from "./settings";

afterEach(() => setLocale("zh-TW"));

describe("the tabs of the settings", () => {
  test("the calendar tab comes after Raphael's and before the usage", () => {
    expect(PANES.map((p) => p.id)).toEqual(["general", "dictation", "agent", "calendar", "usage", "about"]);
  });

  test("it is called 行事曆, Calendar and カレンダー", () => {
    const calendar = PANES.find((p) => p.id === "calendar")!;
    expect(calendar.icon).toBe("calendar");
    expect(calendar.title()).toBe("行事曆");
    setLocale("en");
    expect(calendar.title()).toBe("Calendar");
    setLocale("ja");
    expect(calendar.title()).toBe("カレンダー");
  });

  test("every tab has a name in every language", () => {
    for (const locale of ["zh-TW", "en", "ja"] as const) {
      setLocale(locale);
      const names = PANES.map((p) => p.title());
      expect(names.every((name) => name.length > 0)).toBe(true);
      expect(new Set(names).size).toBe(PANES.length);
    }
  });
});
