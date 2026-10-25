// One profile dialog for creation and editing. The footer owns member fields; explicit resource
// actions belong to the resource itself. In creation mode all subresources wait for a member id.
import * as api from "./api";
import { openActions } from "./member-column";
import { cropDialog } from "./avatar-settings";
import { describeError } from "./format";
import { t } from "./i18n";
import { groupChoices, groupFromValue, groupValue, openAvatarPicker, openDialog, pickPicture, readPicture, type Dialog, type NewMemberResult } from "./member-dialogs";
import { croppedMemberPicture, emptyProfileDraft, flushMemberProfileDraft, mountMemberResources, nextProfileDraftId, type ResourceEditor } from "./member-profile-resources";
import { memberPreset, presetChoices, presetCommand, randomMemberName, type ProfilePreset } from "./member-profile-presets";
import { INSTRUCTIONS_MAX, NAME_MAX, PERSONALITY_MAX, ROLE_MAX, cleanName, faceHTML, isMemberNameTaken, raphaelOf, scheduleText } from "./members";
import type { MemoryItem } from "./memory-view";
import type { AvatarPreset, Member, MemberGroup, MemberPatch, NewMember, Routine, WireAttachment } from "./types";
import { btn, esc, iconButton, openMenu, sel, tabs, tg, toaster } from "./ui";
import { openRoutineDialog } from "./routine-dialog";
import "./member-profile.css";

export type ProfileTab = "identity" | "soul" | "skills" | "memory" | "routines" | "permissions" | "voice";
const TAB_KEYS = { identity: "members.profile.identity", soul: "members.profile.soul", skills: "members.profile.skills", memory: "members.profile.memory", routines: "members.profile.routines", permissions: "members.profile.permissions", voice: "members.profile.voice" } as const;
const TABS: ProfileTab[] = ["identity", "soul", "skills", "memory", "routines", "permissions", "voice"];

export interface MemberProfileHost {
  groups: readonly MemberGroup[];
  presets: readonly AvatarPreset[];
  taken: readonly string[];
  groupId?: number | null;
  create?(result: NewMemberResult): Promise<boolean | Member>;
  update?(id: number, patch: MemberPatch): Promise<boolean>;
  setPicture?(id: number, picture: WireAttachment): Promise<void>;
  saved?(member: Member): void;
}

const field = (id: string, label: string, value: string, max: number): string => `<label class="mb-frow mp-label" for="${id}"><span>${esc(label)}</span><input id="${id}" class="mb-fin" maxlength="${max}" value="${esc(value)}" autocomplete="off"></label>`;
const area = (id: string, label: string, value: string, max: number, rows: number): string => `<label class="mb-frow mp-label" for="${id}"><span>${esc(label)}</span><textarea id="${id}" class="mb-fin mb-ta" maxlength="${max}" rows="${rows}">${esc(value)}</textarea></label>`;

export function openEditMemberDialog(host: MemberProfileHost, member: Member, tab: ProfileTab = "identity"): Dialog {
  return openMemberProfile(host, member, tab);
}

