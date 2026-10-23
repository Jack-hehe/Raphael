// The details panel of the 拉斐爾 page (the mockup's `members`, right): the member whose thread is
// open. At the top its picture at 64 px, which opens the small popover that changes it (the presets,
// if the owner has any, and 「從電腦選…」), then its name and role, changed where they are. Then three
// tabs:
//
// - 詳細資訊: 指示 (a box, saved when it loses the focus), 分組 (a dropdown), 模型 (the model picker,
//   「與拉斐爾相同」 first), 例行任務 (a row each with its switch, a borderless pencil and trash; a
//   borderless plus adds one in a small form: title, a dropdown of 每天 / 每週 / 每隔幾小時 with its
//   time, day or interval, and what to do), and 看的地方, the group's places as chips (they are
//   changed in the group's menu).
// - 記憶: 「共同記憶」, which every agent shares, and 「<name> 的記憶」, the agent's own, with the list of
//   Settings › 拉斐爾 › 記憶 (memory-view.ts); a note's 「more」 moves it from one to the other.
//
// Raphael's panel has his picture (changed here and nowhere else: from the PC, cut in the crop
// dialog of avatar-settings.ts, or 還原 to his emblem), his routines and his memory.
// His name and role are not changed, and he has no group, model or instructions here.
//
// The panel draws itself again from the host's data after each change; what is being typed is kept
// in the fields (the name, the role, the instructions and the routine form are read back before).

import * as api from "./api";
import { changeRaphaelAvatar, resetRaphaelAvatar } from "./avatar-settings";
import { currentAvatar } from "./avatar";
import { describeError } from "./format";
import { t } from "./i18n";
import { groupChoices, groupFromValue, groupValue, openAvatarPicker, pickPicture, readPicture } from "./member-dialogs";
import { createMemory } from "./memory-view";
import { memoryHTML } from "./memory-view";
import {
  HOUR_STEPS,
  INSTRUCTIONS_MAX,
  NAME_MAX,
  PERSONALITY_MAX,
  RAPHAEL_ID,
  ROLE_MAX,
  ROUTINE_PROMPT_MAX,
  ROUTINE_TITLE_MAX,
  cleanName,
  dayName,
  faceHTML,
  isMemberNameTaken,
  isTime,
  scheduleText,
  sourceLabel,
} from "./members";
import { cliBrainLine } from "./cli-brains";
import { type ModelPicker, mountModelPicker } from "./model-picker";
import type { AppContext, AvatarPreset, CliBrain, Member, MemberPatch, MembersList, ModelCatalog, Routine, RoutineSchedule, WireAttachment } from "./types";
import { actionRow, btn, esc, iconButton, openMenu, sel, tabs, tg, toaster } from "./ui";

export type DetailsTab = "details" | "memory";

/** The routine being written: a new one (`id` null) or one being changed. */
export interface RoutineDraft {
  id: number | null;
  title: string;
  prompt: string;
  kind: RoutineSchedule["kind"];
  time: string;
  days: number[];
  hours: number;
}

/** A form for a new routine: every morning at nine. */
export const newRoutineDraft = (): RoutineDraft => ({ id: null, title: "", prompt: "", kind: "daily", time: "09:00", days: [1], hours: 3 });

/** The form for changing `routine`. */
export function routineDraft(routine: Routine): RoutineDraft {
  const s = routine.schedule;
  return {
    id: routine.id,
    title: routine.title,
    prompt: routine.prompt,
    kind: s.kind,
    time: s.kind === "every" ? "09:00" : s.time,
    days: s.kind === "weekly" && s.days.length ? [...s.days] : [1],
    hours: s.kind === "every" ? s.hours : 3,
  };
}

/** The schedule a form says, or null while its time is not one. */
export function draftSchedule(d: RoutineDraft): RoutineSchedule | null {
  if (d.kind === "every") return { kind: "every", hours: Math.min(24, Math.max(1, Math.round(d.hours))) };
  if (!isTime(d.time)) return null;
  return d.kind === "daily" ? { kind: "daily", time: d.time } : { kind: "weekly", days: [...new Set(d.days)].sort((a, b) => a - b), time: d.time };
}

