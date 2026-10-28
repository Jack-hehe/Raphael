// The profile and details panel share the member-owned skill and command editor. New members
// keep these in a draft; existing members write each explicit resource action immediately.
import * as api from "./api";
import { openActions } from "./member-column";
import { describeError } from "./format";
import { t } from "./i18n";
import type { Member, MemberCommand, MemberPatch, MemberSkill, MemberVoice, Routine, WireAttachment } from "./types";
import { btn, esc, iconButton, openMenu, sel, tg, toaster } from "./ui";

export interface ProfileDraft {
  skills: (MemberSkill & { importedIds?: number[] })[];
  commands: MemberCommand[];
  memories: { name: string; body: string; description: string }[];
  routines: Routine[];
}
export const emptyProfileDraft = (): ProfileDraft => ({ skills: [], commands: [], memories: [], routines: [] });
let draftSerial = 0;
export const nextProfileDraftId = (): number => --draftSerial;

/** Removes each successful item from the queue, so a failed import can be retried safely. */
export async function flushMemberProfileDraft(memberId: number, draft: ProfileDraft): Promise<void> {
  while (draft.skills.length) {
    const skill = draft.skills[0]!;
    if (!skill.importedIds) {
      const answer = await api.membersSkillsImport(memberId, skill.source);
      skill.importedIds = (answer.skills ?? [answer.skill]).map(imported => imported.id);
    }
    for (const id of skill.importedIds) await api.membersSkillsUpdate(memberId, id, skill.enabled);
    draft.skills.shift();
  }
  while (draft.commands.length) {
    const { name, description, template } = draft.commands[0]!;
    await api.membersCommandsSave(memberId, { name, description, template });
    draft.commands.shift();
  }
  while (draft.memories.length) {
    const { body, description } = draft.memories[0]!;
    await api.core("memory.create", { member_id: memberId, space: "own", body, description });
    draft.memories.shift();
  }
  while (draft.routines.length) {
    const { title, prompt, schedule, enabled } = draft.routines[0]!;
    await api.routinesCreate({ member_id: memberId, title, prompt, schedule, enabled });
    draft.routines.shift();
  }
}

export interface ResourceHost {
  memberId: number | null;
  tab: "skills" | "commands" | "voice";
  /** The narrow details panel gives skills and commands separate subpages. */
  standalone?: boolean;
  member?: Member;
  draft?: ProfileDraft;
  /** Profile fields remain drafts until its footer is saved. The details panel writes now. */
  patch?(patch: MemberPatch): void;
  changed?(): void;
  onCount?(counts: { skills: number; commands: number }): void;
}
export interface ResourceEditor {
  refresh(): Promise<void>;
  suggest(command: Omit<MemberCommand, "id">): void;
  destroy(): void;
}

