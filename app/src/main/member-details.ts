// The open member's panel keeps its avatar and editable identity above the conversation slot,
// personality/instruction folds and resource rows. Each resource opens a subpage with a back
// button; fields being typed in survive live member updates. Raphael keeps his special avatar,
// routines and memory, while every member uses the same global model and tool capabilities.

import * as api from "./api";
import { mountMemberResources } from "./member-profile-resources";
import "./member-panel.css";
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
import type { AppContext, AvatarPreset, Member, MemberPatch, MembersList, Routine, RoutineSchedule, WireAttachment } from "./types";
import { actionRow, btn, esc, ico, iconButton, openMenu, sel, tg, toaster } from "./ui";

export type DetailsTab = "details" | "memory" | "routines" | "skills" | "commands" | "voice";

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
  /** Changes the member and reads the list again; false (with a toast) when the core refused. */
  update(id: number, patch: MemberPatch): Promise<boolean>;
  /** A picture of this PC for the member; then the list again. */
  setPicture(id: number, file: WireAttachment): Promise<void>;
  /** The × of the panel. */
  close(): void;
  edit?(member: Member): void;
  hide?(member: Member): void;
}

export interface DetailsPanel {
  readonly el: HTMLElement;
  /** Draws the panel again (the list changed, or the language). */
  draw(): void;
  /** Another member's thread is open: its routines and memory are read, its tab is 詳細資訊. */
  showMember(): void;
  refreshRoutines(): void;
  refreshMemory(memberId: number, space?: "own" | "shared"): void;
}

