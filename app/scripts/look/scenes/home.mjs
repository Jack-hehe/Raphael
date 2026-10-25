import { scene, click, wait, check, scroll } from "./steps.mjs";

const ready = [wait(".page-home:not([hidden]) .ho-hero"), check("home-overview")];
export default [
  scene("home", "Home briefing with reports, pending items, calendar and usage", ready),
  scene("home-empty", "Home without pending work, reports, events or usage", ready, { homeEmpty: true }),
  scene("home-720", "Home responsive layout, scrolled to the charts", [...ready, scroll(".ho-dictation"), check("home-overview")]),
  scene("home-report", "A member report opens its conversation at the reported turn", [...ready, click('.ho-row[data-home-item="report:2"]'), wait('.page-agent:not([hidden]) .tn[data-id="2"]'), check("home-report")]),
  scene("home-routine", "A scheduled routine opens the calendar", [...ready, click('.ho-slot[data-home-item="routine:1"]'), wait('.au-page:not([hidden]) .au-occurrence'), check("calendar-shell")]),
  scene("home-calendar", "Today's icon opens the full calendar", [...ready, click('.ho-today [data-home-go="calendar"]'), wait('.au-page:not([hidden]) .au-occurrence'), check("calendar-shell")]),
  scene("home-many", "Many pending questions stay within their card", ready, { homeManyQuestions: true }),
];