export function mountMemberResources(container: HTMLElement, host: ResourceHost): ResourceEditor {
  let alive = true;
  let busy = false;
  let failed = false;
  let generation = 0;
  let loaded = host.memberId === null;
  let skills: MemberSkill[] = host.draft?.skills ?? [];
  let commands: MemberCommand[] = host.draft?.commands ?? [];
  let voices: MemberVoice[] = [];
  let configured = false;
  const voiceState = { voice: host.member?.voice ?? "", read_aloud: host.member?.read_aloud ?? false, notify: host.member?.notify ?? false };
  let command: Omit<MemberCommand, "id"> & { id?: number } = { name: "", description: "", template: "" };
  let suggested = false;
  let source = "";
  const counts = (): void => { if (host.tab !== "voice") host.onCount?.({ skills: skills.length, commands: commands.length }); };
  const empty = (text: string): string => `<p class="mp-empty">${esc(text)}</p>`;
  const label = (text: string, content: string): string => `<label class="mp-label"><span>${esc(text)}</span>${content}</label>`;
  const input = (id: string, value: string, placeholder: string, max = 200): string => `<input id="${id}" class="mb-fin" maxlength="${max}" value="${esc(value)}" placeholder="${esc(placeholder)}" aria-label="${esc(placeholder)}" autocomplete="off">`;
  const capture = (): void => {
    source = container.querySelector<HTMLInputElement>("#mp-skill-source")?.value ?? source;
    command.name = container.querySelector<HTMLInputElement>("#mp-command-name")?.value ?? command.name;
    command.description = container.querySelector<HTMLInputElement>("#mp-command-description")?.value ?? command.description;
    command.template = container.querySelector<HTMLTextAreaElement>("#mp-command-template")?.value ?? command.template;
  };
  function draw(): void {
    if (!alive) return;
    capture();
    const off = busy ? " disabled" : "";
    const skillHTML = `<section class="mp-card"><h3>${esc(t("members.profile.skillsTitle"))}</h3>` +
      `<div class="mp-add">${input("mp-skill-source", source, t("members.profile.source"), 2048)}${btn(esc(t("members.profile.import")), "sm", ` data-res="import"${off}`)}</div>` +
      skills.map(s => `<div class="mp-resource"><div><b>${esc(s.name)}</b><p>${esc(s.description || s.source)}</p></div>${tg(s.enabled, `${t("members.profile.enable")} ${s.name}`, ` data-res="skill-toggle" data-id="${s.id}"${off}`)}${iconButton("more", t("members.more", { p0: s.name }), ` data-res="skill-menu" data-id="${s.id}" aria-haspopup="menu" aria-expanded="false"${off}`)}</div>`).join("") +
      (skills.length ? "" : empty(t("members.profile.noSkills"))) + `</section>`;
    const commandsHTML = `<section class="mp-card"><h3>${esc(t("members.profile.commands"))}</h3>` +
      commands.map(c => `<div class="mp-resource"><div><b>/${esc(c.name)}</b><p>${esc(c.description)}</p></div>${iconButton("more", t("members.more", { p0: c.name }), ` data-res="command-menu" data-id="${c.id}" aria-haspopup="menu" aria-expanded="false"${off}`)}</div>`).join("") +
      (commands.length ? "" : empty(t("members.profile.noCommands"))) +
      `<div class="mp-command-fields">${input("mp-command-name", command.name, t("members.profile.commandName"), 32)}${input("mp-command-description", command.description, t("members.profile.commandDescription"), 200)}</div>` +
      `<textarea id="mp-command-template" class="mb-fin mb-ta" rows="4" maxlength="8000" placeholder="${esc(t("members.profile.commandTemplate", { input: "{input}" }))}" aria-label="${esc(t("members.profile.commandTemplate", { input: "{input}" }))}">${esc(command.template)}</textarea>` +
      `<div class="mp-end">${command.id === undefined ? "" : btn(esc(t("common.cancel")), "sm", ` data-res="command-cancel"${off}`)}${btn(esc(t("members.profile.saveCommand")), "sm", ` data-res="command-save"${off}`)}</div></section>`;
    const voiceChoices = [{ value: "", label: t("members.profile.defaultVoice") }, ...voices.map(v => ({ value: v.id, label: v.name }))];
    if (voiceState.voice && !voiceChoices.some(v => v.value === voiceState.voice)) voiceChoices.push({ value: voiceState.voice, label: voiceState.voice });
    container.innerHTML = failed ? `<div class="mp-error" role="alert">${btn(esc(t("members.profile.retry")), "sm", ' data-res="retry"')}</div>` : !loaded ? `<span class="spin" role="status" aria-label="${esc(t("common.loading"))}"></span>` :
      host.tab === "voice" ? `<section class="mp-card">${label(t("members.profile.voiceLabel"), sel(voiceChoices, voiceState.voice, t("members.profile.voiceLabel"), ` data-res="voice"${!configured || busy ? " disabled" : ""}`))}${configured ? "" : empty(t("members.profile.voiceUnconfigured"))}</section>` +
        `<section class="mp-card">${(["read_aloud", "notify"] as const).map(key => `<div class="mp-resource"><span>${esc(t(key === "read_aloud" ? "members.profile.readAloud" : "members.profile.notify"))}</span>${tg(voiceState[key], t(key === "read_aloud" ? "members.profile.readAloud" : "members.profile.notify"), ` data-res="${key}"${off}`)}</div>`).join("")}</section>` :
      (host.tab === "skills" ? skillHTML : "") + (host.tab === "commands" || !host.standalone ? commandsHTML : "");
  }
  async function refresh(): Promise<void> {
    const mine = ++generation;
    try {
      if (host.tab === "voice") {
        const answer = await api.membersVoices();
        if (!alive || mine !== generation) return;
        voices = answer.voices;
        configured = answer.configured;
      } else if (host.memberId !== null) {
        const [s, c] = await Promise.all([api.membersSkillsList(host.memberId), api.membersCommandsList(host.memberId)]);
        if (!alive || mine !== generation) return;
        skills = s.skills;
        commands = c.commands;
        if (suggested && command.id === undefined && command.name) command.id = commands.find(saved => saved.name === command.name)?.id;
        counts();
      }
      loaded = true;
      failed = false;
    } catch (error) {
      if (!alive || mine !== generation) return;
      failed = true;
      toaster.info(describeError(error));
    }
    draw();
  }
  async function run(action: () => Promise<void>): Promise<void> {
    if (busy) return;
    capture();
    busy = true;
    draw();
    try { await action(); if (alive) { host.changed?.(); counts(); } }
    catch (error) { toaster.info(describeError(error)); }
    finally { busy = false; draw(); }
  }
  async function patch(next: MemberPatch): Promise<void> {
    if (host.patch) host.patch(next);
    else if (host.memberId !== null) await api.membersUpdate(host.memberId, next);
    Object.assign(voiceState, next);
  }
  function choose(action: string | undefined, id: number, target: HTMLButtonElement): void {
    if (action === "retry") { void refresh(); return; }
    if (action === "voice") {
      openMenu(target, [{ value: "", label: t("members.profile.defaultVoice") }, ...voices.map(v => ({ value: v.id, label: v.name }))], voiceState.voice, value => void run(() => patch({ voice: value })));
      return;
    }
    if (action === "read_aloud" || action === "notify") { void run(() => patch({ [action]: !voiceState[action] })); return; }
    if (action === "command-edit") { suggested = false; command = { ...commands.find(c => c.id === id)! }; container.innerHTML = ""; draw(); container.querySelector<HTMLInputElement>("#mp-command-name")?.focus(); return; }
    if (action === "command-cancel") { suggested = false; command = { name: "", description: "", template: "" }; container.innerHTML = ""; draw(); return; }
    void run(async () => {
      if (action === "import") {
        const value = source.trim();
        if (!value) return;
        if (host.memberId === null) {
          if (!skills.some(s => s.source === value)) skills.push({ id: nextProfileDraftId(), source: value, name: value, description: "", enabled: true });
        } else {
          const answer = await api.membersSkillsImport(host.memberId, value);
          for (const skill of answer.skills ?? [answer.skill]) {
            const index = skills.findIndex(existing => existing.id === skill.id);
            if (index < 0) skills.push(skill); else skills[index] = skill;
          }
        }
        source = "";
        const field = alive ? container.querySelector<HTMLInputElement>("#mp-skill-source") : null;
        if (field) field.value = "";
      } else if (action === "skill-toggle") {
        const skill = skills.find(s => s.id === id)!;
        if (host.memberId !== null) await api.membersSkillsUpdate(host.memberId, id, !skill.enabled);
        skill.enabled = !skill.enabled;
      } else if (action === "skill-delete") {
        if (host.memberId !== null) await api.membersSkillsDelete(host.memberId, id);
        const index = skills.findIndex(s => s.id === id);
        if (index >= 0) skills.splice(index, 1);
      } else if (action === "command-delete") {
        if (host.memberId !== null) await api.membersCommandsDelete(host.memberId, id);
        const index = commands.findIndex(c => c.id === id);
        if (index >= 0) commands.splice(index, 1);
      } else if (action === "command-save") {
        const name = command.name.trim().replace(/^\//, "").toLowerCase();
        if (!/^[a-z][a-z0-9_-]{0,31}$/.test(name) || !command.template.includes("{input}")) { toaster.info(t("members.profile.badCommand", { input: "{input}" })); return; }
        const next = { ...command, name, description: command.description.trim(), template: command.template.trim() };
        if (host.memberId !== null) {
          const saved = (await api.membersCommandsSave(host.memberId, next) as { command?: MemberCommand } | undefined)?.command;
          if (saved) {
            const index = commands.findIndex(existing => existing.id === saved.id);
            if (index < 0) commands.push(saved); else commands[index] = saved;
          } else commands = (await api.membersCommandsList(host.memberId)).commands;
        } else {
          const index = commands.findIndex(c => c.id === next.id || c.name.toLowerCase() === name.toLowerCase());
          const saved = { ...next, id: index >= 0 ? commands[index]!.id : nextProfileDraftId() };
          if (index >= 0) commands[index] = saved; else commands.push(saved);
        }
        command = { name: "", description: "", template: "" };
        suggested = false;
        if (alive) container.innerHTML = "";
      }
    });
  }
  const click = (e: Event): void => {
    const target = (e.target as Element).closest<HTMLButtonElement>("[data-res]");
    if (!target || target.disabled || busy) return;
    capture();
    const action = target.dataset.res;
    const id = Number(target.dataset.id);

    if (action === "skill-menu") openActions(target, [{ id: "skill-delete", label: t("members.profile.delete"), icon: "trash" }], choice => choose(choice, id, target));
    else if (action === "command-menu") openActions(target, [{ id: "command-edit", label: t("members.profile.editResource"), icon: "pencil" }, { id: "command-delete", label: t("members.profile.delete"), icon: "trash" }], choice => choose(choice, id, target));
    else choose(action, id, target);
  };
  container.addEventListener("click", click);
  draw();
  void refresh();
  return {
    refresh,
    suggest(next) {
      capture();
      suggested = true;
      command = { ...next, ...(commands.find(saved => saved.name === next.name)?.id === undefined ? {} : { id: commands.find(saved => saved.name === next.name)!.id }) };
      container.innerHTML = "";
      draw();
    },
    destroy() { alive = false; generation++; container.removeEventListener("click", click); },
  };
}

/** Converts the user's selected crop into the file sent to members.set_avatar. */
export async function croppedMemberPicture(file: WireAttachment, crop: { x: number; y: number; zoom: number }): Promise<WireAttachment> {
  const image = new Image();
  await new Promise<void>((resolve, reject) => { image.onload = () => resolve(); image.onerror = () => reject(new Error(t("agent.attachUnreadable", { p0: file.name }))); image.src = `data:${file.mime};base64,${file.data}`; });
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = 512;
  const context = canvas.getContext("2d");
  if (!context) throw new Error(t("agent.attachUnreadable", { p0: file.name }));
  const scale = 512 / Math.min(image.naturalWidth, image.naturalHeight) * crop.zoom;
  const width = image.naturalWidth * scale;
  const height = image.naturalHeight * scale;
  context.drawImage(image, (512 - width) * crop.x, (512 - height) * crop.y, width, height);
  return { name: "avatar.png", mime: "image/png", data: canvas.toDataURL("image/png").split(",")[1]! };
}
