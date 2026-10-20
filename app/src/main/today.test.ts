import { afterEach, describe, expect, test } from "vitest";
import { setLocale } from "./i18n";
import { MAX_SHOWN, currentEvent, isOver, pick, timeText, todayHTML } from "./today";
import type { CalendarEvent, CalendarToday } from "./types";

afterEach(() => setLocale("zh-TW"));

/** The time on 2026-10-20 in Taipei (the tests run in that zone), in ms. */
const at = (day: number, hour: number, minute = 0): number => Date.UTC(2026, 9, day, hour - 8, minute);
const DATE = "2026-10-20";

const timed = (title: string, start: number, end: number): CalendarEvent => ({ title, start_ms: start, end_ms: end, all_day: false });
const allDay = (title: string): CalendarEvent => ({ title, start_ms: at(20, 0), end_ms: at(21, 0), all_day: true });

const answer = (events: CalendarEvent[], extra: Partial<CalendarToday> = {}): CalendarToday => ({
  date: DATE,
  calendars: 1,
  events,
  failed: [],
  ...extra,
});

const STANDUP = timed("Standup", at(20, 9, 30), at(20, 10));
const LUNCH = timed("Lunch", at(20, 12), at(20, 13));
const REVIEW = timed("Review", at(20, 15), at(20, 16));

const html = (today: CalendarToday, now: number): string => todayHTML({ kind: "ready", today }, now);

describe("the event that is marked", () => {
  const events = [allDay("Holiday"), STANDUP, LUNCH, REVIEW];

  test("the one going on now", () => {
    expect(currentEvent(events, at(20, 9, 45))).toBe(1);
    expect(currentEvent(events, at(20, 12, 30))).toBe(2);
  });

  test("else the next to start", () => {
    expect(currentEvent(events, at(20, 8))).toBe(1);
    expect(currentEvent(events, at(20, 10, 30))).toBe(2);
    expect(currentEvent(events, at(20, 13))).toBe(3); // an event is over at its end
  });

  test("none once the last one is over, and an all-day event is never the one", () => {
    expect(currentEvent(events, at(20, 16))).toBe(-1);
    expect(currentEvent([allDay("Holiday")], at(20, 9))).toBe(-1);
    expect(currentEvent([], at(20, 9))).toBe(-1);
  });

  test("a moment is the next event until its time and over a millisecond later", () => {
    const moment = timed("Call", at(20, 14), at(20, 14));
    expect(currentEvent([moment], at(20, 13, 59))).toBe(0);
    expect(isOver(moment, at(20, 14))).toBe(false);
    expect(isOver(moment, at(20, 14) + 1)).toBe(true);
  });

  test("an event that began yesterday and runs on is going on", () => {
    expect(currentEvent([timed("Night", at(19, 23), at(20, 2))], at(20, 1))).toBe(0);
  });
});

describe("the time of an event", () => {
  test("a range, a moment and all day", () => {
    expect(timeText(STANDUP, DATE)).toBe("09:30–10:00");
    expect(timeText(timed("Call", at(20, 14), at(20, 14)), DATE)).toBe("14:00");
    expect(timeText(allDay("Holiday"), DATE)).toBe("全天");
  });

  test("an event across midnight says only the part that is on the day", () => {
    expect(timeText(timed("Night", at(19, 23), at(20, 2)), DATE)).toBe("至 02:00");
    expect(timeText(timed("Night", at(20, 23), at(21, 2)), DATE)).toBe("23:00 起");
    expect(timeText(timed("Trip", at(19, 20), at(21, 8)), DATE)).toBe("全天");
    expect(timeText(timed("Evening", at(20, 22), at(21, 0)), DATE)).toBe("22:00–00:00"); // ends at midnight: still the day's
  });

  test("in the language of the window", () => {
    setLocale("en");
    expect(timeText(allDay("Holiday"), DATE)).toBe("All day");
    expect(timeText(timed("Night", at(19, 23), at(20, 2)), DATE)).toBe("Until 02:00");
    setLocale("ja");
    expect(timeText(timed("Night", at(20, 23), at(21, 2)), DATE)).toBe("23:00 から");
  });
});

