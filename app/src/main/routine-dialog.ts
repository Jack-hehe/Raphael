// A single full routine editor, shared by the member panel, calendar and automation list.
import * as api from "./api";
import { describeError } from "./format";
import { t } from "./i18n";
import { openDialog, confirmDelete, type Dialog } from "./member-dialogs";
import { dayName, faceHTML, HOUR_STEPS, isTime, ROUTINE_TITLE_MAX, ROUTINE_PROMPT_MAX } from "./members";
import { dateKey, openDatePicker } from "./schedule-picker";
import type { Member, Routine, RoutineSchedule } from "./types";
import { btn, esc, ico, openMenu, sel, toaster } from "./ui";
import "./automations.css";

export interface RoutineDialogOptions {
  members: readonly Member[];
  memberId?: number;
  routine?: Routine;
  date?: string;
  time?: string;
  onSaved?(routine: Routine): void;
  onDeleted?(): void;
  /** Profiles can keep routines local until a new member has been created. */
  save?(fields: Pick<Routine, "member_id" | "title" | "prompt" | "schedule">): Promise<Routine>;
  delete?(): Promise<void>;
}

export function openRoutineDialog(o: RoutineDialogOptions): Dialog {
  const routine = o.routine, plan = routine?.schedule;
  let memberId = routine?.member_id ?? o.memberId ?? 0;
  let kind: RoutineSchedule["kind"] = plan?.kind ?? "once";
  const next = new Date(Date.now() + 3600000);
  let date = plan?.start_date ?? o.date ?? dateKey(next);
  let time = plan?.time ?? o.time ?? `${String(next.getHours()).padStart(2, "0")}:00`;
  let days = new Set(plan?.kind === "weekly" ? plan.days : [new Date(`${date}T00:00`).getDay()]);
  let hours = plan?.kind === "every" ? plan.hours : 3;
  let ending = plan?.max_runs ? "after" : plan?.until ? "until" : "never";
  let until = plan?.until ?? date;
  let count = String(plan?.max_runs ?? 10);
  let title = routine?.title ?? "", prompt = routine?.prompt ?? "";
  let more = !!(plan?.max_runs || plan?.until), saving = false;
  let dialog: Dialog;
  const endChoices = (): { value: string; label: string }[] => [
    { value: "never", label: t("auto.never") }, { value: "after", label: t("auto.after") }, { value: "until", label: t("auto.until") },
  ];
  function read(): void {
    title = dialog.el.querySelector<HTMLInputElement>("#au-title")?.value ?? title;
    prompt = dialog.el.querySelector<HTMLTextAreaElement>("#au-prompt")?.value ?? prompt;
    time = dialog.el.querySelector<HTMLInputElement>("#au-time")?.value ?? time;
    count = dialog.el.querySelector<HTMLInputElement>("#au-count")?.value ?? count;
  }
  function body(): string {
    const kinds: [RoutineSchedule["kind"], string][] = [["once", t("auto.once")], ["daily", t("members.daily")], ["weekdays", t("auto.weekdays")], ["weekly", t("members.weekly")], ["every", t("members.every")]];
    return `<input class="mb-fin au-title" id="au-title" maxlength="${ROUTINE_TITLE_MAX}" placeholder="${esc(t("auto.what"))}" aria-label="${esc(t("auto.what"))}" value="${esc(title)}">` +
      `<section class="au-field"><h3>${esc(t("auto.when"))}</h3><div class="au-start"><span>${esc(t("auto.starts"))}</span>${btn(esc(date), "", ' data-dlg="date" aria-label="' + esc(t("auto.date")) + '"')}<input class="mb-fin au-time" id="au-time" type="text" inputmode="numeric" maxlength="5" value="${esc(time)}" aria-label="${esc(t("auto.time"))}" placeholder="00:00"></div>` +
      `<div class="au-chips">${kinds.map(([value, label]) => btn(esc(label), "sm", ` data-dlg="kind" data-kind="${value}" aria-pressed="${kind === value}"`)).join("")}</div>` +
      (kind === "weekly" ? `<div class="au-chips">${[1,2,3,4,5,6,0].map(n => btn(esc(dayName(n)), "sm", ` data-dlg="day" data-day="${n}" aria-pressed="${days.has(n)}"`)).join("")}</div>` : "") +
      (kind === "every" ? sel(HOUR_STEPS.map(n => ({ value: String(n), label: t("members.everyHours", { p0: n }) })), String(hours), t("members.hours"), ' data-dlg="hours"') : "") + `</section>` +
      `<section class="au-field"><h3>${esc(t("auto.who"))}</h3><div class="au-members">${o.members.map(m => btn(faceHTML(m, 30) + `<span>${esc(m.name)}</span>${memberId === m.id ? ico("check") : ""}`, "", ` data-dlg="member" data-member="${m.id}" aria-pressed="${memberId === m.id}"${routine ? " disabled" : ""}`)).join("")}</div></section>` +
      `<label class="au-field"><span class="au-label">${esc(t("members.instructions"))}</span><textarea id="au-prompt" class="mb-fin mb-ta" rows="4" maxlength="${ROUTINE_PROMPT_MAX}" aria-label="${esc(t("members.instructions"))}">${esc(prompt)}</textarea></label>` +
      btn(ico(more ? "down" : "right") + esc(t(more ? "auto.less" : "auto.more")), "sm", ` data-dlg="more" aria-expanded="${more}"`) +
      (more ? `<section class="au-more"><label class="au-field"><span>${esc(t("auto.end"))}</span>${sel(endChoices(), ending, t("auto.end"), ' data-dlg="end"')}` +
      (ending === "after" ? `<input id="au-count" class="mb-fin" type="number" min="1" max="10000" value="${esc(count)}" aria-label="${esc(t("auto.after"))}">` : ending === "until" ? btn(esc(until), "", ' data-dlg="until"') : "") +
      `</label><div class="au-field"><span>${esc(t("auto.results"))}</span>${sel([{ value: "main", label: t("auto.mainThread") }], "main", t("auto.results"), ' data-dlg="destination"')}</div></section>` : "");
  }
  function draw(): void { dialog.el.querySelector(".mb-dlg-body")!.innerHTML = body(); }
  async function save(): Promise<void> {
    if (saving) return;
    read();
    const start = new Date(`${date}T${time}`);
    if (!title.trim() || !prompt.trim() || !isTime(time) || !Number.isFinite(start.getTime()) || (kind === "weekly" && !days.size) || (ending === "after" && (!/^\d+$/.test(count) || Number(count) < 1 || Number(count) > 10000)) || (ending === "until" && until < date)) {
      toaster.info(t("auto.needs")); return;
    }
    if (!routine && kind === "once" && start.getTime() <= Date.now()) { toaster.info(t("auto.future")); return; }
    const common = { start_date: date, ...(ending === "after" ? { max_runs: Number(count) } : {}), ...(ending === "until" ? { until } : {}) };
    const schedule: RoutineSchedule = kind === "every" ? { kind, hours, time, ...common } : kind === "weekly" ? { kind, days: [...days].sort(), time, ...common } : { kind, time, ...common };
    saving = true;
    dialog.el.querySelector<HTMLButtonElement>('[data-dlg="save"]')!.disabled = true;
    try {
      const fields = { member_id: memberId, title: title.trim(), prompt: prompt.trim(), schedule };
      const result = o.save ? { routine: await o.save(fields) } : routine ? await api.routinesUpdate(routine.id, { title: fields.title, prompt: fields.prompt, schedule }) : await api.routinesCreate(fields);
      dialog.close(); o.onSaved?.(result.routine);
    } catch (error) { toaster.info(describeError(error)); }
    finally { saving = false; const save = dialog.el.querySelector<HTMLButtonElement>('[data-dlg="save"]'); if (save) save.disabled = false; }
  }
  dialog = openDialog({ title: t(routine ? "auto.editRoutine" : "auto.newRoutine"), body: body(), cls: "au-routine-dialog",
    actions: { secondary: btn(esc(t("common.cancel")), "", ' data-dlg="cancel"'), destructive: routine ? { label: esc(t("auto.delete")), attrs: ' data-dlg="delete"' } : undefined, primary: { label: esc(t("members.save")), attrs: ' data-dlg="save"' } },
    canClose: () => !saving, onEnter: () => void save(),
    onAct(act, target) {
      read();
      if (act === "save") void save();
      else if (act === "cancel") dialog.close();
      else if (act === "kind") { kind = target.dataset.kind as typeof kind; draw(); }
      else if (act === "day") { const n = Number(target.dataset.day); if (days.has(n)) days.delete(n); else days.add(n); draw(); }
      else if (act === "destination") openMenu(target, [{ value: "main", label: t("auto.mainThread") }], "main", () => {});
      else if (act === "member") { memberId = Number(target.dataset.member); draw(); }
      else if (act === "more") { more = !more; draw(); }
      else if (act === "date" || act === "until") openDatePicker(target, act === "date" ? date : until, key => { if (act === "date") date = key; else until = key; draw(); dialog.el.querySelector<HTMLElement>(`[data-dlg="${act}"]`)?.focus(); });
      else if (act === "hours") openMenu(target, HOUR_STEPS.map(n => ({ value: String(n), label: t("members.everyHours", { p0: n }) })), String(hours), n => { hours = Number(n); draw(); });
      else if (act === "end") openMenu(target, endChoices(), ending, value => { ending = value; draw(); });
      else if (act === "delete" && routine) void confirmDelete(routine.title).then(async yes => {
        if (!yes) return;
        try { if (o.delete) await o.delete(); else await api.routinesDelete(routine.id); dialog.close(); o.onDeleted?.(); } catch (error) { toaster.info(describeError(error)); }
      });
    },
  });
  return dialog;
}
