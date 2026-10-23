// The small windows of the 拉斐爾 page's members: 新增成員 (the mockup's `new-member`: picture, name,
// role, instructions and group, with 「取消」 and 「建立」), the routine the core offers with a
// new member of a group with places to look (taken only with 「加入」), the question before a member
// or a group is deleted, a group's name, a group's 看的地方, and the popover that changes a picture
// (the presets, if the owner has any, and 「從電腦選…」).
//
// Each dialog sits over the window on a dimmed scrim, grows in as the settings' dialogs do (styles.css,
// nothing under reduced motion), closes on Escape, on its × and on a click beside it, and gives the
// focus back to what had it. What a person types goes into the page through `esc` only.

import { t } from "./i18n";
import { describeError } from "./format";
import {
  GROUP_NAME_MAX,
  INSTRUCTIONS_MAX,
  NAME_MAX,
  PERSONALITY_MAX,
  ROLE_MAX,
  cleanName,
  faceHTML,
  initialOf,
  isMemberNameTaken,
  parseSource,
  scheduleText,
  sourceLabel,
} from "./members";
import type { AvatarPreset, MemberGroup, MemberSource, NewMember, RoutineOffer, WireAttachment } from "./types";
import { btn, closeMenu, esc, ico, iconButton, openMenu, openPopover, sel, toaster } from "./ui";

// ---------- the dialog ----------

export interface Dialog {
  readonly el: HTMLElement;
  close(): void;
}

export interface DialogOptions {
  title: string;
  /** The fields, as HTML. */
  body: string;
  /** The buttons at the foot, as HTML: text buttons, bordered. */
  actions: string;
  /** A click on something with a `data-dlg` in the dialog. */
  onAct(act: string, target: HTMLElement, dialog: Dialog): void;
  /** Enter in a one-line field: the dialog's main action, if it has one. */
  onEnter?(dialog: Dialog): void;
  /** Whether the dialog may be closed by Escape, the backdrop or × right now. */
  canClose?(): boolean;
  /** The dialog closed, however it was. */
  onClose?(): void;
  /** A class of the dialog's own, for its layout. */
  cls?: string;
}

/** Opens a dialog over the window (see the top of the file). The first field, or the first button, has the focus. */
export function openDialog(o: DialogOptions): Dialog {
  closeMenu();
  const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  const scrim = document.createElement("div");
  scrim.className = "mb-scrim";
  const id = `mb-dlg-${Math.random().toString(36).slice(2, 8)}`;
  scrim.innerHTML =
    `<div class="mb-dialog${o.cls ? ` ${o.cls}` : ""}" role="dialog" aria-modal="true" aria-labelledby="${id}">` +
    `<div class="mb-dlg-head"><h2 id="${id}">${esc(o.title)}</h2>${iconButton("x", t("common.close"), ' data-dlg="close"')}</div>` +
    `<div class="mb-dlg-body">${o.body}</div>` +
    `<div class="mb-dlg-act">${o.actions}</div>` +
    `</div>`;
  let open = true;
  const mayClose = (): boolean => !o.canClose || o.canClose();
  const isTop = (): boolean =>
    !document.getElementById("app-popover") &&
    !document.querySelector(".av-crop-scrim") &&
    Array.from(document.querySelectorAll(".mb-scrim")).pop() === scrim;
  const dialog: Dialog = {
    el: scrim.querySelector<HTMLElement>(".mb-dialog")!,
    close() {
      if (!open) return;
      open = false;
      closeMenu();
      const keepFocus =
        document.activeElement instanceof HTMLElement &&
        document.activeElement !== document.body &&
        !scrim.contains(document.activeElement);
      scrim.remove();
      document.removeEventListener("keydown", onKey, true);
      if (!keepFocus) opener?.focus({ preventScroll: true });
      o.onClose?.();
    },
  };
  const onKey = (e: KeyboardEvent): void => {
    if (!isTop()) return;
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopImmediatePropagation();
      if (mayClose()) dialog.close();
    } else if (e.key === "Tab") {
      const stops = [
        ...dialog.el.querySelectorAll<HTMLElement>(
          'button:not(:disabled), input:not([type=file]):not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])',
        ),
      ];
      if (stops.length === 0) return;
      const first = stops[0]!;
      const last = stops[stops.length - 1]!;
      const active = document.activeElement as HTMLElement | null;
      if (e.shiftKey && (active === first || !active || !dialog.el.contains(active))) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && (active === last || !active || !dialog.el.contains(active))) {
        e.preventDefault();
        first.focus();
      }
    } else if (
      e.key === "Enter" &&
      !e.isComposing &&
      e.keyCode !== 229 &&
      !e.repeat &&
      e.target instanceof HTMLInputElement &&
      dialog.el.contains(e.target) &&
      o.onEnter
    ) {
      e.preventDefault();
      o.onEnter(dialog);
    }
  };
  scrim.addEventListener("click", (e) => {
    if (e.target === scrim) {
      if (mayClose()) dialog.close();
      return;
    }
    const target = (e.target as Element).closest<HTMLElement>("[data-dlg]");
    if (!target || (target as HTMLButtonElement).disabled) return;
    if (target.dataset.dlg === "close") {
      if (mayClose()) dialog.close();
    } else o.onAct(target.dataset.dlg ?? "", target, dialog);
  });
  document.addEventListener("keydown", onKey, true);
  document.body.append(scrim);
  (dialog.el.querySelector<HTMLElement>("input:not([type=file]), textarea") ?? dialog.el.querySelector<HTMLElement>(".mb-dlg-act .btn"))?.focus();
  return dialog;
}

