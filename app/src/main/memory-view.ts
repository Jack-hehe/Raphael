// Raphael's memory, in the 拉斐爾 tab of Settings: what he keeps about the person, one note a line.
// It is drawn with the rows of every other tab. Closed, it is one row: 「記憶」 and how many notes
// there are, with a chevron. Open, a row for each note (its type and its one line), each with a pencil
// that opens its title (that one line, which the index Raphael always reads is made of) on a line
// and its text in a box, to read and change, and a trash can that deletes it after the 10 s of
// 「復原」 that every delete has; then the summary of the conversation, if there is one, with
// 「清除」; and under the card 「全部清除」, which asks for a second press like 中斷連線.
//
// `memoryHTML` draws it from a snapshot (pure, so its rules can be tested without a browser);
// `createMemory` holds the state and does what the buttons say, through the core's `memory.*`
// methods. A note's text is shown only in its box, and never logged.

import { core } from "./api";
import { describeError } from "./format";
import { t } from "./i18n";
import type { MessageKey } from "./i18n/zh-TW";
import { DISCONNECT_CONFIRM_MS, btn, esc, iconButton, note, row, st, toaster } from "./ui";
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

export interface MemoryView {
  status: MemoryList | "loading" | "failed";
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
};
const tr = t as unknown as (key: MessageKey) => string;
export const typeLabel = (type: string): string => (TYPE_KEYS[type] ? tr(TYPE_KEYS[type]) : type);

const shown = (v: MemoryView, list: MemoryList): MemoryItem[] => list.memories.filter((m) => !v.hidden.includes(m.name));

function editorHTML(v: MemoryView, item: MemoryItem): string {
  const e = v.editing!;
  if (e.body === null) return `<div class="mem-edit" data-mem-editor><span class="spin" role="status" aria-label="${esc(t("common.loading"))}"></span></div>`;
  const off = e.saving ? " disabled" : "";
  return (
    `<div class="mem-edit" data-mem-editor>` +
    `<input id="mem-title" class="mem-title" type="text" maxlength="${MEMORY_DESCRIPTION_MAX}" aria-label="${esc(t("mem.descLabel", { p0: item.description }))}" value="${esc(e.descDraft)}"${off}>` +
    `<textarea id="mem-body" class="mem-body" rows="5" maxlength="${MEMORY_BODY_MAX}" aria-label="${esc(t("mem.body", { p0: item.description }))}"${off}>${esc(e.draft)}</textarea>` +
    (e.error ? note(st("err", esc(e.error)), ' role="alert"') : "") +
    `<div class="mem-edit-actions">${btn(esc(t("common.cancel")), "sm", ` data-mem="cancel"${off}`)}${btn(esc(t("mem.save")), "sm pri", ` data-mem="save"${off}`)}</div>` +
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
      iconButton("pencil", t("mem.edit", { p0: m.description }), ` data-mem="edit" data-name="${esc(m.name)}" aria-expanded="${open}"${off}`) +
        iconButton("trash", t("mem.delete", { p0: m.description }), ` data-mem="delete" data-name="${esc(m.name)}"${off}`),
      { cls: "mem-sub", attrs: ` data-mem-item="${esc(m.name)}"` },
    );
    return open ? [line, editorHTML(v, m)] : [line];
  });
}