export function createDetails(host: DetailsHost): DetailsPanel {
  const el = document.createElement("aside");
  el.className = "mb-det";
  el.innerHTML =
    `<div class="mb-det-head"><span class="mb-det-fill"></span>${iconButton("pencil", t("members.sideEdit"), ' data-det="edit"', "mb-tool")}${iconButton("x", t("members.close"), ' data-det="close"', "mb-tool")}</div>` +
    `<div class="mb-det-scroll"><div class="mb-who"></div><div class="mb-tabs"></div><div class="mb-tab-body"></div></div>`;
  const who = el.querySelector<HTMLElement>(".mb-who")!;
  const tabBar = el.querySelector<HTMLElement>(".mb-tabs")!;
  const body = el.querySelector<HTMLElement>(".mb-tab-body")!;
  const closeButton = el.querySelector<HTMLElement>('[data-det="close"]')!;
  const editButton = el.querySelector<HTMLElement>('[data-det="edit"]')!;

  let tab: DetailsTab = "details";
  let routines: Routine[] | "loading" | "failed" = "loading";
  let routineLoads = 0;
  let draft: RoutineDraft | null = null;
  /** The long fields that are open (個性, 指示): closed, each is one line. */
  const openFields = new Set<string>();
  /** The member the panel was last drawn for: its fields are drawn afresh when it changes. */
  let drawnFor: number | null = null;
  let counts = { skills: 0, commands: 0, memory: 0 };
  let countEpoch = 0;
  let resources: ReturnType<typeof mountMemberResources> | null = null;
  let resourceKey = "";
  let memoryDraft = "";
  async function loadCounts(): Promise<void> {
    const epoch = ++countEpoch, id = host.member().id;
    const result = await Promise.allSettled([
      id === RAPHAEL_ID ? Promise.resolve({skills: []}) : api.core<{skills: unknown[]}>("members.skills.list", {member_id: id}),
      id === RAPHAEL_ID ? Promise.resolve({commands: []}) : api.core<{commands: unknown[]}>("members.commands.list", {member_id: id}),
      api.core<{ memories: unknown[] }>("memory.list", { member_id: id, space: "own" }),
    ]);
    if (epoch !== countEpoch || id !== host.member().id) return;
    counts = {
      skills: result[0].status === "fulfilled" ? result[0].value?.skills?.length ?? 0 : 0,
      commands: result[1].status === "fulfilled" ? result[1].value?.commands?.length ?? 0 : 0,
      memory: result[2].status === "fulfilled" ? result[2].value?.memories?.length ?? 0 : 0,
    };
    draw();
  }
  function pageRow(page: DetailsTab, label: string, icon: Parameters<typeof ico>[0], detail = ""): string {
    return `<button type="button" class="btn mb-panel-row" data-det="tab" data-tab="${page}" data-k="det-tab:${page}">${ico(icon)}<span>${esc(label)}</span><small>${esc(detail)}</small>${ico("right")}</button>`;
  }
  function pageRows(member: Member): string {
    const active = Array.isArray(routines) ? routines.filter(r => r.enabled).length : 0;
    return `<nav class="mb-panel-pages" aria-label="${esc(t("members.details"))}">` +
      pageRow("routines", t("members.routines"), "clock", t("members.panelActive", {p0: active})) +
      pageRow("memory", t("mem.title"), "memory", t("members.panelNotes", {p0: counts.memory})) +
      (member.id === RAPHAEL_ID ? "" : pageRow("skills", t("members.panelSkills"), "sparkle", t("members.panelCount", {p0: counts.skills})) +
      pageRow("commands", t("members.panelCommands"), "steps", t("members.panelCount", {p0: counts.commands})) +
      pageRow("voice", t("members.panelVoice"), "voice") +
      `<button type="button" class="btn mb-panel-row" data-det="hide">${ico("eyeOff")}<span class="mb-panel-row-copy"><span>${esc(t("members.panelHide"))}</span><small>${esc(t("members.panelHideHint"))}</small></span>${ico("right")}</button>`) + `</nav>`;
  }

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
    const time = `<input id="mb-r-time" class="mb-fin mb-time" type="text" inputmode="numeric" maxlength="5" placeholder="HH:MM" value="${esc(d.time)}" aria-label="${esc(t("members.time"))}" autocomplete="off">`;
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
    const rows = routines.length ? routines.map(routineRowHTML).join("") : `<p class="mp-empty">${esc(t("members.profile.noRoutines"))}</p>`;
    return head + (draft && (draft.id === null || !routines.some((r) => r.id === draft?.id)) ? routineFormHTML(draft) : "") + `<div class="mb-rts">${rows}</div>`;
  }

  /**
   * A long field, closed to its name and its first line (or 「未填寫」) behind a real button; open,
   * the box under it. Open while it is being typed in, so a redraw never closes it under the owner.
   */
  function foldHTML(id: string, label: string, value: string, box: string): string {
    const open = openFields.has(id);
    const peek = value.trim().split(/\r?\n/, 1)[0] || t("members.notSet");
    return `<section class="mb-field mb-fold-field">` +
      `<button type="button" class="btn mb-fold-head" data-det="fold" data-field="${id}" data-k="fold:${id}" aria-expanded="${open}" aria-controls="${id}-body">` +
      `<span class="mb-dh">${esc(label)}</span>${open ? "" : `<span class="mb-peek">${esc(peek)}</span>`}${ico("down")}</button>` +
      `<div class="mb-fold-body" id="${id}-body"${open ? "" : " hidden"}>${box}</div></section>`;
  }

  function detailsHTML(member: Member): string {
    if (member.id === RAPHAEL_ID) return `<div data-slot="conversations"></div>` + pageRows(member);
    const list = host.list();
    const group = list?.groups.find((g) => g.id === member.group_id) ?? null;
    const sources = group?.sources ?? [];
    return (
      `<div data-slot="conversations"></div>` + foldHTML("mb-det-personality", t("members.personality"), member.personality ?? "",
        `<textarea id="mb-det-personality" data-member="${member.id}" class="mb-fin mb-ta" rows="3" maxlength="${PERSONALITY_MAX}" placeholder="${esc(t("members.personalityHint"))}">${esc(member.personality ?? "")}</textarea>`) +
      foldHTML("mb-det-instr", t("members.instructions"), member.instructions ?? "",
        `<textarea id="mb-det-instr" data-member="${member.id}" class="mb-fin mb-ta" rows="4" maxlength="${INSTRUCTIONS_MAX}">${esc(member.instructions ?? "")}</textarea>`) +
      `<section class="mb-field"><div class="mb-dh">${esc(t("members.group"))}</div>` +
      sel(groupChoices(list?.groups ?? []), groupValue(group ? group.id : null), t("members.group"), ' data-det="group" data-k="det-group"') +
      `</section>` +
      pageRows(member) +
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
    editButton.hidden = member.id === RAPHAEL_ID;
    editButton.setAttribute("aria-label", t("members.sideEdit"));
    editButton.title = t("members.sideEdit");
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
    who.hidden = tab !== "details";
    tabBar.innerHTML = tab === "details"
      ? ""
      : `<button type="button" class="btn mb-panel-back" data-det="tab" data-tab="details" data-k="det-tab:details">${ico("left")}${esc(t("members.panelBack"))}</button>`;
    keepTyping();
    const typingText = typingIn === "mb-det-instr" || typingIn === "mb-det-personality";
    const kept = typingText ? (focused as HTMLElement).closest<HTMLElement>("section") : null;
    if (sameMember && kept && kept.parentElement === body && tab === "details") {
      // Blur saves the box being typed in, so keep its section alive while the other fields update.
      const at = Array.from(body.children).indexOf(kept);
      const next = document.createElement("div");
      next.innerHTML = detailsHTML(member);
      const fresh = Array.from(next.children);
      for (const child of Array.from(body.children)) if (child !== kept) child.remove();
      kept.before(...fresh.slice(0, at));
      kept.after(...fresh.slice(at + 1));
      scroll.scrollTop = scrollTop;
      return;
    }
    if (typingText) void saveField(focused as HTMLTextAreaElement, true);
    const resourceTab = tab === "skills" || tab === "commands" || tab === "voice" ? tab : null;
    const nextResourceKey = resourceTab ? `${member.id}:${resourceTab}` + (resourceTab === "voice" ? `:${member.voice}:${member.read_aloud}:${member.notify}` : "") : "";
    if (resourceKey !== nextResourceKey) { resources?.destroy(); resources = null; resourceKey = nextResourceKey; }
    if (resourceTab) {
      if (!resources) {
        body.replaceChildren();
        resources = mountMemberResources(body, {memberId: member.id, tab: resourceTab, member, standalone: true, changed: () => void loadCounts()});
      }
    } else {
      const input = body.querySelector<HTMLInputElement>("#mb-memory-new");
      if (input) memoryDraft = input.value;
      body.innerHTML = tab === "details" ? detailsHTML(member) : tab === "routines" ? routinesHTML() :
        `<div class="mb-addrow mb-panel-memory-add"><input id="mb-memory-new" class="mb-fin" maxlength="1200" value="${esc(memoryDraft)}" placeholder="${esc(t("members.panelMemoryHint"))}" aria-label="${esc(t("members.panelMemoryHint"))}">${btn(esc(t("members.panelMemoryAdd")), "", ' data-det="memory-add"')}</div>` + memoryHTML(shared.view()) + memoryHTML(own.view());
    }
    if (sameMember && typingIn && typingIn !== "mb-det-name" && typingIn !== "mb-det-role") el.querySelector<HTMLElement>(`#${CSS.escape(typingIn)}`)?.focus({ preventScroll: true });
    else if (sameMember && focusKey) el.querySelector<HTMLElement>(`[data-k="${CSS.escape(focusKey)}"]`)?.focus({ preventScroll: true });
    if (sameMember && selection && typingIn) {
      const field = el.querySelector<HTMLInputElement | HTMLTextAreaElement>(`#${CSS.escape(typingIn)}`);
      field?.setSelectionRange(selection[0], selection[1], selection[2] ?? undefined);
    }
    if (sameMember) scroll.scrollTop = scrollTop;
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
      case "edit": host.edit?.(member); break;
      case "hide": host.hide?.(member); break;
      case "memory-add": {
        const input = body.querySelector<HTMLInputElement>("#mb-memory-new");
        const value = input?.value.trim();
        if (!value) break;
        (act as HTMLButtonElement).disabled = true;
        void api.core("memory.create", {member_id: member.id, space: "own", body: value}).then(async () => {
          memoryDraft = ""; if (input) input.value = "";
          await own.load(); await loadCounts();
        }).catch(err => toaster.info(describeError(err))).finally(() => { (act as HTMLButtonElement).disabled = false; });
        break;
      }
      case "avatar":
        avatarMenu(act);
        break;
      case "fold": {
        const field = act.dataset.field ?? "";
        const opening = !openFields.has(field);
        if (opening) openFields.add(field); else openFields.delete(field);
        draw();
        if (opening) el.querySelector<HTMLTextAreaElement>(`#${CSS.escape(field)}`)?.focus({ preventScroll: true });
        break;
      }
      case "tab":
        tab = act.dataset.tab as DetailsTab;
        if (tab === "memory") for (const list of memories) void list.load();
        if (tab === "routines") void loadRoutines();
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
    refreshMemory(memberId, space) {
      if (memberId === host.member().id) { void loadCounts(); void resources?.refresh(); }
      if (!memoryHost.visible()) return;
      if (space !== "own") void shared.load();
      if (space !== "shared" && memberId === host.member().id) void own.load();
    },
    showMember() {
      tab = "details";
      memoryDraft = ""; counts = {skills: 0, commands: 0, memory: 0};
      void loadCounts();
      draft = null;
      routines = "loading";
      for (const list of memories) list.reset();
      draw();
      void loadRoutines();
    },
  };
}