/** Asks before something is deleted: 「刪除「name」？」 with 「取消」 and 「刪除」. Resolves true for 刪除. */
export function confirmDelete(name: string): Promise<boolean> {
  return new Promise((resolve) => {
    let answer = false;
    openDialog({
      title: t("members.deleteConfirm", { p0: name }),
      body: "",
      cls: "mb-confirm",
      actions: btn(esc(t("common.cancel")), "", ' data-dlg="cancel"') + btn(esc(t("members.delete")), "danger", ' data-dlg="delete"'),
      onAct(act, _target, dialog) {
        answer = act === "delete";
        dialog.close();
      },
      onClose: () => resolve(answer),
    });
  });
}

/**
 * The routine the core offers with a new member (`members.create`'s `routine_offer`): its title and
 * when it runs, with 「不用」 and 「加入」. It is offered, never made unasked: resolves true for 加入.
 */
export function offerRoutine(offer: RoutineOffer): Promise<boolean> {
  return new Promise((resolve) => {
    let answer = false;
    openDialog({
      title: t("members.offerTitle"),
      cls: "mb-confirm mb-offer",
      body: `<div class="mb-offer-t"><b>${esc(offer.title)}</b><i>${esc(scheduleText(offer.schedule))}</i></div>`,
      actions: btn(esc(t("members.offerNo")), "", ' data-dlg="no"') + btn(esc(t("members.offerYes")), "pri", ' data-dlg="yes"'),
      onAct(act, _target, dialog) {
        answer = act === "yes";
        dialog.close();
      },
      onClose: () => resolve(answer),
    });
  });
}

/** A group's name: a new group's (`title` 新增分組) or a new name. Resolves with the name, or null when it is left. */
export function askGroupName(title: string, current = ""): Promise<string | null> {
  return new Promise((resolve) => {
    let result: string | null = null;
    const save = (dialog: Dialog): void => {
      const input = dialog.el.querySelector<HTMLInputElement>("#mb-group-name")!;
      const name = cleanName(input.value);
      if (name === "") {
        toaster.info(t("members.nameEmpty"));
        input.focus();
        return;
      }
      result = name;
      dialog.close();
    };
    openDialog({
      title,
      body: field("mb-group-name", t("members.groupName"), current, GROUP_NAME_MAX),
      actions: btn(esc(t("common.cancel")), "", ' data-dlg="cancel"') + btn(esc(current ? t("members.save") : t("members.create")), "pri", ' data-dlg="save"'),
      onAct(act, _target, dialog) {
        if (act === "save") save(dialog);
        else dialog.close();
      },
      onEnter: save,
      onClose: () => resolve(result),
    });
  });
}

/** A labelled one-line field of a dialog. */
function field(id: string, label: string, value: string, max: number): string {
  return (
    `<label class="mb-frow" for="${id}"><span>${esc(label)}</span>` +
    `<input id="${id}" class="mb-fin" type="text" maxlength="${max}" value="${esc(value)}" autocomplete="off" spellcheck="false"></label>`
  );
}

/**
 * A group's 看的地方: its places as chips with ×, and a field that adds a GitHub `owner/repo` or a
 * Notion page. Resolves with the list to keep, or null when it is left.
 */
