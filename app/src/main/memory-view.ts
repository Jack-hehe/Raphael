// Raphael's memory in Settings shares the standard rows. A collapsed section shows its note
// count; expanded notes show their type and description. Each note's overflow menu offers
// editing, deletion with the existing ten-second Undo period, and an optional space move.
// Summary and clear-all actions retain their second-press confirmation.
//
// `memoryHTML` draws it from a snapshot (pure, so its rules can be tested without a browser);
// `createMemory` holds the state and does what the buttons say, through the core's `memory.*`
// methods. A note's text is shown only in its box, and never logged.
//
// The memory is in two spaces: 「共同記憶」, what every agent knows of the owner (the same list in
// each agent's panel), and 「<name> 的記憶」, the agent's own. A list made for a space (`space`) is
// one section titled so, sends `space` (and the agent's `member_id` for its own) with every call,
// and gives each note a borderless 「more」 whose menu moves it to the other space (`memory.move`).
// Raphael's tab of Settings and every agent's 記憶 tab show the two sections, shared first.

import { core } from "./api";
import { describeError } from "./format";
import { t } from "./i18n";
import type { MessageKey } from "./i18n/zh-TW";
import { DISCONNECT_CONFIRM_MS, actionRow, btn, closeMenu, esc, iconButton, note, openPopover, row, st, toaster } from "./ui";
import type { Undoable } from "./undo";

/** A note as `memory.list` gives it. */
export interface MemoryItem {
  name: string;
  description: string;
  type: string;
  origin: string;
  updated: string;
  expires: string | null;
}

export interface MemoryList {
  memories: MemoryItem[];
  summary: { text: string; updated_at_ms: number } | null;
}

/** The note that is open: its text and its title (its line in the index Raphael always reads), as
 * they were read, then as they are being typed. `body` is null while the note is read. */
export interface MemoryEditor {
  name: string;
  body: string | null;
  draft: string;
  description: string;
  descDraft: string;
  saving: boolean;
  error: string | null;
}

/** Where a note is kept: the memory every agent shares, or the agent's own. */
export type MemorySpace = "shared" | "own";

export interface MemoryView {
  status: MemoryList | "loading" | "failed";
  /** The space the list is (none: the one memory of before the spaces, titled 「記憶」). */
  space?: MemorySpace;
  /** The section's title: 「共同記憶」 or 「<name> 的記憶」. */
  title?: string;
  /** What the menu of a note says to move it to the other space. */
  moveTo?: string;
  expanded: boolean;
  editing: MemoryEditor | null;
  /** Deleted, and waiting out their 10 s of 「復原」: not drawn. */
  hidden: readonly string[];
  confirmingClear: boolean;
  confirmingSummary: boolean;
  busy: boolean;
}

export const emptyMemoryView = (): MemoryView => ({
  status: "loading", expanded: false, editing: null, hidden: [], confirmingClear: false, confirmingSummary: false, busy: false,
});

/** The most a note's text holds (the core's limit). */
export const MEMORY_BODY_MAX = 1200;
/** The most a note's title holds (the core's limit, and the shell's). */
export const MEMORY_DESCRIPTION_MAX = 80;

/** The word for each type of note, as the catalogue spells it. */
const TYPE_KEYS: Record<string, MessageKey> = {
  user: "mem.type.user",
  preference: "mem.type.preference",
  person: "mem.type.person",
  ongoing: "mem.type.ongoing",
  reference: "mem.type.reference",
  project: "mem.type.project",
};
const tr = t as unknown as (key: MessageKey) => string;
export const typeLabel = (type: string): string => (TYPE_KEYS[type] ? tr(TYPE_KEYS[type]) : type);

const shown = (v: MemoryView, list: MemoryList): MemoryItem[] => list.memories.filter((m) => !v.hidden.includes(m.name));
/** The id of a field of the open note: one per space, as two lists may each have a note open. */
export const memoryFieldId = (base: "mem-body" | "mem-title", space: MemorySpace | undefined): string => (space ? `${base}-${space}` : base);

