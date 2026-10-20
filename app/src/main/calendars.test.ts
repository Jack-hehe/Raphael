import { afterEach, describe, expect, test, vi } from "vitest";
import { type CalendarApi, CalendarTab, calendarFormRows, calendarListRows, calendarReasonText, maskedHost } from "./calendars";
import { setLocale } from "./i18n";
import type { CalendarInfo } from "./types";

afterEach(() => setLocale("zh-TW"));

const SECRET = "https://calendar.google.com/calendar/ical/me%40example.com/private-0123456789abcdef/basic.ics";
const PERSONAL: CalendarInfo = { id: 1, label: "Personal", host: "calendar.google.com" };
const WORK: CalendarInfo = { id: 2, label: "Work", host: "example.org" };

function fakeApi(over: Partial<CalendarApi> = {}): CalendarApi & { add: ReturnType<typeof vi.fn>; test: ReturnType<typeof vi.fn>; remove: ReturnType<typeof vi.fn> } {
  return {
    add: vi.fn(async (label: string) => ({ ok: true as const, calendar: { id: 7, label, host: "calendar.google.com" }, events_today: 3 })),
    test: vi.fn(async () => ({ ok: true as const, events_today: 2 })),
    remove: vi.fn(async () => ({ removed: true })),
    ...over,
  } as never;
}

const tabWith = (api: CalendarApi, items: CalendarInfo[] = []): CalendarTab => {
  const tab = new CalendarTab(api);
  tab.reset(items);
  return tab;
};

describe("why a calendar was not added", () => {
  const reasons = ["url", "not_found", "denied", "http", "timeout", "network", "too_large", "not_calendar", "duplicate", "limit", "missing"];

  test("each word the core answers with has a sentence of its own in every language", () => {
    for (const locale of ["zh-TW", "en", "ja"] as const) {
      setLocale(locale);
      const texts = reasons.map(calendarReasonText);
      expect(new Set(texts).size).toBe(reasons.length);
      expect(texts).not.toContain(calendarReasonText("something else"));
    }
  });

  test("a word nobody knows is a plain failure", () => {
    expect(calendarReasonText("whatever")).toBe("無法讀取這個行事曆");
    setLocale("en");
    expect(calendarReasonText("whatever")).toBe("This calendar could not be read");
  });
});

describe("adding a calendar", () => {
  test("sends the label and the address, lists the calendar and forgets the address", async () => {
    const api = fakeApi();
    const tab = tabWith(api, [PERSONAL]);
    tab.label = "  Family ";
    tab.url = ` ${SECRET} `;
    const message = await tab.add();
    expect(api.add).toHaveBeenCalledWith("Family", SECRET);
    expect(message).toBe("已加入「Family」，今天有 3 項行程");
    expect(tab.items.map((c) => c.label)).toEqual(["Personal", "Family"]);
    expect(tab.url).toBe("");
    expect(tab.label).toBe("");
    expect(tab.error).toBeNull();
    expect(tab.busy).toBe(false);
    // what the page keeps of the calendar has no address in it
    expect(JSON.stringify(tab)).not.toContain("private-0123456789abcdef");
  });

  test("a name and an address are both needed, and nothing is sent without them", async () => {
    const api = fakeApi();
    const tab = tabWith(api);
    tab.url = SECRET;
    expect(await tab.add()).toBeNull();
    expect(tab.error!()).toBe("請輸入名稱");
    tab.label = "Family";
    tab.url = "   ";
    expect(await tab.add()).toBeNull();
    expect(tab.error!()).toBe("請貼上 iCal 網址");
    expect(api.add).not.toHaveBeenCalled();
  });

  test("an address the core refuses stays in the form, with the reason", async () => {
    const api = fakeApi({ add: vi.fn(async () => ({ ok: false as const, reason: "not_found" })) });
    const tab = tabWith(api);
    tab.label = "Family";
    tab.url = SECRET;
    expect(await tab.add()).toBeNull();
    expect(tab.error!()).toBe("找不到這個行事曆，網址可能已失效");
    expect(tab.url).toBe(SECRET);
    expect(tab.label).toBe("Family");
    expect(tab.items).toEqual([]);
    expect(tab.busy).toBe(false);
    setLocale("en");
    expect(tab.error!()).toBe("The calendar was not found. The address may no longer be valid");
  });

  test("a core that cannot be reached is told as an error and keeps the form", async () => {
    const api = fakeApi({ add: vi.fn(async () => Promise.reject("Raphael's core is not running")) });
    const tab = tabWith(api);
    tab.label = "Family";
    tab.url = SECRET;
    expect(await tab.add()).toBeNull();
    expect(tab.error!()).not.toBe("");
    expect(tab.url).toBe(SECRET);
    expect(tab.busy).toBe(false);
  });

  test("the form cannot be sent twice at once", async () => {
    let finish!: (value: never) => void;
    const api = fakeApi({ add: vi.fn(() => new Promise((resolve) => (finish = resolve as never))) as never });
    const tab = tabWith(api);
    tab.label = "Family";
    tab.url = SECRET;
    const first = tab.add();
    expect(tab.busy).toBe(true);
    expect(await tab.add()).toBeNull();
    finish({ ok: true, calendar: { id: 9, label: "Family", host: "calendar.google.com" }, events_today: 0 } as never);
    expect(await first).toBe("已加入「Family」，今天有 0 項行程");
    expect(api.add).toHaveBeenCalledTimes(1);
  });

  test("a mistake that is mended is no longer told", async () => {
    const answers = [{ ok: false as const, reason: "network" }, { ok: true as const, calendar: WORK, events_today: 1 }];
    const tab = tabWith(fakeApi({ add: vi.fn(async () => answers.shift()!) }));
    tab.label = "Work";
    tab.url = SECRET;
    await tab.add();
    expect(tab.error).not.toBeNull();
    await tab.add();
    expect(tab.error).toBeNull();
    expect(tab.items).toEqual([WORK]);
  });
});

