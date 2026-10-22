// Dictionary: words Raphael should spell right. A filter (all, learned by itself, typed), a search,
// the words as a grid of chips (learned ones with a small sparkle), 「新增單字」 for a word of one's
// own and 「匯入」 for many from a .csv or .txt, after a confirmation; words that keep coming up in
// dictation join by themselves.

import { onLocaleChange, t } from "../i18n";

import * as api from "../api";
import { ImportError, countKnown, decodeWordFile, parseWordFile } from "../dictionary-import";
import { describeError } from "../format";
import { LearnHints, editorName } from "../learn-hint";
import { loadingHTML } from "../loading";
import type { AppContext, DictionaryEntry, Page } from "../types";
import { Deletions } from "../undo";
import { btn, esc, fieldHTML, ico, iconBtn, st, tabs, toaster } from "../ui";

type Filter = "all" | "auto" | "manual";
const FILTERS: { id: Filter; name: string }[] = [
  { id: "all", get name() { return t("dictionary.001"); } },
  { id: "auto", get name() { return t("dictionary.002"); } },
  { id: "manual", get name() { return t("dictionary.003"); } },
];

/** The core uses "learned" for corrections; "auto" is also accepted. Other sources count as manual. */
export const isAuto = (entry: DictionaryEntry): boolean => entry.source === "learned" || entry.source === "auto";

export function matches(entry: DictionaryEntry, filter: Filter, query: string): boolean {
  if (filter === "auto" && !isAuto(entry)) return false;
  if (filter === "manual" && isAuto(entry)) return false;
  if (!query) return true;
  const q = query.toLowerCase();
  return entry.word.toLowerCase().includes(q) || entry.misheard.some((m) => m.toLowerCase().includes(q));
}

const wordChip = (entry: DictionaryEntry): string =>
  `<div class="wchip wrow${isAuto(entry) ? " auto" : ""}" data-id="${entry.id}">` +
  (isAuto(entry) ? t("dictionary.004", { p0: ico("sparkle") }) : "") +
  `<div class="wtext"><span class="word">${esc(entry.word)}</span>` +
  (entry.misheard.length > 0 ? t("dictionary.005", { p0: esc(entry.misheard.join("、")) }) : "") +
  `</div><span class="acts">${iconBtn("trash", t("dictionary.006", { p0: entry.word }), ' data-act="delete"')}</span></div>`;

/** The dictionary, and the way in that the tray's 「將單字加入字典」 takes. */
export interface DictionaryPage extends Page {
  /** Opens the fields for a new word and puts the cursor in the word. */
  addWord(): void;
}