/** The memory: its row, and when it is open a row for each note, the summary and 「全部清除」. */
export function memoryHTML(v: MemoryView): string {
  const group = (rows: string[], after = ""): string =>
    `<section class="sgroup mem" data-mem-section aria-label="${esc(t("mem.title"))}"><div class="slist">${rows.join("")}</div>${after}</section>`;
  if (v.status === "loading") {
    return group([row(esc(t("mem.title")), "", `<span class="spin" role="status" aria-label="${esc(t("common.loading"))}"></span>`, { cls: "mem-head" })]);
  }
  if (v.status === "failed") return group([row(esc(t("mem.title")), "", "", { cls: "mem-head" }), note(st("err", t("mem.loadFailed")), ' role="alert"')]);
  const list = v.status;
  const notes = shown(v, list);
  const anything = notes.length > 0 || list.summary !== null;
  const count = notes.length
    ? `<span class="val num">${notes.length}</span>`
    : `<span class="val">${esc(t("mem.empty"))}</span>`;
  const chevron = anything ? iconButton("down", t("mem.details"), ` data-mem="expand" aria-expanded="${v.expanded}"`, "mem-more") : "";
  const head = row(esc(t("mem.title")), "", count + chevron, { cls: "mem-head" });
  if (!v.expanded || !anything) return group([head]);
  const off = v.busy ? " disabled" : "";
  const summary = list.summary
    ? [row(
        esc(t("mem.summary")),
        "",
        btn(esc(v.confirmingSummary ? t("mem.summaryConfirm") : t("mem.summaryClear")), v.confirmingSummary ? "sm danger" : "sm", ` data-mem="summary"${off}`),
        { cls: "mem-sub mem-summary" },
      )]
    : [];
  const clear = notes.length
    ? `<div class="mem-foot">${btn(esc(v.confirmingClear ? t("mem.clearConfirm") : t("mem.clearAll")), v.confirmingClear ? "sm danger" : "sm", ` data-mem="clear"${off}`)}</div>`
    : "";
  return group([head, ...itemRows(v, list), ...summary], clear);
}

// ---------- state ----------

export interface MemoryHost {
  visible(): boolean;
  redraw(): void;
  undo: Pick<Undoable, "begin">;
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
  let expanded = false;
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
  const view = (): MemoryView => ({ status, expanded, editing, hidden, confirmingClear, confirmingSummary, busy });

  async function load(): Promise<void> {
    const mine = ++loads;
    try {
      const next = await core<MemoryList>("memory.list");
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
      const memory = await core<MemoryNote>("memory.get", { name });
      if (editing?.name !== name) return;
      editing = { ...editing, body: memory.body, draft: memory.body, description: memory.description, descDraft: memory.description };
    } catch (err) {
      if (editing?.name !== name) return;
      editing = null;
      toaster.info(describeError(err));
    }
    redraw();
    // The box takes the focus, so that the text can be read and changed at once.
    if (editing?.name === name && typeof document !== "undefined") document.getElementById("mem-body")?.focus();
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
      await core<MemoryNote>("memory.update", titled ? { name: e.name, body: e.draft, description } : { name: e.name, body: e.draft });
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
        await core("memory.delete", { name });
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

  function click(target: Element): boolean {
    const button = target.closest<HTMLElement>("[data-mem]");
    if (!button) return false;
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
      case "clear":
        if (!confirmingClear) {
          arm("clear");
          return true;
        }
        clearTimeout(timers.clear);
        confirmingClear = false;
        editing = null;
        void act(() => core("memory.clear"), "mem.cleared");
        return true;
      case "summary":
        if (!confirmingSummary) {
          arm("summary");
          return true;
        }
        clearTimeout(timers.summary);
        confirmingSummary = false;
        void act(() => core("memory.summary_clear"), "mem.summaryCleared");
        return true;
    }
    return false;
  }

  function input(el: HTMLTextAreaElement | HTMLInputElement): boolean {
    if ((el.id !== "mem-body" && el.id !== "mem-title") || !editing) return false;
    // Kept as it is typed, not drawn again: a redraw would take the caret out of the box.
    editing = el.id === "mem-body" ? { ...editing, draft: el.value } : { ...editing, descDraft: el.value };
    if (editing.error) {
      editing = { ...editing, error: null };
      el.closest("[data-mem-editor]")?.querySelectorAll(".note[role=alert]").forEach((n) => n.remove());
    }
    return true;
  }

  function key(e: KeyboardEvent): boolean {
    const target = e.target as HTMLElement | null;
    const field = target?.id;
    if ((field !== "mem-body" && field !== "mem-title") || !editing) return false;
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      editing = null;
      redraw();
      return true;
    }
    // Enter saves from the title, which is one line; the text takes Enter as a new line.
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey || (field === "mem-title" && !e.isComposing))) {
      e.preventDefault();
      void save();
      return true;
    }
    return false;
  }

  function reset(): void {
    editing = null;
    expanded = false;
    confirmingClear = false;
    confirmingSummary = false;
    clearTimeout(timers.clear);
    clearTimeout(timers.summary);
  }

  return { view, load, click, input, key, reset };
}