describe("testing a calendar", () => {
  test("says it can be read, and how many events there are today", async () => {
    const api = fakeApi();
    const tab = tabWith(api, [PERSONAL]);
    expect(await tab.test(1)).toBe("「Personal」讀取成功，今天有 2 項行程");
    expect(api.test).toHaveBeenCalledWith(1);
    expect(tab.testing).toBeNull();
  });

  test("says why it cannot", async () => {
    const tab = tabWith(fakeApi({ test: vi.fn(async () => ({ ok: false as const, reason: "timeout" })) }), [PERSONAL]);
    expect(await tab.test(1)).toBe("「Personal」讀取失敗：等了太久沒有回應");
    setLocale("en");
    expect(await tab.test(1)).toBe("“Personal” could not be read: The server took too long to answer");
  });

  test("a core that does not answer is a failure too", async () => {
    const tab = tabWith(fakeApi({ test: vi.fn(async () => Promise.reject("[-32001] timeout")) }), [PERSONAL]);
    expect(await tab.test(1)).toContain("「Personal」讀取失敗：");
    expect(tab.testing).toBeNull();
  });
});

describe("removing a calendar", () => {
  test("takes it off the list at once and removes it in the core when the time to undo is up", async () => {
    const api = fakeApi();
    const tab = tabWith(api, [PERSONAL, WORK]);
    const changed = vi.fn();
    const action = tab.removal(1, changed);
    expect(tab.listed).toEqual([WORK]);
    expect(action.message).toBe("已移除「Personal」");
    expect(api.remove).not.toHaveBeenCalled();
    await action.commit();
    expect(api.remove).toHaveBeenCalledWith(1);
    expect(tab.items).toEqual([WORK]);
    expect(tab.leaving.size).toBe(0);
  });

  test("undo puts it back and asks the tab to draw itself", () => {
    const api = fakeApi();
    const tab = tabWith(api, [PERSONAL, WORK]);
    const changed = vi.fn();
    const action = tab.removal(2, changed);
    action.restore();
    expect(tab.listed).toEqual([PERSONAL, WORK]);
    expect(changed).toHaveBeenCalledTimes(1);
    expect(api.remove).not.toHaveBeenCalled();
  });

  test("a failure puts it back and says why", async () => {
    const api = fakeApi({ remove: vi.fn(async () => Promise.reject("[-32603] The vault failed")) });
    const tab = tabWith(api, [PERSONAL]);
    const action = tab.removal(1, () => undefined);
    await expect(action.commit()).rejects.toBeDefined();
    action.restore();
    expect(tab.listed).toEqual([PERSONAL]);
    expect(action.failure!("[-32603] The vault failed")).toContain("無法移除「Personal」：");
  });
});

describe("what the tab shows", () => {
  test("a calendar is its label and the host of its address, masked, with a test and a remove button", () => {
    const rows = calendarListRows(tabWith(fakeApi(), [PERSONAL, WORK])).join("");
    expect(rows).toContain("Personal");
    expect(rows).toContain("calendar.google.com/…");
    expect(rows).toContain('data-act="cal-test" data-id="1"');
    expect(rows).toContain('data-act="cal-remove" data-id="2"');
    expect(maskedHost("example.org")).toBe("example.org/…");
    expect(rows).not.toContain("private-");
    expect(rows).not.toContain("https://");
  });

  test("with no calendar: one row that says so", () => {
    const rows = calendarListRows(tabWith(fakeApi()));
    expect(rows).toHaveLength(1);
    expect(rows[0]).toContain("還沒有連接行事曆");
    expect(rows[0]).not.toContain("cal-remove");
  });

  test("a calendar whose address is lost says so instead of a host", () => {
    const rows = calendarListRows(tabWith(fakeApi(), [{ id: 3, label: "Old", host: null }])).join("");
    expect(rows).toContain("網址已遺失");
    expect(rows).toContain('data-act="cal-remove" data-id="3"');
  });

  test("a label is escaped, and a calendar that is leaving is not listed", () => {
    const tab = tabWith(fakeApi(), [{ id: 4, label: "<script>x</script>", host: "a.b" }, WORK]);
    tab.removal(2, () => undefined);
    const rows = calendarListRows(tab).join("");
    expect(rows).not.toContain("<script>");
    expect(rows).toContain("&lt;script&gt;");
    expect(rows).not.toContain("Work");
  });

  test("the buttons wait while a test or an add is under way", () => {
    const tab = tabWith(fakeApi(), [PERSONAL, WORK]);
    tab.testing = 1;
    expect(calendarListRows(tab).join("").match(/cal-test"[^>]*disabled/g)).toHaveLength(2);
    tab.testing = null;
    tab.busy = true;
    expect(calendarFormRows(tab).join("")).toMatch(/data-act="cal-add" disabled/);
  });

  test("the form has a name, an address that is hidden as it is typed, and an error line when there is one", () => {
    const tab = tabWith(fakeApi());
    tab.label = "Fam<ily";
    tab.url = SECRET;
    const html = calendarFormRows(tab).join("");
    expect(html).toContain('id="cal-label"');
    expect(html).toContain('value="Fam&lt;ily"');
    expect(html).toMatch(/id="cal-url" type="password"/);
    expect(html).toContain('data-act="cal-add"');
    expect(html).not.toContain('role="alert"');
    tab.error = () => calendarReasonText("duplicate");
    expect(calendarFormRows(tab).join("")).toContain('role="alert"');
    expect(calendarFormRows(tab).join("")).toContain("已經加入過這個網址");
  });
});