export function openMemberProfile(host: MemberProfileHost, member?: Member, initialTab: ProfileTab = "identity"): Dialog {
  let tab = initialTab;
  let busy = false;
  let alive = true;
  let created: Member | null = null;
  let creationAccepted = false;
  let acceptedName = "";
  let groupId = member?.group_id ?? host.groupId ?? null;
  let avatar = member?.avatar ?? "";
  let picture: WireAttachment | null = null;
  let pictureUrl = member?.avatar_url ?? null;
  let preset: ProfilePreset = "custom";
  const draft = emptyProfileDraft();
  const voice = { voice: member?.voice ?? "", read_aloud: member?.read_aloud ?? false, notify: member?.notify ?? false };
  let ask = member?.ask_before_members ?? false;
  let reply = member?.reply_style ?? "full";
  let memories: MemoryItem[] = [];
  let routines: Routine[] = [];
  let memoryLoaded = false;
  let routinesLoaded = false;
  let resources: ResourceEditor | null = null;
  let voiceEditor: ResourceEditor | null = null;
  let suggestedCommand: ReturnType<typeof presetCommand> = null;
  const dialog = openDialog({
    title: t(member ? "members.profile.edit" : "members.newMember"),
    cls: "mb-new mp-dialog",
    body: `<div class="mp-layout"><nav class="member-profile-nav">${tabs(TABS.map(key => ({ id: key, label: t(TAB_KEYS[key]), attrs: ` id="mp-tab-${key}" aria-controls="mp-page-${key}" data-dlg="tab" data-tab="${key}"` })), tab, { kind: "nav", plain: true, label: t("members.profile.edit") })}</nav><div class="mp-pages">` +
      TABS.map(key => `<section class="mp-page" role="tabpanel" id="mp-page-${key}" aria-labelledby="mp-tab-${key}"${key === tab ? "" : " hidden"}>${pageHTML(key)}</section>`).join("") + `</div></div>`,
    actions: { secondary: btn(esc(t("common.cancel")), "", ' data-dlg="cancel"'), primary: { label: esc(t(member ? "members.save" : "members.create")), attrs: ` data-dlg="${member ? "save" : "create"}"` } },
    canClose: () => !busy,
    onAct(act, target) {
      if (busy) return;
      if (act === "cancel") dialog.close();
      else if (act === "save" || act === "create") void save();
      else if (act === "tab") selectTab(target.dataset.tab as ProfileTab);
      else if (act === "avatar") avatarMenu(target);
      else if (act === "group") openMenu(target, groupChoices(host.groups), groupValue(groupId), value => { groupId = groupFromValue(value); drawGroup(); });
      else if (act === "preset") openMenu(target, presetChoices(), preset, value => applyPreset(value as ProfilePreset));
      else if (act === "female" || act === "male") { nameInput.value = randomMemberName(act, host.taken); drawFace(); }
      else if (act === "reply") openMenu(target, [{ value: "full", label: t("members.profile.full") }, { value: "conversational", label: t("members.profile.conversational") }], reply, value => { reply = value as typeof reply; drawReply(); });
      else if (act === "ask") { ask = !ask; target.setAttribute("aria-checked", String(ask)); }
      else if (act === "memory-add") void addMemory();
      else if (act === "memory-menu") openActions(target, [{ id: "delete", label: t("members.profile.delete"), icon: "trash" }], () => void resourceAction(async () => {
        const index = Number(target.dataset.index);
        if (member) await api.core("memory.delete", { member_id: member.id, space: "own", name: memories[index]!.name });
        else draft.memories.splice(index, 1);
        await loadMemory();
      }));
      else if (act === "routine-add") routineForm();
      else if (act === "routine-menu") {
        const routine = routines.find(r => r.id === Number(target.dataset.id));
        if (routine) openActions(target, [{ id: "edit", label: t("members.profile.editResource"), icon: "pencil" }, { id: "delete", label: t("members.profile.delete"), icon: "trash" }], choice => {
          if (choice === "edit") routineForm(routine);
          else void resourceAction(async () => {
            if (member) await api.routinesDelete(routine.id);
            else draft.routines.splice(draft.routines.findIndex(r => r.id === routine.id), 1);
            await loadRoutines();
          });
        });
      }
      else if (act === "routine-toggle") void resourceAction(async () => {
        const routine = routines.find(r => r.id === Number(target.dataset.id))!;
        if (member) await api.routinesUpdate(routine.id, { enabled: !routine.enabled });
        else routine.enabled = !routine.enabled;
        await loadRoutines();
      });
      else if (act === "memory-retry") void loadMemory();
      else if (act === "routine-retry") void loadRoutines();
    },
    onEnter() { if (tab === "identity") void save(); else if (tab === "memory") void addMemory(); },
    onClose() { alive = false; resources?.destroy(); voiceEditor?.destroy(); },
  });
  const nameInput = dialog.el.querySelector<HTMLInputElement>("#mb-new-name")!;
  const disabledBeforeBusy = new Map<HTMLInputElement | HTMLTextAreaElement | HTMLButtonElement, boolean>();
  const value = (id: string): string => dialog.el.querySelector<HTMLInputElement | HTMLTextAreaElement>(`#${id}`)!.value;
  const set = (id: string, text: string): void => { dialog.el.querySelector<HTMLInputElement | HTMLTextAreaElement>(`#${id}`)!.value = text; };
  function pageHTML(key: ProfileTab): string {
    if (key === "identity") return `<section class="mp-card mp-fields"><h3>${esc(t("members.profile.identity"))}</h3><label class="mp-label"><span>${esc(t("members.profile.preset"))}</span><span class="mp-preset"></span></label>` +
      `<div class="mp-avatar"><span class="mb-new-av"></span>${btn(esc(t("members.changeAvatar")), "sm", ' data-dlg="avatar" aria-haspopup="menu" aria-expanded="false"')}</div>` +
      field("mb-new-name", t("members.name"), member?.name ?? "", NAME_MAX) +
      `<div class="mp-name-actions">${btn(esc(t("members.profile.femaleName")), "sm", ' data-dlg="female"')}${btn(esc(t("members.profile.maleName")), "sm", ' data-dlg="male"')}</div>` +
      field("mb-new-role", t("members.role"), member?.role ?? "", ROLE_MAX) + field("mp-blurb", t("members.profile.blurb"), member?.blurb ?? "", 160) +
      `<div class="mb-frow mp-label"><span>${esc(t("members.group"))}</span><span class="mb-new-group"></span></div></section>`;
    if (key === "soul") return `<section class="mp-card mp-fields"><h3>${esc(t("members.profile.soul"))}</h3>` + area("mb-new-personality", t("members.personality"), member?.personality ?? "", PERSONALITY_MAX, 4) +
      area("mb-new-instr", t("members.instructions"), member?.instructions ?? "", INSTRUCTIONS_MAX, 8) + `<div class="mp-counter" aria-live="polite"></div></section>` +
      `<section class="mp-card"><h3>${esc(t("members.profile.replyStyle"))}</h3><div class="mp-reply"></div><p class="mp-reply-hint"></p></section>`;
    if (key === "skills") return `<div class="mp-resources"></div>`;
    if (key === "memory") return `<section class="mp-card"><h3>${esc(t("members.profile.memory"))}</h3><div class="mp-memory-list"></div><div class="mp-add mp-card-add">${field("mp-memory", t("members.profile.remember"), "", 1200)}${btn(esc(t("members.profile.add")), "sm", ' data-dlg="memory-add"')}</div></section>`;
    if (key === "routines") return `<section class="mp-card"><div class="mp-card-head"><h3>${esc(t("members.profile.routines"))}</h3>${btn(esc(t("members.routineAdd")), "sm", ' data-dlg="routine-add"')}</div><div class="mp-routine-list"></div></section>`;
    if (key === "permissions") return `<section class="mp-card"><h3>${esc(t("members.profile.permissions"))}</h3><div class="mp-resource"><div><b>${esc(t("members.profile.ask"))}</b><p>${esc(t("members.profile.askHint"))}</p></div>${tg(ask, t("members.profile.ask"), ' data-dlg="ask"')}</div></section>`;
    return `<div class="mp-voice"></div>`;
  }
  function drawFace(): void {
    dialog.el.querySelector<HTMLElement>(".mb-new-av")!.innerHTML = pictureUrl ? faceHTML({ id: member?.id ?? -1, name: nameInput.value, avatar_url: pictureUrl }, 104) :
      `<span class="av mp-person" aria-hidden="true"><svg width="80" height="80" viewBox="0 0 64 64" fill="currentColor"><circle cx="32" cy="22" r="11"/><path d="M11 58v-5a21 21 0 0 1 42 0v5Z"/></svg></span>`;
  }
  function drawGroup(): void { dialog.el.querySelector<HTMLElement>(".mb-new-group")!.innerHTML = sel(groupChoices(host.groups), groupValue(groupId), t("members.group"), ' data-dlg="group"'); }
  function drawPreset(): void { dialog.el.querySelector<HTMLElement>(".mp-preset")!.innerHTML = sel(presetChoices(), preset, t("members.profile.preset"), ' data-dlg="preset"'); }
  function count(): void { dialog.el.querySelector<HTMLElement>(".mp-counter")!.textContent = t("members.profile.instructionsCount", { count: value("mb-new-instr").length, max: INSTRUCTIONS_MAX }); }
  function drawReply(): void {
    dialog.el.querySelector<HTMLElement>(".mp-reply")!.innerHTML = sel([{ value: "full", label: t("members.profile.full") }, { value: "conversational", label: t("members.profile.conversational") }], reply, t("members.profile.replyStyle"), ' data-dlg="reply"');
    dialog.el.querySelector<HTMLElement>(".mp-reply-hint")!.textContent = t(reply === "full" ? "members.profile.fullHint" : "members.profile.conversationalHint");
  }
  function applyPreset(next: ProfilePreset): void {
    preset = next;
    if (next !== "custom") {
      const fields = memberPreset(next);
      set("mb-new-role", fields.role ?? ""); set("mp-blurb", fields.blurb ?? "");
      set("mb-new-personality", fields.personality ?? ""); set("mb-new-instr", fields.instructions ?? "");
      if (!member) {
        const command = presetCommand(next)!;
        if (!draft.commands.some(c => c.name === command.name)) draft.commands.push({ ...command, id: nextProfileDraftId() });
        if (resources) void resources.refresh();
      } else { suggestedCommand = presetCommand(next); if (suggestedCommand) resources?.suggest(suggestedCommand); }
    }
    drawPreset(); count();
  }
  function selectTab(next: ProfileTab): void {
    tab = next;
    dialog.el.querySelectorAll<HTMLElement>(".member-profile-nav .tab").forEach(button => {
      const selected = button.dataset.tab === tab;
      button.setAttribute("aria-selected", String(selected)); button.tabIndex = selected ? 0 : -1;
    });
    dialog.el.querySelectorAll<HTMLElement>(".mp-page").forEach(page => { page.hidden = page.id !== `mp-page-${tab}`; });
    if (tab === "skills" && !resources) {
      resources = mountMemberResources(dialog.el.querySelector<HTMLElement>(".mp-resources")!, { memberId: member?.id ?? null, tab: "skills", draft });
      if (suggestedCommand) resources.suggest(suggestedCommand);
    }
    if (tab === "voice" && !voiceEditor) voiceEditor = mountMemberResources(dialog.el.querySelector<HTMLElement>(".mp-voice")!, { memberId: member?.id ?? null, tab: "voice", member: { ...member, ...voice } as Member, patch: patch => Object.assign(voice, patch) });
    if (tab === "memory" && !memoryLoaded) void loadMemory();
    if (tab === "routines" && !routinesLoaded) void loadRoutines();
  }
  function avatarMenu(anchor: HTMLElement): void {
    openAvatarPicker(anchor, {
      presets: host.presets,
      onPreset(next) { avatar = next; picture = null; pictureUrl = host.presets.find(p => p.avatar === next)?.url ?? null; drawFace(); },
      onComputer: () => pickPicture(file => void (async () => {
        try {
          const read = await readPicture(file);
          if (!read || !alive) return;
          const crop = await cropDialog(`data:${read.mime};base64,${read.data}`);
          if (!crop || !alive) return;
          const cropped = await croppedMemberPicture(read, crop);
          if (!alive) return;
          picture = cropped; avatar = ""; pictureUrl = `data:${cropped.mime};base64,${cropped.data}`; drawFace();
        } catch (error) { toaster.info(describeError(error)); }
      })()),
    });
  }
  async function resourceAction(action: () => Promise<void>): Promise<void> {
    if (busy) return;
    busy = true;
    setBusy();
    try { await action(); } catch (error) { toaster.info(describeError(error)); }
    finally { busy = false; setBusy(); }
  }
  function setBusy(): void {
    dialog.el.querySelectorAll<HTMLInputElement | HTMLTextAreaElement | HTMLButtonElement>("input,textarea,button").forEach(control => {
      if (busy) {
        if (!disabledBeforeBusy.has(control)) disabledBeforeBusy.set(control, control.disabled);
        control.disabled = true;
      } else {
        if (disabledBeforeBusy.has(control)) control.disabled = disabledBeforeBusy.get(control)!;
        disabledBeforeBusy.delete(control);
        // A retry after creation saves remaining resources. Identity fields already belong to
        // the new member, so they cannot silently change while the resource queue is retried.
        if (creationAccepted && (control.closest("#mp-page-identity,#mp-page-soul,#mp-page-permissions,#mp-page-voice"))) control.disabled = true;
      }
    });
  }
  async function loadMemory(): Promise<void> {
    const list = dialog.el.querySelector<HTMLElement>(".mp-memory-list")!;
    try {
      if (member) memories = (await api.core<{ memories: MemoryItem[] }>("memory.list", { member_id: member.id, space: "own" })).memories;
      else memories = draft.memories.map(m => ({ ...m, type: "reference", origin: "user", updated: "", expires: null }));
      if (!alive) return;
      memoryLoaded = true;
      list.innerHTML = memories.length ? memories.map((m, index) => `<div class="mp-resource"><span>${esc(m.description)}</span>${iconButton("more", t("members.more", { p0: m.description }), ` data-dlg="memory-menu" data-index="${index}" aria-haspopup="menu" aria-expanded="false"`)}</div>`).join("") : `<p class="mp-empty">${esc(t("members.profile.noMemory"))}</p>`;
    } catch (error) { if (alive) { list.innerHTML = btn(esc(t("members.profile.retry")), "sm", ' data-dlg="memory-retry"'); toaster.info(describeError(error)); } }
  }
  async function addMemory(): Promise<void> {
    const body = value("mp-memory").trim();
    if (!body || busy) return;
    await resourceAction(async () => {
      if (member) await api.core("memory.create", { member_id: member.id, space: "own", body, description: body.slice(0, 80) });
      else draft.memories.push({ name: `draft-${-nextProfileDraftId()}`, body, description: body.slice(0, 80) });
      set("mp-memory", ""); await loadMemory();
    });
  }
  async function loadRoutines(): Promise<void> {
    const list = dialog.el.querySelector<HTMLElement>(".mp-routine-list")!;
    try {
      routines = member ? (await api.routinesList(member.id)).routines : draft.routines;
      if (!alive) return;
      routinesLoaded = true;
      list.innerHTML = routines.length ? routines.map(r => `<div class="mp-resource"><div><b>${esc(r.title)}</b><p>${esc(scheduleText(r.schedule))}</p></div>${tg(r.enabled, r.title, ` data-dlg="routine-toggle" data-id="${r.id}"`)}${iconButton("more", t("members.more", { p0: r.title }), ` data-dlg="routine-menu" data-id="${r.id}" aria-haspopup="menu" aria-expanded="false"`)}</div>`).join("") : `<p class="mp-empty">${esc(t("members.profile.noRoutines"))}</p>`;
    } catch (error) { if (alive) { list.innerHTML = btn(esc(t("members.profile.retry")), "sm", ' data-dlg="routine-retry"'); toaster.info(describeError(error)); } }
  }
  function routineForm(existing?: Routine): void {
    const owner = member ?? { ...raphaelOf(null), id: -1, name: nameInput.value.trim() || t("members.newMember"), avatar_url: pictureUrl ?? undefined };
    openRoutineDialog({
      members: [owner], memberId: owner.id, routine: existing,
      onSaved: () => void loadRoutines(), onDeleted: () => void loadRoutines(),
      ...(!member ? {
        async save(fields: Pick<Routine, "member_id" | "title" | "prompt" | "schedule">): Promise<Routine> {
          if (existing) { Object.assign(existing, fields); return existing; }
          const routine: Routine = { ...fields, id: nextProfileDraftId(), enabled: true, last_run: null, next_run: null };
          draft.routines.push(routine);
          return routine;
        },
        async delete(): Promise<void> { draft.routines.splice(draft.routines.findIndex(r => r.id === existing?.id), 1); },
      } : {}),
    });
  }
  async function save(): Promise<void> {
    if (busy) return;
    const name = cleanName(nameInput.value);
    if (!name || isMemberNameTaken(name, host.taken.filter(taken => taken.toLocaleLowerCase() !== member?.name.toLocaleLowerCase()))) {
      selectTab("identity"); nameInput.focus(); toaster.info(name ? t("members.nameTaken", { p0: name }) : t("members.nameEmpty")); return;
    }
    const fields: NewMember = { name, role: cleanName(value("mb-new-role")), blurb: cleanName(value("mp-blurb")), personality: value("mb-new-personality").trim(), instructions: value("mb-new-instr").trim(), group_id: groupId, reply_style: reply, ask_before_members: ask, ...voice, ...(avatar ? { avatar } : {}) };
    busy = true; setBusy();
    try {
      if (member) {
        const okay = host.update ? await host.update(member.id, fields) : !!(await api.membersUpdate(member.id, fields));
        if (!okay) return;
        if (picture) {
          if (host.setPicture) await host.setPicture(member.id, picture);
          else await api.membersSetAvatar(member.id, picture);
          picture = null;
        }
        host.saved?.({ ...member, ...fields }); dialog.close();
      } else {
        if (!created) {
          if (!creationAccepted) {
            const result = { member: fields, picture };
            const answer = host.create ? await host.create(result) : (await api.membersCreate(fields)).member;
            if (!answer) return;
            creationAccepted = true;
            acceptedName = name;
            if (answer === true && !Object.values(draft).some(items => items.length)) { dialog.close(); return; }
            if (typeof answer === "object") created = answer;
          }
          created ??= (await api.membersList()).members.find(m => m.name === acceptedName) ?? null;
          if (!created) throw new Error(t("members.profile.resourceError"));
        }
        if (!host.create && picture) { await api.membersSetAvatar(created.id, picture); picture = null; }
        await flushMemberProfileDraft(created.id, draft);
        host.saved?.(created); dialog.close();
      }
    } catch (error) { toaster.info(describeError(error)); }
    finally { busy = false; setBusy(); }
  }
  nameInput.addEventListener("input", drawFace);
  dialog.el.querySelector("#mb-new-instr")!.addEventListener("input", count);
  dialog.el.querySelector(".member-profile-nav")!.addEventListener("keydown", event => {
    const e = event as KeyboardEvent;
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) return;
    e.preventDefault();
    const index = TABS.indexOf(tab);
    const next = e.key === "Home" ? TABS[0]! : e.key === "End" ? TABS[TABS.length - 1]! : TABS[(index + (e.key === "ArrowDown" ? 1 : -1) + TABS.length) % TABS.length]!;
    selectTab(next); dialog.el.querySelector<HTMLElement>(`#mp-tab-${next}`)!.focus();
  });
  dialog.el.querySelector(".member-profile-nav [role=tablist]")!.setAttribute("aria-orientation", "vertical");
  drawFace(); drawGroup(); drawPreset(); drawReply(); count(); selectTab(initialTab);
  if (initialTab === "identity") nameInput.focus();
  else dialog.el.querySelector<HTMLElement>(`#mp-tab-${initialTab}`)?.focus();
  return dialog;
}