export interface DetailsHost {
  app: AppContext;
  /** The member whose thread is open. */
  member(): Member;
  list(): MembersList | null;
  presets(): readonly AvatarPreset[];
  /** The catalog of the model picker, read the first time the panel needs it. */
  catalog(): Promise<ModelCatalog | null>;
  clis?(): Promise<readonly CliBrain[]>;
  /** Changes the member and reads the list again; false (with a toast) when the core refused. */
  update(id: number, patch: MemberPatch): Promise<boolean>;
  /** A picture of this PC for the member; then the list again. */
  setPicture(id: number, file: WireAttachment): Promise<void>;
  /** The × of the panel. */
  close(): void;
}

export interface DetailsPanel {
  readonly el: HTMLElement;
  /** Draws the panel again (the list changed, or the language). */
  draw(): void;
  /** Another member's thread is open: its routines and memory are read, its tab is 詳細資訊. */
  showMember(): void;
  refreshRoutines(): void;
  refreshCatalog(): void;
  refreshMemory(memberId: number, space?: "own" | "shared"): void;
}

export function createDetails(host: DetailsHost): DetailsPanel {
  const el = document.createElement("aside");
  el.className = "mb-det";
  el.innerHTML =
    `<div class="mb-det-head"><span class="mb-det-fill"></span>${iconButton("x", t("members.close"), ' data-det="close"', "mb-tool")}</div>` +
    `<div class="mb-det-scroll"><div class="mb-who"></div><div class="mb-tabs"></div><div class="mb-tab-body"></div></div>`;
  const who = el.querySelector<HTMLElement>(".mb-who")!;
  const tabBar = el.querySelector<HTMLElement>(".mb-tabs")!;
  const body = el.querySelector<HTMLElement>(".mb-tab-body")!;
  const closeButton = el.querySelector<HTMLElement>('[data-det="close"]')!;

  let tab: DetailsTab = "details";
  let routines: Routine[] | "loading" | "failed" = "loading";
  let routineLoads = 0;
  let draft: RoutineDraft | null = null;
  let catalog: ModelCatalog | null = null;
  let clis: readonly CliBrain[] = [];
  let catalogAsked = false;
  let catalogLoads = 0;
  let picker: ModelPicker | null = null;
  /** The member the panel was last drawn for: its fields are drawn afresh when it changes. */
  let drawnFor: number | null = null;

  const memoryHost = {
    visible: () => el.isConnected && !el.hidden && tab === "memory",
    redraw: () => draw(),
    undo: host.app.undo,
    memberId: () => host.member().id,
    ownerName: () => host.member().name,
    expanded: true,
  };
  const shared = createMemory({ ...memoryHost, space: "shared", onMoved: () => void own.load() });
  const own = createMemory({ ...memoryHost, space: "own", onMoved: () => void shared.load() });
  const memories = [shared, own];

  // ---------- drawing ----------

  function whoHTML(member: Member): string {
    const face =
      `<button type="button" class="btn mb-avbtn" data-det="avatar" data-k="det-avatar" aria-haspopup="menu" aria-expanded="false" aria-label="${esc(t("members.changeAvatar"))}" title="${esc(t("members.changeAvatar"))}">` +
      faceHTML(member, 64) +
      `</button>`;
    if (member.id === RAPHAEL_ID) return face + `<b class="mb-who-name">${esc(member.name)}</b>`;
    return (
      face +
      `<input class="mb-who-name mb-edit" id="mb-det-name" data-member="${member.id}" type="text" maxlength="${NAME_MAX}" value="${esc(member.name)}" aria-label="${esc(t("members.name"))}" autocomplete="off" spellcheck="false">` +
      cliBrainLine(member.model ?? "") +
      `<input class="mb-who-role mb-edit" id="mb-det-role" data-member="${member.id}" type="text" maxlength="${ROLE_MAX}" value="${esc(member.role)}" placeholder="${esc(t("members.role"))}" aria-label="${esc(t("members.role"))}" autocomplete="off" spellcheck="false">`
    );
  }

  function routineRowHTML(routine: Routine): string {
    if (draft?.id === routine.id) return routineFormHTML(draft);
    return (
      `<div class="mb-rt" data-routine="${routine.id}">` +
      `<span class="mb-rt-main"><b>${esc(routine.title)}</b><span>${esc(scheduleText(routine.schedule))}</span></span>` +
      `<span class="mb-rt-ctl">` +
      iconButton("pencil", t("members.routineEdit", { p0: routine.title }), ` data-det="routine-edit" data-id="${routine.id}" data-k="routine-edit:${routine.id}"`, "row mb-tool") +
      iconButton("trash", t("members.routineDelete", { p0: routine.title }), ` data-det="routine-delete" data-id="${routine.id}" data-k="routine-delete:${routine.id}"`, "row mb-tool") +
      tg(routine.enabled, t("members.routineSwitch", { p0: routine.title }), ` data-det="routine-switch" data-id="${routine.id}" data-k="routine-switch:${routine.id}"`) +
      `</span></div>`
    );
  }

  function routineFormHTML(d: RoutineDraft): string {
    const kinds = [
      { value: "daily", label: t("members.daily") },
      { value: "weekly", label: t("members.weekly") },
      { value: "every", label: t("members.every") },
    ];
    const days = Array.from({ length: 7 }, (_, n) => ({ value: String(n), label: dayName(n) }));
    // a routine of several days keeps them until another day is chosen
    const dayValue = d.days.length === 1 ? String(d.days[0]) : d.days.join(",");
    if (d.days.length > 1) days.unshift({ value: dayValue, label: d.days.map(dayName).join(t("members.daysJoin")) });
    const hours = HOUR_STEPS.map((n) => ({ value: String(n), label: n === 1 ? t("members.everyHour") : t("members.everyHours", { p0: n }) }));
    const time = `<input id="mb-r-time" class="mb-fin mb-time" type="time" value="${esc(d.time)}" aria-label="${esc(t("members.time"))}">`;
    return (
      `<div class="mb-rform" data-routine-form>` +
      `<input id="mb-r-title" class="mb-fin" type="text" maxlength="${ROUTINE_TITLE_MAX}" value="${esc(d.title)}" placeholder="${esc(t("members.routineTitle"))}" aria-label="${esc(t("members.routineTitle"))}" autocomplete="off">` +
      `<div class="mb-rsched">${sel(kinds, d.kind, t("members.schedule"), ' data-det="r-kind" data-k="r-kind"')}` +
      (d.kind === "weekly" ? sel(days, dayValue, t("members.day"), ' data-det="r-day" data-k="r-day"') : "") +
      (d.kind === "every" ? sel(hours, String(d.hours), t("members.hours"), ' data-det="r-hours" data-k="r-hours"') : time) +
      `</div>` +
      `<textarea id="mb-r-prompt" class="mb-fin mb-ta" rows="3" maxlength="${ROUTINE_PROMPT_MAX}" placeholder="${esc(t("members.routinePrompt"))}" aria-label="${esc(t("members.routinePrompt"))}">${esc(d.prompt)}</textarea>` +
      actionRow({ cls: "mb-ract", sticky: true, secondary: btn(esc(t("common.cancel")), "sm", ' data-det="r-cancel" data-k="r-cancel"'), primary: { label: esc(t("members.save")), kind: "sm", attrs: ' data-det="r-save" data-k="r-save"' } }) +
      `</div>`
    );
  }

  function routinesHTML(): string {
    const head =
      `<div class="mb-dh"><span>${esc(t("members.routines"))}</span>` +
      iconButton("plus", t("members.routineAdd"), ` data-det="routine-add" data-k="routine-add"${draft?.id === null ? " disabled" : ""}`, "row mb-tool") +
      `</div>`;
    if (routines === "loading") return head + `<span class="spin" role="status" aria-label="${esc(t("common.loading"))}"></span>`;
    if (routines === "failed") return head;
    const rows = routines.map(routineRowHTML).join("");
    return head + (draft && (draft.id === null || !routines.some((r) => r.id === draft?.id)) ? routineFormHTML(draft) : "") + `<div class="mb-rts">${rows}</div>`;
  }

  function detailsHTML(member: Member): string {
    if (member.id === RAPHAEL_ID) return `<section class="mb-field">${routinesHTML()}</section>`;
    const list = host.list();
    const group = list?.groups.find((g) => g.id === member.group_id) ?? null;
    const sources = group?.sources ?? [];
    return (
      `<section class="mb-field"><label class="mb-dh" for="mb-det-personality">${esc(t("members.personality"))}</label>` +
      `<textarea id="mb-det-personality" data-member="${member.id}" class="mb-fin mb-ta" rows="3" maxlength="${PERSONALITY_MAX}" placeholder="${esc(t("members.personalityHint"))}">${esc(member.personality ?? "")}</textarea></section>` +
      `<section class="mb-field"><label class="mb-dh" for="mb-det-instr">${esc(t("members.instructions"))}</label>` +
      `<textarea id="mb-det-instr" data-member="${member.id}" class="mb-fin mb-ta" rows="4" maxlength="${INSTRUCTIONS_MAX}">${esc(member.instructions ?? "")}</textarea></section>` +
      `<section class="mb-field"><div class="mb-dh">${esc(t("members.group"))}</div>` +
      sel(groupChoices(list?.groups ?? []), groupValue(group ? group.id : null), t("members.group"), ' data-det="group" data-k="det-group"') +
      `</section>` +
      `<section class="mb-field"><div class="mb-dh">${esc(t("members.model"))}</div><span class="mp-slot mb-model" data-model-picker="answer"></span></section>` +
      `<section class="mb-field">${routinesHTML()}</section>` +
      (sources.length
        ? `<section class="mb-field"><div class="mb-dh">${esc(t("members.sources"))}</div><div class="mb-chips">` +
          sources.map((source) => `<span class="mb-chip">${esc(sourceLabel(source))}</span>`).join("") +
          `</div></section>`
        : "")
    );
  }

  /** The fields being typed in keep what they hold when the panel draws again. */
  function keepTyping(): void {
    if (!draft) return;
    const title = body.querySelector<HTMLInputElement>("#mb-r-title");
    const prompt = body.querySelector<HTMLTextAreaElement>("#mb-r-prompt");
    const time = body.querySelector<HTMLInputElement>("#mb-r-time");
    if (title) draft.title = title.value;
    if (prompt) draft.prompt = prompt.value;
    if (time) draft.time = time.value;
  }

  function draw(): void {
    const member = host.member();
    const sameMember = drawnFor === member.id;
    closeButton.setAttribute("aria-label", t("members.close"));
    closeButton.title = t("members.close");
    el.setAttribute("aria-label", t("members.panel"));
    const focused = document.activeElement as HTMLElement | null;
    const typingIn = focused && el.contains(focused) && (focused.matches("input, textarea") ? focused.id : null);
    const scroll = el.querySelector<HTMLElement>(".mb-det-scroll")!;
    const scrollTop = scroll.scrollTop;
    const textField = typingIn ? focused as HTMLInputElement | HTMLTextAreaElement : null;
    const selection = textField && typeof textField.selectionStart === "number"
      ? [textField.selectionStart, textField.selectionEnd!, textField.selectionDirection] as const : null;
    const focusKey = focused && el.contains(focused) ? focused.closest<HTMLElement>("[data-k]")?.dataset.k : undefined;
    // the name and role are drawn afresh for another member, and left alone while they are typed in
    if (!sameMember || !(typingIn === "mb-det-name" || typingIn === "mb-det-role")) {
      if (!sameMember && (typingIn === "mb-det-name" || typingIn === "mb-det-role")) {
        void saveField(focused as HTMLInputElement, true);
      }
      who.innerHTML = whoHTML(member);
    } else {
      const av = who.querySelector<HTMLElement>(".mb-avbtn");
      if (av) av.innerHTML = faceHTML(member, 64);
      // A focused field keeps its draft; the other field can still change remotely.
      for (const [id, value] of [["mb-det-name", member.name], ["mb-det-role", member.role]]) {
        const input = who.querySelector<HTMLInputElement>(`#${id}`);
        if (input && id !== typingIn) input.value = value;
      }
    }
    drawnFor = member.id;
    tabBar.innerHTML = tabs(
      [
        { id: "details", label: t("members.details"), attrs: ' data-det="tab" data-tab="details" data-k="det-tab:details"' },
        { id: "memory", label: t("mem.title"), attrs: ' data-det="tab" data-tab="memory" data-k="det-tab:memory"' },
      ],
      tab,
      { label: t("members.panel"), kind: "nav", plain: true, fill: true },
    );
    keepTyping();
    const typingText = typingIn === "mb-det-instr" || typingIn === "mb-det-personality";
    const kept = typingText ? (focused as HTMLElement).closest<HTMLElement>("section") : null;
    if (sameMember && kept && kept.parentElement === body && tab === "details") {
      // Blur saves the box being typed in, so keep its section alive while the other fields update.
      const at = Array.from(body.children).indexOf(kept);
      const next = document.createElement("div");
      next.innerHTML = detailsHTML(member);
      const fresh = Array.from(next.children);
      picker?.close();
      picker = null;
      for (const child of Array.from(body.children)) if (child !== kept) child.remove();
      kept.before(...fresh.slice(0, at));
      kept.after(...fresh.slice(at + 1));
      mountPicker(member);
      scroll.scrollTop = scrollTop;
      return;
    }
    if (typingText) void saveField(focused as HTMLTextAreaElement, true);
    picker?.close();
    picker = null;
    body.innerHTML = tab === "details" ? detailsHTML(member) : memoryHTML(shared.view()) + memoryHTML(own.view());
    if (tab === "details" && member.id !== RAPHAEL_ID) mountPicker(member);
    if (sameMember && typingIn && typingIn !== "mb-det-name" && typingIn !== "mb-det-role") el.querySelector<HTMLElement>(`#${CSS.escape(typingIn)}`)?.focus({ preventScroll: true });
    else if (sameMember && focusKey) el.querySelector<HTMLElement>(`[data-k="${CSS.escape(focusKey)}"]`)?.focus({ preventScroll: true });
    if (sameMember && selection && typingIn) {
      const field = el.querySelector<HTMLInputElement | HTMLTextAreaElement>(`#${CSS.escape(typingIn)}`);
      field?.setSelectionRange(selection[0], selection[1], selection[2] ?? undefined);
    }
    if (sameMember) scroll.scrollTop = scrollTop;
  }

  function mountPicker(member: Member): void {
    const slot = body.querySelector<HTMLElement>(".mb-model");
    if (!slot) return;
    picker = mountModelPicker(slot, {
      role: "answer",
      current: member.model ?? "",
      catalog,
      clis,
      loading: catalog === null && catalogAsked,
      label: t("members.model"),
      key: "det-model",
      showPrice: false,
      lead: { id: "", name: t("members.sameModel") },
      onChoose: (id) => void host.update(member.id, { model: id }),
    });
    if (!catalogAsked) {
      void loadCatalog();
    }
  }

  async function loadCatalog(): Promise<void> {
    const mine = ++catalogLoads;
    catalogAsked = true;
    const [read, brains] = await Promise.all([host.catalog(), host.clis?.() ?? Promise.resolve([])]);
    if (mine !== catalogLoads) return;
    catalog = read;
    clis = brains;
    picker?.update({ catalog: read, clis, loading: false });
  }

  // ---------- routines ----------

  async function loadRoutines(): Promise<void> {
    const mine = ++routineLoads;
    const id = host.member().id;
    try {
      const { routines: read } = await api.routinesList(id);
      if (mine !== routineLoads || id !== host.member().id) return;
      routines = read;
    } catch {
      if (mine !== routineLoads || id !== host.member().id) return;
      if (!Array.isArray(routines)) routines = "failed";
    }
    draw();
  }

  let savingRoutine = false;

  async function saveRoutine(): Promise<void> {
    if (!draft || savingRoutine) return;
    keepTyping();
    const d = draft;
    const title = d.title.trim();
    const prompt = d.prompt.trim();
    const schedule = draftSchedule(d);
    if (!title || !prompt || !schedule) {
      toaster.info(t("members.routineNeeds"));
      return;
    }
    savingRoutine = true;
    try {
      if (d.id === null) await api.routinesCreate({ member_id: host.member().id, title, prompt, schedule });
      else await api.routinesUpdate(d.id, { title, prompt, schedule });
    } catch (err) {
      toaster.info(describeError(err));
      return;
    } finally {
      savingRoutine = false;
    }
    draft = null;
    await loadRoutines();
  }

  async function switchRoutine(id: number, button: HTMLElement): Promise<void> {
    if (typeof routines !== "object") return;
    const routine = routines.find((r) => r.id === id);
    if (!routine) return;
    const enabled = !routine.enabled;
    routines = routines.map((r) => (r.id === id ? { ...r, enabled } : r));
    button.setAttribute("aria-checked", String(enabled));
    button.classList.add("flip");
    try {
      await api.routinesUpdate(id, { enabled });
    } catch (err) {
      toaster.info(describeError(err));
      await loadRoutines();
    }
  }

  async function deleteRoutine(id: number): Promise<void> {
    try {
      await api.routinesDelete(id);
    } catch (err) {
      toaster.info(describeError(err));
    }
    await loadRoutines();
  }

  // ---------- the picture, the name and the role ----------

  function avatarMenu(anchor: HTMLElement): void {
    const member = host.member();
    if (member.id === RAPHAEL_ID) {
      openAvatarPicker(anchor, {
        presets: [],
        onComputer: () => void changeRaphaelAvatar(),
        more: currentAvatar() !== null ? [{ label: t("settings.avatarReset"), run: () => void resetRaphaelAvatar() }] : [],
      });
      return;
    }
    openAvatarPicker(anchor, {
      presets: host.presets(),
      onPreset: (avatar) => void host.update(member.id, { avatar }),
      onComputer: () =>
        pickPicture((file) =>
          void readPicture(file).then((read) => {
            if (read) void host.setPicture(member.id, read);
          }),
        ),
    });
  }

  async function saveField(input: HTMLInputElement | HTMLTextAreaElement, replacing = false): Promise<void> {
    if (input.dataset.saved === "1") return;
    const memberId = input.dataset.member !== undefined ? Number(input.dataset.member) : host.member().id;
    const member = host.list()?.members.find((m) => m.id === memberId) ?? (host.member().id === memberId ? host.member() : null);
    if (!member || member.id === RAPHAEL_ID) return;
    if (replacing || !input.isConnected) input.dataset.saved = "1";
    if (input.id === "mb-det-name") {
      const value = cleanName(input.value);
      if (value === member.name) {
        input.value = member.name;
        return;
      }
      const others = (host.list()?.members ?? []).filter((other) => other.id !== member.id).map((other) => other.name);
      const taken = value !== "" && isMemberNameTaken(value, others);
      if (value === "" || taken) {
        if (taken) toaster.info(t("members.nameTaken", { p0: value }));
        input.value = member.name;
        return;
      }
      input.value = value;
      await host.update(member.id, { name: value });
    } else if (input.id === "mb-det-role") {
      const value = cleanName(input.value);
      input.value = value;
      if (value !== member.role) await host.update(member.id, { role: value });
    } else if (input.id === "mb-det-personality") {
      const value = input.value.trim();
      if (value !== (member.personality ?? "").trim()) await host.update(member.id, { personality: value });
    } else if (input.id === "mb-det-instr") {
      const value = input.value.trim();
      if (value !== (member.instructions ?? "").trim()) await host.update(member.id, { instructions: value });
    }
  }

  // ---------- input ----------

  el.addEventListener("click", (e) => {
    const target = e.target as Element;
    if (memories.some((list) => list.click(target))) return;
    const act = target.closest<HTMLElement>("[data-det]");
    if (!act || (act as HTMLButtonElement).disabled) return;
    const member = host.member();
    switch (act.dataset.det) {
      case "close":
        host.close();
        break;
      case "avatar":
        avatarMenu(act);
        break;
      case "tab":
        tab = act.dataset.tab as DetailsTab;
        if (tab === "memory") for (const list of memories) void list.load();
        draw();
        break;
      case "group":
        openMenu(act, groupChoices(host.list()?.groups ?? []), groupValue(member.group_id), (value) => {
          const groupId = groupFromValue(value);
          if (groupId !== member.group_id) void host.update(member.id, { group_id: groupId });
        });
        break;
      case "routine-add":
        keepTyping();
        draft = newRoutineDraft();
        draw();
        body.querySelector<HTMLElement>("#mb-r-title")?.focus();
        break;
      case "routine-edit": {
        const routine = typeof routines === "object" ? routines.find((r) => r.id === Number(act.dataset.id)) : undefined;
        if (!routine) break;
        draft = routineDraft(routine);
        draw();
        body.querySelector<HTMLElement>("#mb-r-title")?.focus();
        break;
      }
      case "routine-delete":
        void deleteRoutine(Number(act.dataset.id));
        break;
      case "routine-switch":
        void switchRoutine(Number(act.dataset.id), act);
        break;
      case "r-kind":
        openMenu(act, [
          { value: "daily", label: t("members.daily") },
          { value: "weekly", label: t("members.weekly") },
          { value: "every", label: t("members.every") },
        ], draft?.kind ?? "daily", (value) => {
          if (!draft) return;
          keepTyping();
          draft.kind = value as RoutineSchedule["kind"];
          draw();
        });
        break;
      case "r-day":
        openMenu(act, Array.from({ length: 7 }, (_, n) => ({ value: String(n), label: dayName(n) })), String(draft?.days[0] ?? 1), (value) => {
          if (!draft) return;
          keepTyping();
          draft.days = [Number(value)];
          draw();
        });
        break;
      case "r-hours":
        openMenu(act, HOUR_STEPS.map((n) => ({ value: String(n), label: n === 1 ? t("members.everyHour") : t("members.everyHours", { p0: n }) })), String(draft?.hours ?? 3), (value) => {
          if (!draft) return;
          keepTyping();
          draft.hours = Number(value);
          draw();
        });
        break;
      case "r-cancel":
        draft = null;
        draw();
        break;
      case "r-save":
        void saveRoutine();
        break;
    }
  });

  el.addEventListener("input", (e) => {
    const target = e.target as HTMLInputElement | HTMLTextAreaElement;
    for (const list of memories) if (list.input(target)) break;
  });
  el.addEventListener("keydown", (e) => {
    if (memories.some((list) => list.key(e))) return;
    const target = e.target as HTMLElement;
    if (e.key === "Enter" && !e.isComposing && target instanceof HTMLInputElement && (target.id === "mb-det-name" || target.id === "mb-det-role")) {
      e.preventDefault();
      target.blur();
    } else if (e.key === "Escape" && target instanceof HTMLInputElement && (target.id === "mb-det-name" || target.id === "mb-det-role")) {
      e.preventDefault();
      e.stopPropagation();
      const member = host.member();
      target.value = target.id === "mb-det-name" ? member.name : member.role;
      target.blur();
    }
  });
  el.addEventListener("focusout", (e) => {
    const target = e.target as HTMLElement;
    if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement) {
      if (["mb-det-name", "mb-det-role", "mb-det-personality", "mb-det-instr"].includes(target.id)) void saveField(target);
    }
  });

  return {
    el,
    draw,
    refreshRoutines() { void loadRoutines(); },
    refreshCatalog() {
      catalogLoads++; catalog = null; catalogAsked = false;
      if (picker && el.isConnected && !el.hidden) void loadCatalog();
    },
    refreshMemory(memberId, space) {
      if (!memoryHost.visible()) return;
      if (space !== "own") void shared.load();
      if (space !== "shared" && memberId === host.member().id) void own.load();
    },
    showMember() {
      tab = "details";
      draft = null;
      routines = "loading";
      for (const list of memories) list.reset();
      draw();
      void loadRoutines();
    },
  };
}
