import { scene, click, hover, type, wait, page, key, scroll, room, newRoom, check } from "./steps.mjs";

const auto = [page("home"), click('.ho-today [data-home-go="calendar"]'), wait(".au-toolbar")];
const calendar = [...auto, wait(".au-occurrence"), check("calendar-chips"), check("calendar-shell")];
const dialog = [...auto, click('[data-au="new"]'), wait(".mb-dialog")];
const more = [...dialog, click('[data-dlg="more"]')];
const team = [page("team"), wait(".team-member"), check("team-header")];
const tools = [...calendar, { action: "calendar-tools" }, check("calendar-tools")];
const groupMenu = [...team, click('[data-team="group-menu"]')];
export default [
  scene("automations-hover", "Calendar chip hover", [...calendar, hover('.au-occurrence')]),
  scene("automations-empty", "Calendar without routines", [...auto, check("calendar-shell")], { routines: [] }),
  scene("automations-calendar", "Automation week calendar", calendar),
  scene("automations-day", "Automation day calendar", [...calendar, click('[data-period="day"]'), check("calendar-shell")]),
  scene("automations-tools", "Responsive calendar month, members and keep-awake", tools),
  scene("automations-tools-month", "Calendar tools navigate mini months", [...tools, click('[data-au="month-next"]'), check("calendar-tools")]),
  scene("automations-tools-date", "Calendar tools select a date and return to the grid", [...tools, click('[data-mini-date="2026-10-28"]'), wait('.au-day-col[data-day="2026-10-28"]'), check("calendar-shell")]),
  scene("automations-tools-awake", "Calendar tools preserve the keep-awake switch", [...tools, click('[data-au="awake"]'), wait('[data-au="awake"][aria-checked="true"]'), check("calendar-tools-awake")]),
  ...["once", "daily", "weekdays", "weekly", "every"].map(kind => scene(`automation-${kind}`, `${kind} automation dialog`, [...dialog, click(`[data-kind="${kind}"]`)])),
  scene("automation-hours-menu", "Every-hours menu", [...dialog, click('[data-kind="every"]'), click('[data-dlg="hours"]')]),
  scene("automation-destination", "Automation destination menu", [...more, click('[data-dlg="destination"]')]),
  scene("automation-end-menu", "Automation end menu", [...more, click('[data-dlg="end"]')]),
  scene("automation-max-runs", "Automation maximum run count", [...more, click('[data-dlg="end"]'), click('[data-value="after"]')]),
  scene("automation-until", "Automation end date picker", [...more, click('[data-dlg="end"]'), click('[data-value="until"]'), click('[data-dlg="until"]')]),
  scene("automation-edit", "Edit an automation", [...auto, click('[data-au="edit"]')]),
  scene("automation-delete", "Delete an automation confirmation", [...auto, click('[data-au="edit"]'), click('[data-dlg="delete"]')]),
  scene("automation-drag-prefill", "Calendar drop opens a prefilled automation", [...tools, { action: "drag-start", selector: '[data-drag-member="2"]' }, { action: "drop", selector: '[data-slot][data-hour="9"]', memberId: 2 }]),
  scene("automation-more-bottom", "Scrollable automation advanced fields", [...more, click('[data-dlg="end"]'), click('[data-value="after"]'), scroll(".au-more")]),
  scene("team", "Grouped team board", team),
  scene("team-group-menu", "Team group actions", groupMenu),
  ...["rename", "delete"].map(action => scene(`team-${action}`, `Team group ${action}`, [...groupMenu, click(`[data-pick="${action}"]`)])),
  scene("team-new-menu", "Team creation menu", [...team, click('[data-team="new"]')]),
  scene("team-new-group", "New team group", [...team, click('[data-team="new"]'), click('[data-pick="group"]')]),
  scene("team-mentions", "Mentions follow the character at line start, after a newline and inline", [...team, type(".team-composer textarea", "@"), check("team-mentions"), type(".team-composer textarea", "請整理進度。\n@"), check("team-mentions"), type(".team-composer textarea", "請 @"), check("team-mentions")]),
  scene("team-empty", "Team board with Raphael only", team, { groups: [], onlyRaphael: true }),
  scene("team-drop-target", "Team member drag destination", [...team, { action: "drag-start", selector: ".team-member[draggable]" }]),
  scene("team-zoom", "Zoomed team board", [...team, click('[data-team="zoom-in"]')]),
  scene("sidebar", "Conversation sidebar and rooms", []),
  scene("compact-rooms", "Rooms in the compact picker", [{ action: "rooms-menu" }]),
  scene("new-room", "New room dialog", [newRoom()]),
  scene("room-group-menu", "New room group menu", [newRoom(), click('[data-dlg="group"]')]),
  scene("room-draft", "Room composer with a mention", [room(1), type("#room-message", "@小晴 請整理今天的上線進度。")]),
  scene("room-menu", "Room actions", [room(1), click("[data-room-menu]")]),
  ...["rename", "members", "delete"].map(action => scene(`room-${action}`, `Room ${action} dialog`, [room(1), click("[data-room-menu]"), click(`[data-pick="${action}"]`)])),
  scene("room-hover", "Room answer toolbar", [room(1), hover(".au-room-thread .ra .chat-message")]),
];
