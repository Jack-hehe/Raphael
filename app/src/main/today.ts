// The Today card of the home page as HTML: today's events from the calendars the owner connected
// (`calendar.today`), each with its time (or 「全天」) and title, the one going on now or else the
// next one marked, and, instead of the list, one line when there is nothing today or no calendar
// at all. The core reads the calendars; this file only writes what it says.

import { t } from "./i18n";

import { dayBounds, formatClock } from "./format";
import { loadingHTML } from "./loading";
import { longDate } from "./stats";
import type { CalendarEvent, CalendarToday } from "./types";
import { btn, esc } from "./ui";

/** What the card has to show: nothing yet, or the core's answer. */
export type TodayState = { kind: "loading" } | { kind: "ready"; today: CalendarToday };

/** The most events the card lists; a day with more says how many it leaves out. */
export const MAX_SHOWN = 8;

/** When a timed event is over. A moment (no end) lasts one more millisecond, so that it is the next event until its time comes. */
const endOf = (event: CalendarEvent): number => Math.max(event.end_ms, event.start_ms + 1);

/** Whether the event has finished. All-day events are never over before the day is. */
export const isOver = (event: CalendarEvent, nowMs: number): boolean => !event.all_day && endOf(event) <= nowMs;

/**
 * The event to point at: the first timed one that is not over, which is the one going on now or,
 * if none is, the next to start. -1 when nothing is left today. (`events` come sorted by start.)
 */
export function currentEvent(events: CalendarEvent[], nowMs: number): number {
  return events.findIndex((event) => !event.all_day && !isOver(event, nowMs));
}

/** The time of an event on the day `date`: 「全天」, a range, a start, or, for one that crosses midnight, the end or the start only. */
export function timeText(event: CalendarEvent, date: string): string {
  if (event.all_day) return t("home.todayAllDay");
  const [from, to] = dayBounds(date);
  const before = event.start_ms < from;
  const after = event.end_ms > to;
  if (before && after) return t("home.todayAllDay");
  if (before) return t("home.todayUntil", { p0: formatClock(event.end_ms) });
  if (after) return t("home.todayFrom", { p0: formatClock(event.start_ms) });
  if (event.end_ms > event.start_ms) return `${formatClock(event.start_ms)}–${formatClock(event.end_ms)}`;
  return formatClock(event.start_ms);
}

/**
 * The events to list and how many are left out. A long day drops what is over first, so that what
 * is still to come is what stays on the card.
 */
export function pick(events: CalendarEvent[], nowMs: number): { shown: CalendarEvent[]; more: number } {
  if (events.length <= MAX_SHOWN) return { shown: events, more: 0 };
  const ahead = events.filter((event) => !isOver(event, nowMs));
  if (ahead.length === 0) return { shown: events.slice(-MAX_SHOWN), more: 0 };
  return { shown: ahead.slice(0, MAX_SHOWN), more: Math.max(0, ahead.length - MAX_SHOWN) };
}

function eventsHTML(today: CalendarToday, nowMs: number): string {
  const current = currentEvent(today.events, nowMs);
  const { shown, more } = pick(today.events, nowMs);
  const items = shown.map((event) => {
    const now = current >= 0 && event === today.events[current];
    return (
      `<li class="today-ev${isOver(event, nowMs) ? " is-over" : ""}${now ? " is-current" : ""}"${now ? ' aria-current="time"' : ""}>` +
      `<span class="today-time num">${esc(timeText(event, today.date))}</span>` +
      `<span class="today-title">${esc(event.title || t("home.todayNoTitle"))}</span></li>`
    );
  });
  if (more > 0) items.push(`<li class="today-more">${esc(t("home.todayMore", { p0: more }))}</li>`);
  return `<ul class="today-list">${items.join("")}</ul>`;
}

/** The inside of the card. `nowMs` decides which event is marked and which are over. */
export function todayHTML(state: TodayState, nowMs: number): string {
  const head = (note = ""): string =>
    `<div class="card-head"><h2 id="today-h">${esc(t("home.today"))}</h2>${note ? `<span class="card-note">${esc(note)}</span>` : ""}</div>`;
  if (state.kind === "loading") return head() + loadingHTML();
  const { today } = state;
  if (today.calendars === 0) {
    return (
      head(longDate(today.date)) +
      `<p class="today-line">${esc(t("home.todayNone"))}</p>` +
      `<div class="today-act">${btn(esc(t("home.todayConnect")), "sm", ' data-go="settings" data-pane="calendar"')}</div>`
    );
  }
  // Calendars that could not be read are told, and an empty day is not claimed when none could be.
  const unread = today.failed.length > 0;
  const nothing = today.events.length === 0 && !(unread && today.failed.length >= today.calendars);
  return (
    head(longDate(today.date)) +
    (today.events.length > 0 ? eventsHTML(today, nowMs) : nothing ? `<p class="today-line">${esc(t("home.todayEmpty"))}</p>` : "") +
    (unread
      ? `<div class="today-warn"><span>${esc(t("home.todayFailed", { p0: today.failed.map((c) => c.label).join(t("home.listSep")) }))}</span>` +
        `${btn(esc(t("home.todayManage")), "quiet sm", ' data-go="settings" data-pane="calendar"')}</div>`
      : "")
  );
}