export function editSources(group: MemberGroup): Promise<MemberSource[] | null> {
  return new Promise((resolve) => {
    let sources = [...group.sources];
    let result: MemberSource[] | null = null;
    const chips = (): string =>
      sources
        .map(
          (source, i) =>
            `<span class="mb-chip">${esc(sourceLabel(source))}` +
            `<button type="button" class="chip-x" data-dlg="drop" data-i="${i}" aria-label="${esc(t("members.sourceRemove", { p0: sourceLabel(source) }))}">${ico("x")}</button></span>`,
        )
        .join("");
    const add = (dialog: Dialog): void => {
      const input = dialog.el.querySelector<HTMLInputElement>("#mb-source")!;
      const parsed = parseSource(input.value);
      if (!parsed) {
        if (input.value.trim() !== "") toaster.info(t("members.sourceBad"));
        return;
      }
      if (!sources.some((source) => source.kind === parsed.kind && source.ref === parsed.ref)) sources = [...sources, parsed];
      input.value = "";
      dialog.el.querySelector<HTMLElement>(".mb-chips")!.innerHTML = chips();
      input.focus();
    };
    openDialog({
      title: `${t("members.sources")}：${group.name}`,
      cls: "mb-sources",
      body:
        `<div class="mb-chips">${chips()}</div>` +
        `<div class="mb-addrow"><input id="mb-source" class="mb-fin" type="text" placeholder="${esc(t("members.sourceHint"))}" aria-label="${esc(t("members.sourceHint"))}" autocomplete="off" spellcheck="false">` +
        btn(esc(t("members.sourceAdd")), "", ' data-dlg="add"') +
        `</div>`,
      actions: btn(esc(t("common.cancel")), "", ' data-dlg="cancel"') + btn(esc(t("members.save")), "pri", ' data-dlg="save"'),
      onAct(act, target, dialog) {
        if (act === "add") add(dialog);
        else if (act === "drop") {
          sources = sources.filter((_, i) => i !== Number(target.dataset.i));
          dialog.el.querySelector<HTMLElement>(".mb-chips")!.innerHTML = chips();
          dialog.el.querySelector<HTMLInputElement>("#mb-source")!.focus();
        } else if (act === "save") {
          result = sources;
          dialog.close();
        } else dialog.close();
      },
      onEnter: add,
      onClose: () => resolve(result),
    });
  });
}

// ---------- a picture ----------