export function createDictionary(app: AppContext): DictionaryPage {
  const el = document.createElement("div");
  el.className = "page page-dict";
  el.dataset.page = "dictionary";
  el.hidden = true;
  el.innerHTML =
    t("dictionary.008", {
      p0:
        `<div class="head-actions">${btn(t("dictionary.025"), "", ' data-act="import"')}` +
        `${btn(t("dictionary.007", { p0: ico("plus") }), "pri", ' data-act="new" aria-expanded="false"')}</div>`,
    }) +
    t("dictionary.009") +
    `<div class="addword card" hidden>${fieldHTML(t("dictionary.010"), "", { id: "nw-word", get label() { return t("dictionary.011"); } })}` +
    `<div class="addword-actions">${btn(t("dictionary.014"), "pri", ' data-act="add"')}${btn(t("common.cancel"), "", ' data-act="new-cancel"')}</div></div>` +
    `<div class="importbox card" role="status" hidden><span class="importmsg"></span>` +
    `<div class="addword-actions">${btn(t("dictionary.025"), "pri", ' data-act="import-go"')}${btn(t("common.cancel"), "", ' data-act="import-cancel"')}</div></div>` +
    `<input type="file" accept=".csv,.txt,text/csv,text/plain" hidden data-import-file aria-label="${esc(t("dictionary.034"))}">` +
    `<div class="tools">` +
    tabs(FILTERS.map((f) => ({ id: f.id, label: f.name, attrs: ` data-filter="${f.id}"` })), "all", { label: t("common.filter"), kind: "filter" }) +
    `<span class="count num"></span>` +
    `<div class="dsearch">${fieldHTML(t("dictionary.016"), "", { icon: "search", get label() { return t("dictionary.016"); }, id: "dict-q" })}</div></div>` +
    `<div class="learn-hint" hidden></div>` +
    `<div class="wgrid"></div><div class="empty" hidden></div>`;
  const wordInput = el.querySelector<HTMLInputElement>("#nw-word")!;
  const importBox = el.querySelector<HTMLElement>(".importbox")!;
  const importMessage = el.querySelector<HTMLElement>(".importmsg")!;
  const importFile = el.querySelector<HTMLInputElement>("[data-import-file]")!;
  const addBox = el.querySelector<HTMLElement>(".addword")!;
  const newButton = el.querySelector<HTMLElement>('[data-act="new"]')!;
  const searchInput = el.querySelector<HTMLInputElement>("#dict-q")!;
  const count = el.querySelector<HTMLElement>(".count")!;
  const grid = el.querySelector<HTMLElement>(".wgrid")!;
  const empty = el.querySelector<HTMLElement>(".empty")!;
  const hintBox = el.querySelector<HTMLElement>(".learn-hint")!;

  // Editors that hide their text from learning: one hint each, until dismissed.
  const hints = new LearnHints();
  app.onEvent((event) => hints.handle(event));
  hints.onChange(renderHints);
  function renderHints(): void {
    const editors = hints.pending();
    hintBox.hidden = editors.length === 0;
    hintBox.innerHTML = editors
      .map(
        (editor) =>
          `<div class="alert" role="status">${st("warn", t("learnHint.body", { p0: editorName(editor) }))}` +
          `${btn(t("learnHint.dismiss"), "sm", ` data-act="hint-dismiss" data-editor="${esc(editor)}"`)}</div>`,
      )
      .join("");
  }

  let entries: DictionaryEntry[] = [];
  let filter: Filter = "all";
  let query = "";
  /** Deleted, but not yet for the core (the toast is still there): left out of the list. */
  const deleting = new Set<number>();
  /** Deleted for good: a load that started before the delete must not bring the word back. */
  const deletions = new Deletions();
  let loads = 0;
  let pending = false;
  /** The words of the chosen file that wait for the answer to the confirmation, and how many of them are there already. */
  let staged: { words: string[]; known: number } | null = null;
  let importing = false;

  function render(): void {
    grid.setAttribute("aria-busy", String(pending));
    if (pending && entries.length === 0) {
      grid.innerHTML = loadingHTML();
      empty.hidden = true;
      return;
    }
    const kept = entries.filter((entry) => !deleting.has(entry.id));
    const shown = kept.filter((entry) => matches(entry, filter, query));
    count.textContent = t("dictionary.017", { p0: shown.length });
    grid.innerHTML = shown.map(wordChip).join("");
    empty.hidden = shown.length > 0;
    empty.innerHTML =
      kept.length === 0
        ? t("dictionary.018")
        : t("dictionary.019");
  }

  async function load(): Promise<void> {
    const mine = ++loads;
    pending = true;
    render();
    const read = deletions.startRead();
    const [listed] = await Promise.allSettled([api.dictionaryList()]);
    if (mine !== loads) return;
    if (listed.status === "fulfilled") entries = deletions.filter(read, listed.value.entries);
    else toaster.info(t("dictionary.020", { p0: () => describeError(listed.reason) }));
    pending = false;
    render();
  }

  async function add(word: string): Promise<boolean> {
    try {
      await api.dictionaryAdd(word);
    } catch (err) {
      toaster.info(t("dictionary.021", { p0: () => describeError(err) }));
      return false;
    }
    toaster.info(t("dictionary.022", { p0: word }));
    void load();
    return true;
  }

  async function addFromFields(): Promise<void> {
    const word = wordInput.value.trim();
    if (!word) {
      wordInput.focus();
      return;
    }
    if (await add(word)) {
      wordInput.value = "";
      wordInput.focus();
    }
  }

  function showAdd(open: boolean): void {
    addBox.hidden = !open;
    newButton.setAttribute("aria-expanded", String(open));
    if (open) wordInput.focus();
    else newButton.focus();
  }

  function renderImport(): void {
    importBox.hidden = staged === null;
    if (!staged) return;
    const fresh = staged.words.length - staged.known;
    importMessage.textContent = staged.known > 0 ? t("dictionary.026", { p0: fresh, p1: staged.known }) : t("dictionary.027", { p0: fresh });
  }

  function importError(err: unknown): string {
    if (err instanceof ImportError) return t(err.kind === "big" ? "dictionary.029" : err.kind === "empty" ? "dictionary.031" : "dictionary.030");
    return t("dictionary.033", { p0: () => describeError(err) });
  }

  /** Reads the chosen file and asks before anything is added. */
  async function stageFile(file: File): Promise<void> {
    try {
      const words = parseWordFile(decodeWordFile(await file.arrayBuffer()), file.name);
      const known = countKnown(words, entries.map((entry) => entry.word));
      if (known === words.length) {
        toaster.info(t("dictionary.032"));
        return;
      }
      staged = { words, known };
    } catch (err) {
      toaster.info(err instanceof ImportError ? importError(err) : t("dictionary.030"));
      return;
    }
    renderImport();
    importBox.querySelector<HTMLElement>('[data-act="import-go"]')!.focus();
  }

  async function runImport(): Promise<void> {
    if (!staged || importing) return;
    const { words } = staged;
    importing = true;
    try {
      const { added } = await api.dictionaryImport(words);
      staged = null;
      renderImport();
      toaster.info(t("dictionary.028", { p0: added }));
      void load();
    } catch (err) {
      toaster.info(importError(err));
    } finally {
      importing = false;
    }
  }

  function remove(id: number): void {
    deleting.add(id);
    render();
    app.undo.begin({
      message: t("dictionary.023"),
      commit: async () => {
        await api.dictionaryDelete(id);
        deletions.confirm(id);
        entries = entries.filter((entry) => entry.id !== id);
        deleting.delete(id);
      },
      restore: () => {
        deleting.delete(id);
        render();
      },
      failure: (err) => t("dictionary.024", { p0: () => describeError(err) }),
    });
  }

  el.addEventListener("click", (e) => {
    const target = e.target as Element;
    const tab = target.closest<HTMLElement>("[data-filter]");
    if (tab) {
      filter = tab.dataset.filter as Filter;
      el.querySelectorAll<HTMLElement>("[data-filter]").forEach((b) => b.setAttribute("aria-pressed", String(b === tab)));
      render();
      return;
    }
    const act = target.closest<HTMLElement>("[data-act]");
    if (!act) return;
    if (act.dataset.act === "add") void addFromFields();
    else if (act.dataset.act === "new") showAdd(addBox.hidden === true);
    else if (act.dataset.act === "new-cancel") showAdd(false);
    else if (act.dataset.act === "import") importFile.click();
    else if (act.dataset.act === "import-go") void runImport();
    else if (act.dataset.act === "import-cancel") {
      staged = null;
      renderImport();
    }
    else if (act.dataset.act === "hint-dismiss") hints.dismiss(act.dataset.editor ?? "");
    else if (act.dataset.act === "delete") remove(Number(act.closest<HTMLElement>(".wrow")?.dataset.id));
  });
  el.addEventListener("keydown", (e) => {
    const target = e.target as Element;
    if (e.key === "Enter" && target === wordInput) {
      e.preventDefault();
      void addFromFields();
    } else if (e.key === "Escape" && addBox.contains(target)) {
      showAdd(false);
    } else if (e.key === "Escape" && importBox.contains(target)) {
      staged = null;
      renderImport();
    }
  });
  importFile.addEventListener("change", () => {
    const file = importFile.files?.[0];
    importFile.value = ""; // the same file can be chosen again
    if (file) void stageFile(file);
  });
  searchInput.addEventListener("input", () => {
    query = searchInput.value.trim();
    render();
  });

  onLocaleChange(() => {
    renderHints();
    el.querySelector("h1")!.textContent = t("main.003");
    newButton.innerHTML = t("dictionary.007", { p0: ico("plus") });
    el.querySelector(".lede")!.outerHTML = t("dictionary.009");
    el.querySelector(".tabs")!.setAttribute("aria-label", t("common.filter"));
    for (const f of FILTERS) el.querySelector<HTMLElement>(`[data-filter="${f.id}"]`)!.textContent = f.name;
    wordInput.placeholder = t("dictionary.010");
    wordInput.setAttribute("aria-label", t("dictionary.011"));
    importFile.setAttribute("aria-label", t("dictionary.034"));
    el.querySelector('[data-act="import"]')!.textContent = t("dictionary.025");
    importBox.querySelector('[data-act="import-go"]')!.textContent = t("dictionary.025");
    importBox.querySelector('[data-act="import-cancel"]')!.textContent = t("common.cancel");
    renderImport();
    searchInput.placeholder = t("dictionary.016");
    searchInput.setAttribute("aria-label", t("dictionary.016"));
    addBox.querySelector('[data-act="add"]')!.textContent = t("dictionary.014");
    addBox.querySelector('[data-act="new-cancel"]')!.textContent = t("common.cancel");
    render();
  });

  return {
    id: "dictionary",
    el,
    show() {
      void load();
    },
    hide() {
      loads++;
    },
    addWord() {
      showAdd(true);
    },
  };
}