function editorHTML(v: MemoryView, item: MemoryItem): string {
  const e = v.editing!;
  if (e.body === null) return `<div class="mem-edit" data-mem-editor><span class="spin" role="status" aria-label="${esc(t("common.loading"))}"></span></div>`;
  const off = e.saving ? " disabled" : "";
  return (
    `<div class="mem-edit" data-mem-editor>` +
    `<input id="${memoryFieldId("mem-title", v.space)}" class="mem-title" type="text" maxlength="${MEMORY_DESCRIPTION_MAX}" aria-label="${esc(t("mem.descLabel", { p0: item.description }))}" value="${esc(e.descDraft)}"${off}>` +
    `<textarea id="${memoryFieldId("mem-body", v.space)}" class="mem-body" rows="5" maxlength="${MEMORY_BODY_MAX}" aria-label="${esc(t("mem.body", { p0: item.description }))}"${off}>${esc(e.draft)}</textarea>` +
    (e.error ? note(st("err", esc(e.error)), ' role="alert"') : "") +
    actionRow({ cls: "mem-edit-actions", sticky: true, secondary: btn(esc(t("common.cancel")), "sm", ` data-mem="cancel"${off}`), primary: { label: esc(t("mem.save")), kind: "sm", attrs: ` data-mem="save"${off}` } }) +
    `</div>`
  );
}

function itemRows(v: MemoryView, list: MemoryList): string[] {
  const off = v.busy ? " disabled" : "";
  return shown(v, list).flatMap((m) => {
    const open = v.editing?.name === m.name;
    const inferred = m.origin === "inferred" ? `<span class="mem-tag">${esc(t("mem.inferred"))}</span>` : "";
    const line = row(
      `<span class="mem-line"><span class="mem-type">${esc(typeLabel(m.type))}</span><span class="mem-desc">${esc(m.description)}</span>${inferred}</span>`,
      "",
      iconButton("more", t("mem.more", { p0: m.description }), ` data-mem="more" data-name="${esc(m.name)}" aria-haspopup="menu" aria-expanded="false"${off}`),
      { cls: "mem-sub", attrs: ` data-mem-item="${esc(m.name)}"` },
    );
    return open ? [line, editorHTML(v, m)] : [line];
  });
}

/** The memory: its row, and when it is open a row for each note, the summary and 「全部清除」. */
export function memoryHTML(v: MemoryView): string {
  const title = v.title ?? t("mem.title");
  const space = v.space ? ` data-mem-space="${v.space}"` : "";
  const group = (rows: string[], after = ""): string =>
    `<section class="sgroup mem" data-mem-section${space} aria-label="${esc(title)}"><div class="slist">${rows.join("")}</div>${after}</section>`;
  if (v.status === "loading") {
    return group([row(esc(title), "", `<span class="spin" role="status" aria-label="${esc(t("common.loading"))}"></span>`, { cls: "mem-head" })]);
  }
  if (v.status === "failed") return group([row(esc(title), "", "", { cls: "mem-head" }), note(st("err", t("mem.loadFailed")), ' role="alert"')]);
  const list = v.status;
  const notes = shown(v, list);
  const anything = notes.length > 0 || list.summary !== null;
  const count = notes.length
    ? `<span class="val num">${notes.length}</span>`
    : `<span class="val">${esc(t("mem.empty"))}</span>`;
  const chevron = anything ? iconButton("down", t("mem.details"), ` data-mem="expand" aria-expanded="${v.expanded}"`, "mem-more") : "";
  const head = row(esc(title), "", count + chevron, { cls: "mem-head" });
  if (!v.expanded || !anything) return group([head]);
  const off = v.busy ? " disabled" : "";
  const summary = list.summary
    ? [row(
        esc(t("mem.summary")),
        "",
        actionRow({ destructive: { label: esc(v.confirmingSummary ? t("mem.summaryConfirm") : t("mem.summaryClear")), kind: "sm", attrs: ` data-mem="summary"${off}` } }),
        { cls: "mem-sub mem-summary" },
      )]
    : [];
  const clear = notes.length
    ? actionRow({ cls: "mem-foot", destructive: { label: esc(v.confirmingClear ? t("mem.clearConfirm") : t("mem.clearAll")), kind: "sm", attrs: ` data-mem="clear"${off}` } })
    : "";
  return group([head, ...itemRows(v, list), ...summary], clear);
}

