// The calendar of the owner's routines across the whole team: a local-time day or week grid, with the
// month, the members to drag onto a time and the keep-awake switch on the right. Home opens it.
import * as api from "../api";
import { occurrences } from "../automation-calendar";
import { describeError, formatClock } from "../format";
import { getLocale, onLocaleChange, t } from "../i18n";
import { dayName, faceHTML } from "../members";
import { openRoutineDialog } from "../routine-dialog";
import { dateKey, localDay, shiftDay, monthLabel, monthGrid } from "../schedule-picker";
import type { AppContext, Member, Page, Routine, RoutineRun } from "../types";
import { btn, closeMenu, esc, ico, iconButton, openPopover, tg, toaster } from "../ui";
import "../members.css";
import "../automations.css";

export const runStatus = (status: string): string => t(status === "skipped" ? "auto.skipped" : status === "running" || status === "confirm" ? "auto.running" : status === "done" ? "auto.done" : "auto.error");
const HOUR_PX = 64;

export function createAutomationsPage(app: AppContext): Page {
  const el = document.createElement("section"); el.className = "au-page";
  let members: Member[] = [], routines: Routine[] = [];
  let runCounts = new Map<number, number>();
  let logs = new Map<number, RoutineRun[]>(), period = "week", date = new Date(), mini = new Date();
  let visible = false, awake = false, loads = 0, pending = true;
  let timer: ReturnType<typeof setInterval> | undefined;
  let tools: HTMLElement | null = null;
  const member = (id: number): Member | undefined => members.find(m => m.id === id);
  const edit = (routine?: Routine, memberId?: number, day?: string, time?: string): void => {
    openRoutineDialog({ members, routine, memberId, date: day, time, onSaved: () => void load(), onDeleted: () => void load() });
  };
  async function load(): Promise<void> {
    const mine = ++loads;
    try {
      const [people, plans] = await Promise.all([api.membersList(getLocale()), api.routinesList()]);
      const histories = await Promise.all(plans.routines.map(async r => [r.id, await api.routineRuns(r.id)] as const));
      if (mine !== loads || !visible) return;
      members = people.members; routines = plans.routines; logs = new Map(histories.map(([id, h]) => [id, h.runs])); runCounts = new Map(histories.map(([id, h]) => [id, h.total ?? h.runs.length])); pending = false; draw();
    } catch (error) { if (mine === loads && visible) { pending = false; draw(); toaster.info(describeError(error)); } }
  }
  function rangeLabel(start: Date, end: Date): string {
    const last = shiftDay(end, -1), day: Intl.DateTimeFormatOptions = { month: "long", day: "numeric" };
    return period === "day" ? start.toLocaleDateString(getLocale(), { ...day, weekday: "short" })
      : `${start.toLocaleDateString(getLocale(), day)} – ${last.toLocaleDateString(getLocale(), last.getMonth() === start.getMonth() ? { day: "numeric" } : day)}`;
  }
  function toolbarHTML(start: Date, end: Date): string {
    return `<header class="au-toolbar">${btn(esc(t("auto.today")), "sm", ' data-au="today"')}` +
      `<div class="au-step">${iconButton("left", t("auto.previous"), ' data-au="prev"')}${iconButton("right", t("auto.following"), ' data-au="next"')}</div>` +
      `<h1>${esc(rangeLabel(start, end))}</h1><span class="au-spacer"></span>` +
      `<div class="au-segment" role="group">${["day", "week"].map(p => btn(esc(t(p === "day" ? "auto.day" : "auto.week")), "sm", ` data-au="period" data-period="${p}" aria-pressed="${period === p}"`)).join("")}</div>` +
      iconButton("calendar", t("auto.tools"), ' data-au="tools" aria-haspopup="dialog"', "au-tools-toggle") +
      btn(ico("plus") + esc(t("auto.new")), "pri sm", ' data-au="new"') + `</header>`;
  }
  function sideHTML(): string {
    return `<aside class="au-calendar-side"><div class="au-month-head">${iconButton("left", t("auto.monthPrevious"), ' data-au="month-prev"')}<b>${esc(monthLabel(mini))}</b>${iconButton("right", t("auto.monthNext"), ' data-au="month-next"')}</div>${monthGrid(mini, dateKey(date), "data-mini-date")}` +
      `<h3>${esc(t("members.title"))}</h3><div class="au-drag-members">${members.map(m => btn(faceHTML(m, 24) + `<span>${esc(m.name)}</span>`, "", ` draggable="true" data-drag-member="${m.id}" title="${esc(t("auto.drag"))}"`)).join("")}</div>` +
      `<label class="au-awake" title="${esc(t("auto.keepAwake"))}"><span>${esc(t("auto.awake"))}</span>${tg(awake, t("auto.keepAwake"), ' data-au="awake"')}</label></aside>`;
  }
  function gridHTML(start: Date, n: number, end: Date): string {
    const now = new Date();
    const days = Array.from({ length: n }, (_, i) => shiftDay(start, i));
    const future = routines.flatMap(r => occurrences(r, start, end, r.schedule.max_runs ? Math.max(0, r.schedule.max_runs - (runCounts.get(r.id) ?? 0)) : Infinity));
    const history = routines.flatMap(routine => (logs.get(routine.id) ?? []).map(run => ({ routine, run, at: run.scheduled ?? run.started })).filter(o => o.at >= start.getTime() && o.at < end.getTime() && o.at <= now.getTime()));
    const all: { routine: Routine; at: number; run?: RoutineRun }[] = [...history, ...future.filter(o => !history.some(h => h.routine.id === o.routine.id && h.at === o.at))].sort((a, b) => a.at - b.at);
    const today = dateKey(now);
    const zone = Intl.DateTimeFormat(getLocale(), { timeZoneName: "shortOffset" }).formatToParts(now).find(p => p.type === "timeZoneName")?.value ?? "";
    return `<div class="au-calendar-scroll"><div class="au-grid" style="--au-days:${n}"><div class="au-grid-head"><span>${esc(zone)}</span>${days.map(d => `<b class="${dateKey(d) === today ? "is-today" : ""}">${esc(dayName(d.getDay()))}<strong>${d.getDate()}</strong></b>`).join("")}</div><div class="au-hours">${Array.from({ length: 24 }, (_, h) => `<div>${String(h).padStart(2, "0")}:00</div>`).join("")}</div>` +
      days.map(d => {
        const key = dateKey(d), events = all.filter(o => dateKey(new Date(o.at)) === key);
        return `<div class="au-day-col" data-day="${key}">${Array.from({ length: 24 }, (_, h) => `<div class="au-slot" data-slot="${key}" data-hour="${h}"></div>`).join("")}` +
          events.map((o, index) => {
            const at = new Date(o.at), m = member(o.routine.member_id);
            const overlaps = events.filter(e => Math.abs(e.at - o.at) < 1800000).length, lane = events.slice(0, index).filter(e => Math.abs(e.at - o.at) < 1800000).length;
            const clock = formatClock(o.at), label = [m?.name, o.routine.title, clock, o.run ? runStatus(o.run.status) : ""].filter(Boolean).join(" · ");
            return btn(`<span class="au-occurrence-copy"><time datetime="${at.toISOString()}">${clock}</time><b>${esc(o.routine.title)}</b></span>${m ? faceHTML(m, 20) : ""}${o.run ? `<i class="au-history-mark" data-status="${o.run.status}"></i>` : ""}`,
              `au-occurrence${o.run ? " is-past" : ""}`, ` ${o.run ? `data-run="${o.run.id}" data-status="${o.run.status}"` : ""} aria-label="${esc(label)}" title="${esc(label)}" data-au="edit" data-id="${o.routine.id}" style="top:${(at.getHours() + at.getMinutes() / 60) * HOUR_PX}px;left:calc(${lane * 100 / overlaps}% + 2px);width:calc(${100 / overlaps}% - 4px)"`);
          }).join("") + (key === today ? `<i class="au-now" style="top:${(now.getHours() + now.getMinutes() / 60) * HOUR_PX}px"></i>` : "") + `</div>`;
      }).join("") + `</div></div>`;
  }
  function draw(): void {
    const reopenTools = tools?.isConnected;
    if (reopenTools) closeMenu();
    const scroll = el.querySelector(".au-calendar-scroll"), top = scroll?.scrollTop ?? 8 * HOUR_PX;
    const start = period === "day" ? localDay(dateKey(date)) : shiftDay(localDay(dateKey(date)), -((date.getDay() + 6) % 7));
    const n = period === "day" ? 1 : 7, end = shiftDay(start, n);
    el.innerHTML = toolbarHTML(start, end) + (pending ? `<div class="au-empty" role="status">${esc(t("common.loading"))}</div>`
      : `<div class="au-calendar">${gridHTML(start, n, end)}${sideHTML()}</div>`);
    const grid = el.querySelector(".au-calendar-scroll"); if (grid) grid.scrollTop = top;
    if (reopenTools) openTools();
  }
  function openTools(): void {
    const anchor = el.querySelector<HTMLElement>('[data-au="tools"]'); if (!anchor) return;
    tools = openPopover(anchor, sideHTML(), { className: "au-calendar-tools", role: "dialog", label: t("auto.tools"), onClose: () => { tools = null; } });
    tools.addEventListener("click", onClick);
    tools.addEventListener("dragstart", onDragStart);
  }
  function onClick(e: Event): void {
    const target = (e.target as Element).closest<HTMLElement>("[data-au], [data-mini-date]"); if (!target) return;
    if (target.dataset.miniDate) { if (tools?.isConnected) closeMenu(); date = localDay(target.dataset.miniDate); draw(); return; }
    const act = target.dataset.au, r = routines.find(r => r.id === Number(target.dataset.id));
    if (act === "tools") { if (tools?.isConnected) closeMenu(); else openTools(); }
    else if (act === "new") edit();
    else if (act === "edit" && r) edit(r);
    else if (act === "awake") { e.preventDefault(); (target as HTMLButtonElement).disabled = true; void api.keepAwakeSet(!awake).then(value => { awake = value; draw(); }).catch(error => { (target as HTMLButtonElement).disabled = false; toaster.info(describeError(error)); }); }
    else if (act === "period") { period = target.dataset.period!; draw(); }
    else if (act === "today") { date = new Date(); mini = date; draw(); }
    else if (act === "prev" || act === "next") { date = shiftDay(date, (act === "prev" ? -1 : 1) * (period === "day" ? 1 : 7)); mini = date; draw(); }
    else if (act === "month-prev" || act === "month-next") { mini = new Date(mini.getFullYear(), mini.getMonth() + (act === "month-prev" ? -1 : 1), 1); draw(); }
  }
  function onDragStart(e: DragEvent): void {
    const m = (e.target as Element).closest<HTMLElement>("[data-drag-member]");
    if (m) { e.dataTransfer?.setData("application/x-raphael-member", m.dataset.dragMember!); if (tools?.isConnected) closeMenu(); }
  }
  el.addEventListener("click", onClick);
  el.addEventListener("dragstart", onDragStart);
  el.addEventListener("dragover", e => { if ((e.target as Element).closest("[data-slot]") && e.dataTransfer?.types.includes("application/x-raphael-member")) e.preventDefault(); });
  el.addEventListener("drop", e => { const slot = (e.target as Element).closest<HTMLElement>("[data-slot]"); if (!slot) return; e.preventDefault(); const value = e.dataTransfer?.getData("application/x-raphael-member"); if (!value) return; const id = Number(value); if (members.some(m => m.id === id)) edit(undefined, id, slot.dataset.slot, `${slot.dataset.hour!.padStart(2, "0")}:00`); });
  app.onEvent(event => { if (visible && ["RoutinesChanged", "MembersChanged", "CoreReady"].includes(event.type)) void load(); });
  onLocaleChange(() => { if (visible) { draw(); void load(); } });
  return {
    id: "automations", el,
    show() {
      visible = true;
      draw(); void load();
      void api.keepAwakeGet().then(v => { awake = v; if (visible) draw(); }).catch(() => {});
      clearInterval(timer); timer = setInterval(() => { if (visible) draw(); }, 60000);
    },
    hide() { visible = false; loads++; clearInterval(timer); if (tools?.isConnected) closeMenu(); },
  };
}
