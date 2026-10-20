// The Calendar tab of the settings: the calendars the owner connected (iCal subscription addresses,
// read-only) and the form that adds one. The address of a calendar is a secret. It is typed in the
// form, sent to the core once and kept in the Windows Credential Manager; the page is only ever
// given the label and the host of what it sent, and forgets the address as soon as the core has it.

import { t } from "./i18n";

import { describeError } from "./format";
import type { CalendarAdded, CalendarChecked, CalendarInfo } from "./types";
import type { UndoableAction } from "./undo";
import { btn, esc, fieldHTML, note, row, st } from "./ui";

/** Why a calendar could not be added or read: the word the core answers with, in words. */
export function calendarReasonText(reason: string): string {
  switch (reason) {
    case "url":
      return t("settings.calendarReasonUrl");
    case "not_found":
      return t("settings.calendarReasonNotFound");
    case "denied":
      return t("settings.calendarReasonDenied");
    case "http":
      return t("settings.calendarReasonHttp");
    case "timeout":
      return t("settings.calendarReasonTimeout");
    case "network":
      return t("settings.calendarReasonNetwork");
    case "too_large":
      return t("settings.calendarReasonTooLarge");
    case "not_calendar":
      return t("settings.calendarReasonNotCalendar");
    case "duplicate":
      return t("settings.calendarReasonDuplicate");
    case "limit":
      return t("settings.calendarReasonLimit");
    case "missing":
      return t("settings.calendarReasonMissing");
    default:
      return t("settings.calendarReasonOther");
  }
}

/** What the page shows of an address: its host, and that there is more that it keeps to itself. */
export const maskedHost = (host: string): string => `${host}/…`;

/** The calls of the core that the tab makes. */
export interface CalendarApi {
  add(label: string, url: string): Promise<CalendarAdded>;
  test(id: number): Promise<CalendarChecked>;
  remove(id: number): Promise<{ removed: boolean }>;
}

/** What the tab remembers: the calendars, what is typed in the form, what is under way and what went wrong. */
export class CalendarTab {
  items: CalendarInfo[] = [];
  /** The form. The address is cleared as soon as the core has taken it. */
  label = "";
  url = "";
  /** The form is being sent. */
  busy = false;
  /** The calendar being tested. */
  testing: number | null = null;
  /** What is wrong with the form, as a line under it. */
  error: (() => string) | null = null;
  /** Calendars whose removal waits for the end of the time to undo it: not listed meanwhile. */
  readonly leaving = new Set<number>();

  constructor(private readonly api: CalendarApi) {}

  /** The panel opened: the calendars the core has, and an empty form. */
  reset(items: CalendarInfo[]): void {
    this.items = items;
    this.label = "";
    this.url = "";
    this.error = null;
    this.busy = false;
    this.testing = null;
  }

  /** The calendars to list. */
  get listed(): CalendarInfo[] {
    return this.items.filter((c) => !this.leaving.has(c.id));
  }

  private labelOf(id: number): string {
    return this.items.find((c) => c.id === id)?.label ?? "";
  }

  /**
   * Sends the form. The core fetches the address and reads it before it keeps it, so a wrong
   * address is told here and stays in the form to be mended. Resolves with what to say when the
   * calendar was added, null otherwise (`error` says what is wrong).
   */
  async add(): Promise<string | null> {
    if (this.busy) return null;
    const label = this.label.trim();
    const url = this.url.trim();
    if (!label) {
      this.error = () => t("settings.calendarNeedName");
      return null;
    }
    if (!url) {
      this.error = () => t("settings.calendarNeedUrl");
      return null;
    }
    this.busy = true;
    this.error = null;
    try {
      const answer = await this.api.add(label, url);
      if (!answer.ok) {
        const reason = answer.reason;
        this.error = () => calendarReasonText(reason);
        return null;
      }
      this.items = [...this.items, answer.calendar];
      this.label = "";
      this.url = "";
      return t("settings.calendarAdded", { p0: answer.calendar.label, p1: answer.events_today });
    } catch (err) {
      this.error = () => describeError(err);
      return null;
    } finally {
      this.busy = false;
    }
  }

  /** Fetches a calendar again; resolves with what to say about it. */
  async test(id: number): Promise<string> {
    const label = this.labelOf(id);
    this.testing = id;
    try {
      const answer = await this.api.test(id);
      return answer.ok
        ? t("settings.calendarTestOk", { p0: label, p1: answer.events_today })
        : t("settings.calendarTestFail", { p0: label, p1: calendarReasonText(answer.reason) });
    } catch (err) {
      return t("settings.calendarTestFail", { p0: label, p1: () => describeError(err) });
    } finally {
      this.testing = null;
    }
  }

  /**
   * Takes a calendar off the list, to be removed in the core when the time to undo it is up. The
   * address goes with it (the core clears it from the vault). `changed` redraws the tab.
   */
  removal(id: number, changed: () => void): UndoableAction {
    const label = this.labelOf(id);
    this.leaving.add(id);
    return {
      message: t("settings.calendarRemoved", { p0: label }),
      commit: async () => {
        await this.api.remove(id);
        this.items = this.items.filter((c) => c.id !== id);
        this.leaving.delete(id);
      },
      restore: () => {
        this.leaving.delete(id);
        changed();
      },
      failure: (err) => t("settings.calendarRemoveFailed", { p0: label, p1: () => describeError(err) }),
    };
  }
}

/** The rows of the list: each calendar with its label, the host of its address and the buttons, or one row saying there are none. */
export function calendarListRows(tab: CalendarTab): string[] {
  const items = tab.listed;
  if (items.length === 0) return [row(esc(t("settings.calendarNone")), esc(t("settings.calendarNoneDesc")))];
  return items.map((c) =>
    row(
      esc(c.label),
      esc(c.host === null ? t("settings.calendarLost") : maskedHost(c.host)),
      btn(
        esc(tab.testing === c.id ? t("onboarding.011") : t("settings.calendarTest")),
        "sm",
        ` data-act="cal-test" data-id="${c.id}"${tab.testing !== null ? " disabled" : ""}`,
      ) + btn(esc(t("settings.calendarRemove")), "sm", ` data-act="cal-remove" data-id="${c.id}"`),
      { attrs: ` data-row="calendar-${c.id}"` },
    ),
  );
}

/** The form that adds one: a name, the address (a password field, for it is a secret) and 「加入」. */
export function calendarFormRows(tab: CalendarTab): string[] {
  return [
    row(
      esc(t("settings.calendarName")),
      "",
      fieldHTML(t("settings.calendarNameHint"), tab.label, { w: 200, label: t("settings.calendarName"), id: "cal-label" }),
    ),
    row(
      esc(t("settings.calendarUrl")),
      esc(t("settings.calendarUrlDesc")),
      fieldHTML(t("settings.calendarUrlHint"), tab.url, { w: 240, label: t("settings.calendarUrl"), id: "cal-url", type: "password" }) +
        btn(esc(tab.busy ? t("onboarding.011") : t("settings.calendarAdd")), "pri sm", ` data-act="cal-add"${tab.busy ? " disabled" : ""}`),
    ),
    ...(tab.error ? [note(st("err", tab.error()), ' role="alert"')] : []),
  ];
}
