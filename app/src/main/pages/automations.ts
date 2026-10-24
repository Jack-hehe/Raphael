// The owner's routines across the entire team, as a list or a local-time day/week calendar.
import * as api from "../api";
import { occurrences } from "../automation-calendar";
import { describeError, formatClock } from "../format";
import { getLocale, onLocaleChange, t } from "../i18n";
import { dayName, faceHTML, scheduleText } from "../members";
import { openRoutineDialog } from "../routine-dialog";
import { dateKey, localDay, shiftDay, monthLabel, monthGrid } from "../schedule-picker";
import { openMemberThread } from "../team-navigation";
import type { AppContext, Member, Page, Routine, RoutineRun } from "../types";
import { btn, esc, ico, iconButton, tg, toaster } from "../ui";
import "../members.css";
import "../automations.css";

const EXAMPLES = [
  { title: "auto.example.news", prompt: "auto.example.newsPrompt", time: "08:00", weekly: false },
  { title: "auto.example.calendar", prompt: "auto.example.calendarPrompt", time: "08:00", weekly: true },
  { title: "auto.example.japanese", prompt: "auto.example.japanesePrompt", time: "21:00", weekly: false },
] as const;

export const runStatus = (status: string): string => t(status === "skipped" ? "auto.skipped" : status === "running" || status === "confirm" ? "auto.running" : status === "done" ? "auto.done" : "auto.error");
const stamp = (at: number): string => new Date(at).toLocaleString(getLocale(), { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
export function createAutomationsPage(app: AppContext): Page {
  const el = document.createElement("section"); el.className = "au-page";
  let members: Member[] = [], routines: Routine[] = [];
  let runCounts = new Map<number, number>();
  let logs = new Map<number, RoutineRun[]>(), view = "list", period = "week", date = new Date(), mini = new Date();
  let visible = false, awake = false, loads = 0, pending = true;
  let timer: ReturnType<typeof setInterval> | undefined;
  const member = (id: number): Member | undefined => members.find(m => m.id === id);
  const edit = (routine?: Routine, memberId?: number, day?: string, time?: string, preset?: Parameters<typeof openRoutineDialog>[0]["preset"]): void => {
    openRoutineDialog({ members, routine, memberId, date: day, time, preset, onSaved: () => void load(), onDeleted: () => void load() });
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
  function listHTML(): string {
    return `<div class="au-list">${routines.length ? routines.map(r => {
      const m = member(r.member_id), last = logs.get(r.id)?.[0];
      const state = last ? runStatus(last.status) : t("auto.noRuns");
      const line = [t("auto.last", { p0: r.last_run ? stamp(r.last_run) + " · " + state : t("auto.noRuns") }), r.next_run ? t("auto.next", { p0: stamp(r.next_run) }) : t("auto.ended")].join(" · ");
      return `<article class="au-card"><div class="au-card-head"><i class="au-status ${r.enabled && r.next_run ? "is-on" : ""}"></i>` +
        btn(`${m ? faceHTML(m, 28) : ""}<span class="au-sentence"><strong>${esc(m?.name ?? t("nav.agent"))}</strong> ${esc(t("auto.sentence", { p0: scheduleText(r.schedule), p1: r.title }))}</span>`, "au-edit", ` data-au="edit" data-id="${r.id}"`) +
        tg(r.enabled, t("members.routineSwitch", { p0: r.title }), ` data-au="toggle" data-id="${r.id}"`) + `</div><p>${esc(line)}</p><div class="au-card-foot"><a href="#member-${r.member_id}" data-au="destination" data-id="${r.id}">${esc(t("auto.destination", { p0: m?.name ?? t("nav.agent") }))}</a></div></article>`;
    }).join("") : `<div class="au-empty">${ico("repeat")}<h2>${esc(t("auto.noRoutines"))}</h2><p>${esc(t("auto.emptyHelp"))}</p><div class="au-examples">${EXAMPLES.map((example, i) => btn(ico("plus") + `<span>${esc(t(example.title))}</span>`, "au-example", ` data-au="example" data-example="${i}"`)).join("")}</div>${btn(ico("plus") + esc(t("auto.new")), "pri", ' data-au="new"')}</div>`}<div class="au-awake"><span>${esc(t("auto.keepAwake"))}<small>${esc(t("auto.awakeHelp"))}</small></span>${tg(awake, t("auto.keepAwake"), ' data-au="awake"')}</div></div>`;
  }
  function calendarHTML(): string {
    const start = period === "day" ? localDay(dateKey(date)) : shiftDay(localDay(dateKey(date)), -((date.getDay() + 6) % 7));
    const n = period === "day" ? 1 : 7, end = shiftDay(start, n), now = new Date();
    const days = Array.from({ length: n }, (_, i) => shiftDay(start, i));
    const future = routines.flatMap(r => occurrences(r, start, end, r.schedule.max_runs ? Math.max(0, r.schedule.max_runs - (runCounts.get(r.id) ?? 0)) : Infinity));
    const history = routines.flatMap(routine => (logs.get(routine.id) ?? []).map(run => ({ routine, run, at: run.scheduled ?? run.started })).filter(o => o.at >= start.getTime() && o.at < end.getTime() && o.at <= now.getTime()));
    const all: { routine: Routine; at: number; run?: RoutineRun }[] = [...history, ...future.filter(o => !history.some(h => h.routine.id === o.routine.id && h.at === o.at))].sort((a,b) => a.at-b.at);
    const today = dateKey(now);
    return `<div class="au-calendar-toolbar">${iconButton("left", t("auto.previous"), ' data-au="prev"')}${iconButton("right", t("auto.following"), ' data-au="next"')}<b>${esc(start.toLocaleDateString(getLocale()))} – ${esc(shiftDay(end, -1).toLocaleDateString(getLocale()))}</b>${btn(esc(t("auto.today")), "sm", ' data-au="today"')}<span class="au-spacer"></span><div class="au-segment">${["day", "week"].map(p => btn(esc(t(p === "day" ? "auto.day" : "auto.week")), "sm", ` data-au="period" data-period="${p}" aria-pressed="${period === p}"`)).join("")}</div></div>` +
    `<div class="au-calendar"><aside class="au-calendar-side"><div class="au-month-head">${iconButton("left", t("auto.monthPrevious"), ' data-au="month-prev"')}<b>${esc(monthLabel(mini))}</b>${iconButton("right", t("auto.monthNext"), ' data-au="month-next"')}</div>${monthGrid(mini, dateKey(date), "data-mini-date")}<h3>${esc(t("members.title"))}</h3><div class="au-drag-members">${members.map(m => btn(faceHTML(m, 28) + `<span>${esc(m.name)}</span>`, "", ` draggable="true" data-drag-member="${m.id}"`)).join("")}</div><p>${esc(t("auto.drag"))}</p></aside>` +
    `<div class="au-calendar-scroll"><div class="au-grid" style="--au-days:${n}"><div class="au-grid-head"><span>${esc(Intl.DateTimeFormat(getLocale(), { timeZoneName: "shortOffset" }).formatToParts(now).find(p => p.type === "timeZoneName")?.value ?? "")}</span>${days.map(d => `<b class="${dateKey(d) === today ? "is-today" : ""}">${esc(dayName(d.getDay()))}<strong>${d.getDate()}</strong></b>`).join("")}</div><div class="au-hours">${Array.from({ length: 24 }, (_, h) => `<div>${String(h).padStart(2, "0")}:00</div>`).join("")}</div>` +
      days.map(d => {
        const key = dateKey(d), events = all.filter(o => dateKey(new Date(o.at)) === key);
        return `<div class="au-day-col" data-day="${key}">${Array.from({ length: 24 }, (_, h) => `<div class="au-slot" data-slot="${key}" data-hour="${h}"></div>`).join("")}` +
          events.map((o, index) => { const at = new Date(o.at), m = member(o.routine.member_id); const overlaps = events.filter(e => Math.abs(e.at - o.at) < 1800000).length; const lane = events.slice(0, index).filter(e => Math.abs(e.at - o.at) < 1800000).length;
            const clock = formatClock(o.at), label = [m?.name, o.routine.title, clock, o.run ? runStatus(o.run.status) : ""].filter(Boolean).join(" · ");
            return btn(`${o.run ? `<i class="au-history-mark" title="${esc(runStatus(o.run.status))}">${o.run.status === "done" ? "✓" : o.run.status === "skipped" ? "–" : "!"}</i>` : ""}${m ? faceHTML(m, 20) : ""}<span class="au-occurrence-copy"><b>${esc(o.routine.title)}</b><time datetime="${at.toISOString()}">${clock}</time></span>`, `au-occurrence${o.run ? " is-past" : ""}`, ` ${o.run ? `data-run="${o.run.id}" data-status="${o.run.status}"` : ""} aria-label="${esc(label)}" title="${esc(label)}" data-au="edit" data-id="${o.routine.id}" style="top:${(at.getHours() + at.getMinutes() / 60) * 64}px;left:calc(${lane * 100 / overlaps}% + 2px);width:calc(${100 / overlaps}% - 4px)"`);
          }).join("") + (key === today ? `<i class="au-now" style="top:${(now.getHours() + now.getMinutes() / 60) * 64}px"></i>` : "") + `</div>`;
      }).join("") + `</div></div></div>`;
  }
  function draw(): void {
    const scroll = el.querySelector(".au-calendar-scroll"), top = scroll?.scrollTop ?? 8 * 64;
    el.innerHTML = `<header class="au-header"><div class="au-heading"><h1>${esc(t("auto.title"))}</h1><p>${esc(t("auto.about"))}</p></div><div class="au-segment">${["list", "calendar"].map(v => btn(esc(t(v === "list" ? "auto.list" : "auto.calendar")), "sm", ` data-au="view" data-view="${v}" aria-pressed="${view === v}"`)).join("")}</div>${btn(ico("plus") + esc(t("auto.new")), "pri", ' data-au="new"')}</header>` +
      (pending ? `<div class="au-empty" role="status">${esc(t("common.loading"))}</div>` : view === "list" ? listHTML() : `<p class="au-calendar-hint">${esc(t("auto.calendarHelp"))}</p>` + calendarHTML());
    const grid = el.querySelector(".au-calendar-scroll"); if (grid) grid.scrollTop = top;
  }
  el.addEventListener("click", e => {
    const target = (e.target as Element).closest<HTMLElement>("[data-au], [data-mini-date]"); if (!target) return;
    if (target.dataset.miniDate) { date = localDay(target.dataset.miniDate); draw(); return; }
    const act = target.dataset.au, r = routines.find(r => r.id === Number(target.dataset.id));
    if (act === "new") edit();
    else if (act === "edit" && r) edit(r);
    else if (act === "destination" && r) { e.preventDefault(); openMemberThread(app, r.member_id); }
    else if (act === "example") {
      const example = EXAMPLES[Number(target.dataset.example)];
      if (example) edit(undefined, 0, undefined, undefined, { title: t(example.title), prompt: t(example.prompt), schedule: example.weekly ? { kind: "weekly", days: [1], time: example.time } : { kind: "daily", time: example.time } });
    }
    else if (act === "toggle" && r) { (target as HTMLButtonElement).disabled = true; void api.routinesUpdate(r.id, { enabled: !r.enabled }).then(() => load()).catch(error => { (target as HTMLButtonElement).disabled = false; toaster.info(describeError(error)); }); }
    else if (act === "awake") { (target as HTMLButtonElement).disabled = true; void api.keepAwakeSet(!awake).then(value => { awake = value; draw(); }).catch(error => { (target as HTMLButtonElement).disabled = false; toaster.info(describeError(error)); }); }
    else if (act === "view") { view = target.dataset.view!; draw(); }
    else if (act === "period") { period = target.dataset.period!; draw(); }
    else if (act === "today") { date = new Date(); mini = date; draw(); }
    else if (act === "prev" || act === "next") { date = shiftDay(date, (act === "prev" ? -1 : 1) * (period === "day" ? 1 : 7)); mini = date; draw(); }
    else if (act === "month-prev" || act === "month-next") { mini = new Date(mini.getFullYear(), mini.getMonth() + (act === "month-prev" ? -1 : 1), 1); draw(); }
  });
  el.addEventListener("dragstart", e => { const m = (e.target as Element).closest<HTMLElement>("[data-drag-member]"); if (m) e.dataTransfer?.setData("application/x-raphael-member", m.dataset.dragMember!); });
  el.addEventListener("dragover", e => { if ((e.target as Element).closest("[data-slot]") && e.dataTransfer?.types.includes("application/x-raphael-member")) e.preventDefault(); });
  el.addEventListener("drop", e => { const slot = (e.target as Element).closest<HTMLElement>("[data-slot]"); if (!slot) return; e.preventDefault(); const value = e.dataTransfer?.getData("application/x-raphael-member"); if (!value) return; const id = Number(value); if (members.some(m => m.id === id)) edit(undefined, id, slot.dataset.slot, `${slot.dataset.hour!.padStart(2, "0")}:00`); });
  app.onEvent(event => { if (visible && ["RoutinesChanged", "MembersChanged", "CoreReady"].includes(event.type)) void load(); });
  onLocaleChange(() => { if (visible) { draw(); void load(); } });
  return { id: "automations", el, show() { visible = true; draw(); void load(); void api.keepAwakeGet().then(v => { awake = v; if (visible) draw(); }).catch(() => {}); timer = setInterval(() => { if (visible && view === "calendar") draw(); }, 60000); }, hide() { visible = false; loads++; clearInterval(timer); } };
}