/** The kinds of picture a member may have, and the largest (the composer's limits for images, services/attachments.py). */
const PICTURE_TYPES: Record<string, string> = { png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", webp: "image/webp", gif: "image/gif" };
export const PICTURE_ACCEPT = ".png,.jpg,.jpeg,.webp,.gif,image/png,image/jpeg,image/webp,image/gif";
export const PICTURE_MAX_BYTES = 10 * 1024 * 1024;

/** The type of a picture of this PC, from the system or from the end of its name; null for anything else. */
export function pictureType(file: { name: string; type: string }): string | null {
  const type = file.type.trim().toLowerCase().replace("image/jpg", "image/jpeg");
  if (Object.values(PICTURE_TYPES).includes(type)) return type;
  const extension = /\.([^.]+)$/.exec(file.name)?.[1]?.toLowerCase() ?? "";
  return type === "" || type === "application/octet-stream" ? (PICTURE_TYPES[extension] ?? null) : null;
}

/** `bytes` in base64, a slice at a time. */
function base64(bytes: Uint8Array): string {
  let binary = "";
  for (let at = 0; at < bytes.length; at += 0x8000) binary += String.fromCharCode(...bytes.subarray(at, at + 0x8000));
  return btoa(binary);
}

/** A picture of this PC as `members.set_avatar` takes it, or null (with a toast) when it is not one or is too big. */
export async function readPicture(file: File): Promise<WireAttachment | null> {
  const mime = pictureType(file);
  if (mime === null) {
    toaster.info(t("agent.attachKind"));
    return null;
  }
  if (file.size <= 0 || file.size > PICTURE_MAX_BYTES) {
    toaster.info(t("agent.attachTooBig", { p0: file.name, p1: 10, p2: 20 }));
    return null;
  }
  try {
    return { name: file.name, mime, data: base64(new Uint8Array(await file.arrayBuffer())) };
  } catch {
    toaster.info(t("agent.attachUnreadable", { p0: file.name }));
    return null;
  }
}

/** The one hidden file input of the pictures: outside the popover, which closes when the file dialog takes the focus. */
let picker: HTMLInputElement | null = null;
let picked: ((file: File) => void) | null = null;

/** Opens the file dialog for a picture; `then` gets the file chosen. */
export function pickPicture(then: (file: File) => void): void {
  if (!picker?.isConnected) {
    picker = document.createElement("input");
    picker.type = "file";
    picker.accept = PICTURE_ACCEPT;
    picker.hidden = true;
    picker.className = "mb-picker";
    picker.addEventListener("change", () => {
      const file = picker?.files?.[0];
      if (file && picked) picked(file);
    });
    document.body.append(picker);
  }
  picked = then;
  picker.value = "";
  picker.click();
}

export interface AvatarPickerOptions {
  presets: readonly AvatarPreset[];
  /** A preset was chosen: its avatar value, `preset:<stem>`. */
  onPreset?(avatar: string): void;
  /** 「從電腦選…」: what it does (the file dialog, or Raphael's own picker of the app). */
  onComputer(): void;
  /** More actions under 從電腦選 (Raphael's 還原). */
  more?: readonly { label: string; run(): void }[];
}

/** The small popover of a picture: the preset grid, if there are presets, and 「從電腦選…」. */
export function openAvatarPicker(anchor: HTMLElement, o: AvatarPickerOptions): void {
  const grid = o.presets.length
    ? `<div class="mb-presets" role="group" aria-label="${esc(t("settings.avatar"))}">` +
      o.presets
        .map(
          (preset, i) =>
            `<button type="button" role="menuitem" tabindex="-1" class="mb-preset" data-preset="${esc(preset.avatar)}" aria-label="${esc(t("members.preset", { p0: i + 1 }))}">` +
            `<img src="${esc(preset.url)}" alt="" draggable="false"></button>`,
        )
        .join("") +
      `</div><div class="menu-sep" role="separator"></div>`
    : "";
  const actions = [{ label: t("members.fromPc"), run: o.onComputer }, ...(o.more ?? [])];
  const el = openPopover(
    anchor,
    grid +
      actions
        .map((action, i) => `<button type="button" role="menuitem" tabindex="-1" class="menu-action" data-pick="${i}"><span class="mt">${esc(action.label)}</span></button>`)
        .join(""),
    { className: "menu mb-avpop", role: "menu", label: t("settings.avatar") },
  );
  el.addEventListener("click", (e) => {
    const target = (e.target as Element).closest<HTMLElement>("button");
    if (!target) return;
    closeMenu();
    if (target.dataset.preset !== undefined) o.onPreset?.(target.dataset.preset);
    else actions[Number(target.dataset.pick)]?.run();
  });
  el.querySelector<HTMLElement>("button")?.focus();
}

// ---------- 新增成員 ----------

/** What 建立 hands over: the member, and a picture of this PC to set once it exists. */
export interface NewMemberResult {
  member: NewMember;
  picture: WireAttachment | null;
}

export interface NewMemberOptions {
  groups: readonly MemberGroup[];
  /** The group it starts in (the one the menu was opened from), null for 未分組. */
  groupId: number | null;
  presets: readonly AvatarPreset[];
  /** The names already taken (the names are unique). */
  taken: readonly string[];
  /** Makes the member; true when it was made and the dialog may close. */
  create(result: NewMemberResult): Promise<boolean>;
}

/** The value of a group in the dropdowns: its id, or `none` for 未分組. */
export const groupValue = (id: number | null): string => (id === null ? "none" : String(id));
export const groupFromValue = (value: string): number | null => (value === "none" ? null : Number(value));

/** The choices of a group dropdown: 未分組, then the groups. */
export const groupChoices = (groups: readonly MemberGroup[]): { value: string; label: string }[] => [
  { value: "none", label: t("members.ungrouped") },
  ...groups.map((group) => ({ value: String(group.id), label: group.name })),
];

/** 新增成員, the mockup's `new-member`. */
export function openNewMember(o: NewMemberOptions): Dialog {
  let groupId = o.groupId;
  let avatar = "";
  let picture: WireAttachment | null = null;
  let pictureUrl: string | null = null;
  let busy = false;
  const groupOf = (): MemberGroup | undefined => o.groups.find((group) => group.id === groupId);

  const dialog = openDialog({
    title: t("members.newMember"),
    cls: "mb-new",
    body:
      `<div class="mb-new-face"><span class="mb-new-av"></span>${btn(esc(t("members.changeAvatar")), "sm", ' data-dlg="avatar" aria-haspopup="menu" aria-expanded="false"')}</div>` +
      field("mb-new-name", t("members.name"), "", NAME_MAX) +
      field("mb-new-role", t("members.role"), "", ROLE_MAX) +
      `<label class="mb-frow mb-frow-top" for="mb-new-personality"><span>${esc(t("members.personality"))}</span>` +
      `<textarea id="mb-new-personality" class="mb-fin mb-ta" rows="3" maxlength="${PERSONALITY_MAX}" placeholder="${esc(t("members.personalityHint"))}"></textarea></label>` +
      `<label class="mb-frow mb-frow-top" for="mb-new-instr"><span>${esc(t("members.instructions"))}</span>` +
      `<textarea id="mb-new-instr" class="mb-fin mb-ta" rows="3" maxlength="${INSTRUCTIONS_MAX}"></textarea></label>` +
      `<div class="mb-frow"><span>${esc(t("members.group"))}</span><span class="mb-new-group"></span></div>`,
    actions: btn(esc(t("common.cancel")), "", ' data-dlg="cancel"') + btn(esc(t("members.create")), "pri", ' data-dlg="create"'),
    canClose: () => !busy,
    onEnter: () => void create(),
    onAct(act, target) {
      if (busy) return;
      if (act === "cancel") dialog.close();
      else if (act === "create") void create();
      else if (act === "avatar") avatarMenu(target);
      else if (act === "group") {
        openMenu(target, groupChoices(o.groups), groupValue(groupId), (value) => {
          groupId = groupFromValue(value);
          draw();
        });
      }
    },
  });
  const nameInput = dialog.el.querySelector<HTMLInputElement>("#mb-new-name")!;
  const face = dialog.el.querySelector<HTMLElement>(".mb-new-av")!;

  function drawFace(): void {
    face.innerHTML = faceHTML({ id: -1, name: nameInput.value, avatar_url: pictureUrl }, 64);
  }

  function draw(): void {
    drawFace();
    const group = groupOf();
    dialog.el.querySelector<HTMLElement>(".mb-new-group")!.innerHTML = sel(
      groupChoices(o.groups),
      groupValue(group ? group.id : null),
      t("members.group"),
      ' data-dlg="group"',
    );
  }

  function avatarMenu(anchor: HTMLElement): void {
    openAvatarPicker(anchor, {
      presets: o.presets,
      onPreset(value) {
        avatar = value;
        picture = null;
        pictureUrl = o.presets.find((preset) => preset.avatar === value)?.url ?? null;
        drawFace();
      },
      onComputer: () =>
        pickPicture((file) =>
          void readPicture(file).then((read) => {
            if (!read) return;
            picture = read;
            avatar = "";
            pictureUrl = `data:${read.mime};base64,${read.data}`;
            drawFace();
          }),
        ),
    });
  }

  async function create(): Promise<void> {
    if (busy) return;
    const name = cleanName(nameInput.value);
    if (name === "") {
      toaster.info(t("members.nameEmpty"));
      nameInput.focus();
      return;
    }
    if (isMemberNameTaken(name, o.taken)) {
      toaster.info(t("members.nameTaken", { p0: name }));
      nameInput.focus();
      return;
    }
    const group = groupOf();
    const member: NewMember = {
      name,
      role: cleanName(dialog.el.querySelector<HTMLInputElement>("#mb-new-role")!.value),
      personality: dialog.el.querySelector<HTMLTextAreaElement>("#mb-new-personality")!.value.trim(),
      instructions: dialog.el.querySelector<HTMLTextAreaElement>("#mb-new-instr")!.value.trim(),
      group_id: group ? group.id : null,
      ...(avatar ? { avatar } : {}),
    };
    busy = true;
    dialog.el.querySelectorAll<HTMLButtonElement>(".mb-dlg-act .btn").forEach((button) => (button.disabled = true));
    let made = false;
    try {
      made = await o.create({ member, picture });
    } catch (err) {
      toaster.info(describeError(err));
    }
    busy = false;
    if (made) dialog.close();
    else {
      dialog.el.querySelectorAll<HTMLButtonElement>(".mb-dlg-act .btn").forEach((button) => (button.disabled = false));
      if (!dialog.el.contains(document.activeElement)) nameInput.focus();
    }
  }

  nameInput.addEventListener("input", () => {
    const letter = face.querySelector(".mb-av-letter");
    if (letter) letter.textContent = initialOf(nameInput.value);
  });
  draw();
  return dialog;
}