// ---------- state ----------

export interface MemoryHost {
  visible(): boolean;
  redraw(): void;
  undo: Pick<Undoable, "begin">;
  /**
   * Whose memory: a member's id (its own folder, `memory/members/<id>/`), sent to the core as
   * `member_id` on every call; 0, or none, is Raphael's, and nothing is sent.
   */
  memberId?: () => number;
  /** The list of notes is open to begin with, and again after `reset` (the 記憶 tab of a member's panel). */
  expanded?: boolean;
  /** The space this list is: its calls say it, and its notes can be moved to the other one. */
  space?: MemorySpace;
  /** The agent's name, for 「<name> 的記憶」 and 「移到 <name> 的記憶」. */
  ownerName?: () => string;
  /** A note was moved to the other space: the list of that space reads it again. */
  onMoved?: () => void;
}

export interface MemoryPanel {
  view(): MemoryView;
  load(): Promise<void>;
  click(target: Element): boolean;
  input(el: HTMLTextAreaElement | HTMLInputElement): boolean;
  key(e: KeyboardEvent): boolean;
  /** The panel is closed or leaves the tab: close the note that is open and forget the presses. */
  reset(): void;
}

interface MemoryNote extends MemoryItem {
  body: string;
}

export function createMemory(host: MemoryHost): MemoryPanel {
  let status: MemoryView["status"] = "loading";
  let expanded = host.expanded === true;
  const space = host.space;
  /**
   * Calls the core's `method`: in a space, with the space and, for an agent's own, its `member_id`
   * (Raphael's is 0); without one, as before the spaces, with the member's id only for a member.
   */
  const call = <T>(method: string, params?: Record<string, unknown>): Promise<T> => {
    const id = host.memberId?.() ?? 0;
    if (space === "shared") return core<T>(method, { ...params, space });
    if (space === "own") return core<T>(method, { ...params, space, member_id: id });
    if (id) return core<T>(method, { ...params, member_id: id });
    return params === undefined ? core<T>(method) : core<T>(method, params);
  };
  const ownTitle = (): string => t("mem.own", { p0: host.ownerName?.() ?? t("agent.title") });
  let editing: MemoryEditor | null = null;
  let hidden: string[] = [];
  let confirmingClear = false;
  let confirmingSummary = false;
  let busy = false;
  let loads = 0;
  const timers: { clear?: ReturnType<typeof setTimeout>; summary?: ReturnType<typeof setTimeout> } = {};

  const redraw = (): void => {
    if (host.visible()) host.redraw();
  };
  const view = (): MemoryView => ({
    status, expanded, editing, hidden, confirmingClear, confirmingSummary, busy,
    ...(space
      ? {
          space,
          title: space === "shared" ? t("mem.shared") : ownTitle(),
          moveTo: space === "shared" ? t("mem.moveToOwn", { p0: host.ownerName?.() ?? t("agent.title") }) : t("mem.moveToShared"),
        }
      : {}),
  });
  const bodyId = memoryFieldId("mem-body", space);
  const titleId = memoryFieldId("mem-title", space);

  async function load(): Promise<void> {
    const mine = ++loads;
    try {
      const next = await call<MemoryList>("memory.list");
      if (mine !== loads) return;
      status = next;
      if (editing && !next.memories.some((m) => m.name === editing!.name)) editing = null;
    } catch {
      if (mine !== loads) return;
      if (typeof status !== "object") status = "failed";
    }
    redraw();
  }

  async function open(name: string): Promise<void> {
    if (editing?.name === name) {
      editing = null;
      redraw();
      return;
    }
    editing = { name, body: null, draft: "", description: "", descDraft: "", saving: false, error: null };
    redraw();
    try {
      const memory = await call<MemoryNote>("memory.get", { name });
      if (editing?.name !== name) return;
      editing = { ...editing, body: memory.body, draft: memory.body, description: memory.description, descDraft: memory.description };
    } catch (err) {
      if (editing?.name !== name) return;
      editing = null;
      toaster.info(describeError(err));
    }
    redraw();
    // The box takes the focus, so that the text can be read and changed at once.
    if (editing?.name === name && typeof document !== "undefined") document.getElementById(bodyId)?.focus();
  }

  async function save(): Promise<void> {
    const e = editing;
    if (!e || e.body === null || e.saving) return;
    // The title is one line, as the index shows it; the core keeps it at one line too.
    const description = e.descDraft.split(/\s+/).filter(Boolean).join(" ");
    if (!description) {
      editing = { ...e, error: t("mem.descEmpty") };
      redraw();
      return;
    }
    const titled = description !== e.description;
    if (e.draft === e.body && !titled) {
      editing = null;
      redraw();
      return;
    }
    editing = { ...e, saving: true, error: null };
    redraw();
    try {
      // The text always goes (the shell asks for it); the title only when it changed.
      await call<MemoryNote>("memory.update", titled ? { name: e.name, body: e.draft, description } : { name: e.name, body: e.draft });
      editing = null;
      toaster.info(t("mem.saved"));
      await load();
    } catch (err) {
      if (editing?.name === e.name) editing = { ...editing, saving: false, error: describeError(err) };
      redraw();
    }
  }

  function remove(name: string): void {
    if (typeof status !== "object") return;
    if (editing?.name === name) editing = null;
    hidden = [...hidden, name];
    redraw();
    host.undo.begin({
      message: t("mem.deleted"),
      commit: async () => {
        await call("memory.delete", { name });
        hidden = hidden.filter((n) => n !== name);
        if (typeof status === "object") status = { ...status, memories: status.memories.filter((m) => m.name !== name) };
        redraw();
      },
      restore: () => {
        hidden = hidden.filter((n) => n !== name);
        redraw();
      },
    });
  }

  /** The first press of a button that asks for a second: it waits that long, then forgets. */
  function arm(which: "clear" | "summary"): void {
    clearTimeout(timers[which]);
    if (which === "clear") confirmingClear = true;
    else confirmingSummary = true;
    timers[which] = setTimeout(() => {
      if (which === "clear") confirmingClear = false;
      else confirmingSummary = false;
      redraw();
    }, DISCONNECT_CONFIRM_MS);
    redraw();
  }

  async function act(work: () => Promise<unknown>, done: MessageKey): Promise<void> {
    busy = true;
    redraw();
    try {
      await work();
      toaster.info(tr(done));
    } catch (err) {
      toaster.info(describeError(err));
    }
    busy = false;
    await load();
  }

  /** Moves the note `name` to the other space (`memory.move`); both lists read again. */
  async function move(name: string): Promise<void> {
    if (!space) return;
    if (editing?.name === name) editing = null;
    busy = true;
    redraw();
    try {
      // the note by its name; `member_id` is whose own memory is the one side of the move
      await core("memory.move", { name, to_space: space === "shared" ? "own" : "shared", member_id: host.memberId?.() ?? 0 });
      toaster.info(t("mem.moved"));
    } catch (err) {
      toaster.info(describeError(err));
    }
    busy = false;
    await load();
    host.onMoved?.();
  }

  /** Keep editing, deletion and optional space moves in the note's menu. */
  function moreMenu(anchor: HTMLElement, name: string): void {
    const label = view().moveTo ?? "";
    const description = status !== "loading" && status !== "failed" ? status.memories.find(note => note.name === name)?.description ?? name : name;
    const el = openPopover(
      anchor,
      `<button type="button" role="menuitem" tabindex="-1" data-mem-edit><span class="mt">${esc(t("mem.edit", { p0: description }))}</span></button>` +
      (label ? `<button type="button" role="menuitem" tabindex="-1" data-mem-move><span class="mt">${esc(label)}</span></button>` : "") +
      `<button type="button" role="menuitem" class="danger" tabindex="-1" data-mem-delete><span class="mt">${esc(t("mem.delete", { p0: description }))}</span></button>`,
      { className: "menu", role: "menu", label: anchor.getAttribute("aria-label") ?? undefined },
    );
    el.addEventListener("click", (e) => {
      const target = (e.target as Element).closest('[data-mem-edit], [data-mem-move], [data-mem-delete]');
      if (!target) return;
      closeMenu();
      if (target.hasAttribute('data-mem-edit')) void open(name);
      else if (target.hasAttribute('data-mem-delete')) remove(name);
      else void move(name);
    });
    el.querySelector<HTMLElement>("button")?.focus();
  }

  function click(target: Element): boolean {
    const button = target.closest<HTMLElement>("[data-mem]");
    if (!button) return false;
    // two lists on one page: each hears the clicks of its own section only
    if (space && button.closest<HTMLElement>("[data-mem-section]")?.dataset.memSpace !== space) return false;
    const name = button.dataset.name ?? "";
    switch (button.dataset.mem) {
      case "expand":
        expanded = !expanded;
        if (!expanded) editing = null;
        redraw();
        return true;
      case "edit":
        void open(name);
        return true;
      case "cancel":
        editing = null;
        redraw();
        return true;
      case "save":
        void save();
        return true;
      case "delete":
        remove(name);
        return true;
      case "more":
        moreMenu(button, name);
        return true;
      case "clear":
        if (!confirmingClear) {
          arm("clear");
          return true;
        }
        clearTimeout(timers.clear);
        confirmingClear = false;
        editing = null;
        void act(() => call("memory.clear"), "mem.cleared");
        return true;
      case "summary":
        if (!confirmingSummary) {
          arm("summary");
          return true;
        }
        clearTimeout(timers.summary);
        confirmingSummary = false;
        void act(() => call("memory.summary_clear"), "mem.summaryCleared");
        return true;
    }
    return false;
  }

  function input(el: HTMLTextAreaElement | HTMLInputElement): boolean {
    if ((el.id !== bodyId && el.id !== titleId) || !editing) return false;
    // Kept as it is typed, not drawn again: a redraw would take the caret out of the box.
    editing = el.id === bodyId ? { ...editing, draft: el.value } : { ...editing, descDraft: el.value };
    if (editing.error) {
      editing = { ...editing, error: null };
      el.closest("[data-mem-editor]")?.querySelectorAll(".note[role=alert]").forEach((n) => n.remove());
    }
    return true;
  }

  function key(e: KeyboardEvent): boolean {
    const target = e.target as HTMLElement | null;
    const field = target?.id;
    if ((field !== bodyId && field !== titleId) || !editing) return false;
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      editing = null;
      redraw();
      return true;
    }
    // Enter saves from the title, which is one line; the text takes Enter as a new line.
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey || (field === titleId && !e.isComposing))) {
      e.preventDefault();
      void save();
      return true;
    }
    return false;
  }

  function reset(): void {
    loads++; // A pending list belongs to the member or tab that was left.
    status = "loading";
    editing = null;
    expanded = host.expanded === true;
    confirmingClear = false;
    confirmingSummary = false;
    clearTimeout(timers.clear);
    clearTimeout(timers.summary);
  }

  return { view, load, click, input, key, reset };
}