describe("a long day", () => {
  const many = Array.from({ length: 12 }, (_, n) => timed(`E${n}`, at(20, 6 + n), at(20, 6 + n, 30)));

  test("a day that fits lists all of it", () => {
    expect(pick(many.slice(0, MAX_SHOWN), at(20, 12))).toEqual({ shown: many.slice(0, MAX_SHOWN), more: 0 });
  });

  test("a longer one drops what is over first and says how many it leaves out", () => {
    const { shown, more } = pick(many, at(20, 10)); // E0 to E3 are over
    expect(shown.map((e) => e.title)).toEqual(["E4", "E5", "E6", "E7", "E8", "E9", "E10", "E11"]);
    expect(more).toBe(0);
    const early = pick(many, at(20, 5));
    expect(early.shown.map((e) => e.title)).toEqual(["E0", "E1", "E2", "E3", "E4", "E5", "E6", "E7"]);
    expect(early.more).toBe(4);
  });

  test("when all of it is over the last ones are shown", () => {
    expect(pick(many, at(20, 23)).shown.map((e) => e.title)).toEqual(["E4", "E5", "E6", "E7", "E8", "E9", "E10", "E11"]);
  });

  test("the card says how many more, and still marks the right one", () => {
    const out = html(answer(many), at(20, 5));
    expect(out).toContain("還有 4 項");
    expect(out.match(/is-current/g)).toHaveLength(1);
    expect(out).toContain('aria-current="time"');
  });
});

describe("the card", () => {
  test("the events, with their times and titles, the marked one and the ones that are over", () => {
    const out = html(answer([allDay("Holiday"), STANDUP, LUNCH, REVIEW]), at(20, 12, 15));
    const items = out.split("<li ").slice(1);
    expect(items).toHaveLength(4);
    expect(items[0]).toContain("全天");
    expect(items[0]).toContain("Holiday");
    expect(items[0]).not.toContain("is-over");
    expect(items[1]).toContain("09:30–10:00");
    expect(items[1]).toContain("is-over");
    expect(items[2]).toContain("is-current");
    expect(items[2]).toContain('aria-current="time"');
    expect(items[2]).toContain("12:00–13:00");
    expect(items[3]).not.toContain("is-current");
    expect(items[3]).not.toContain("is-over");
    expect(out.match(/aria-current/g)).toHaveLength(1);
  });

  test("its heading says today and the date", () => {
    const out = html(answer([STANDUP]), at(20, 8));
    expect(out).toContain('<h2 id="today-h">今天</h2>');
    expect(out).toContain("10 月 20 日");
    setLocale("en");
    expect(html(answer([STANDUP]), at(20, 8))).toContain('<h2 id="today-h">Today</h2>');
  });

  test("a day without events says so", () => {
    const out = html(answer([]), at(20, 8));
    expect(out).toContain("今天沒有行程。");
    expect(out).not.toContain("<ul");
    expect(out).not.toContain("data-go");
  });

  test("with no calendar connected: one line and a button to the Calendar tab of the settings", () => {
    const out = html(answer([], { calendars: 0 }), at(20, 8));
    expect(out).toContain("還沒有連接行事曆。");
    expect(out).toContain('data-go="settings" data-pane="calendar"');
    expect(out).toContain("連接行事曆");
    expect(out).not.toContain("今天沒有行程");
    expect(out).not.toContain("<ul");
    setLocale("ja");
    expect(html(answer([], { calendars: 0 }), at(20, 8))).toContain("カレンダーを接続");
  });

  test("while it loads: the heading and the placeholder, no claim about the day", () => {
    const out = todayHTML({ kind: "loading" }, at(20, 8));
    expect(out).toContain('<h2 id="today-h">今天</h2>');
    expect(out).toContain("page-loading");
    expect(out).not.toContain("今天沒有行程");
    expect(out).not.toContain("data-go");
  });

  test("a calendar that could not be read is named, with a way to the settings; the events of the others stay", () => {
    const failed = [{ id: 2, label: "Work", reason: "timeout" }];
    const out = html(answer([STANDUP], { calendars: 2, failed }), at(20, 8));
    expect(out).toContain("09:30–10:00");
    expect(out).toContain("無法讀取：Work");
    expect(out).toContain('data-go="settings" data-pane="calendar"');
    expect(out).not.toContain("今天沒有行程");
  });

  test("an empty day is not claimed when none of the calendars could be read", () => {
    const failed = [
      { id: 1, label: "Personal", reason: "network" },
      { id: 2, label: "Work", reason: "network" },
    ];
    const out = html(answer([], { calendars: 2, failed }), at(20, 8));
    expect(out).toContain("無法讀取：Personal、Work");
    expect(out).not.toContain("今天沒有行程");
    // but an empty day is claimed when only some could not be read
    expect(html(answer([], { calendars: 2, failed: failed.slice(0, 1) }), at(20, 8))).toContain("今天沒有行程。");
  });

  test("titles and labels are escaped, and an event without a title has a name", () => {
    const out = html(
      answer([timed("<img src=x onerror=alert(1)>", at(20, 9), at(20, 10)), timed("", at(20, 11), at(20, 12))], {
        failed: [{ id: 1, label: "<b>x</b>", reason: "http" }],
      }),
      at(20, 8),
    );
    expect(out).not.toContain("<img");
    expect(out).not.toContain("<b>x</b>");
    expect(out).toContain("&lt;img src=x onerror=alert(1)&gt;");
    expect(out).toContain("（未命名）");
  });
});
