// Dictionary: words Raphael should spell right, with the spellings the speech recognition tends to
// give for them (a fixed replacement). A filter (all, learned by itself, typed), a search, the
// words as a grid of chips (learned ones with a small sparkle), 「新增單字」 for a word of one's
// own, and the suggestions found in the latest dictations (spec §12.3).

import { onLocaleChange, t } from "../i18n";

import * as api from "../api";
import { describeError, parseMisheard } from "../format";
import { LearnHints, editorName } from "../learn-hint";
import { loadingHTML } from "../loading";
import type { AppContext, DictionaryEntry, Page } from "../types";
import { dismissMessage, suggestionsHTML, visibleSuggestions } from "../suggest";
import { Deletions } from "../undo";
import { btn, esc, fieldHTML, ico, iconBtn, st, toaster } from "../ui";

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

export function createDictionary(app: AppContext): Page {
  const el = document.createElement("div");
  el.className = "page page-dict";
  el.dataset.page = "dictionary";
  el.hidden = true;
  el.innerHTML =
    t("dictionary.008", { p0: btn(t("dictionary.007", { p0: ico("plus") }), "pri", ' data-act="new" aria-expanded="false"') }) +
    t("dictionary.009") +
    `<div class="addword card" hidden>${fieldHTML(t("dictionary.010"), "", { id: "nw-word", get label() { return t("dictionary.011"); } })}` +
    `${fieldHTML(t("dictionary.012"), "", { id: "nw-mis", get label() { return t("dictionary.013"); } })}` +
    `<div class="addword-actions">${btn(t("dictionary.014"), "pri", ' data-act="add"')}${btn(t("onboarding.031"), "", ' data-act="new-cancel"')}</div></div>` +
    t("dictionary.015") +
    FILTERS.map((f) => `<button type="button" data-filter="${f.id}" aria-pressed="${f.id === "all"}">${f.name}</button>`).join("") +
    `</div><span class="count num"></span>` +
    `<div class="dsearch">${fieldHTML(t("dictionary.016"), "", { icon: "search", get label() { return t("dictionary.016"); }, id: "dict-q" })}</div></div>` +
    `<div class="learn-hint" hidden></div>` +
    `<div class="sugg" hidden></div>` +
    `<div class="wgrid"></div><div class="empty" hidden></div>`;
  const wordInput = el.querySelector<HTMLInputElement>("#nw-word")!;
  const misheardInput = el.querySelector<HTMLInputElement>("#nw-mis")!;
  const addBox = el.querySelector<HTMLElement>(".addword")!;
  const newButton = el.querySelector<HTMLElement>('[data-act="new"]')!;
  const searchInput = el.querySelector<HTMLInputElement>("#dict-q")!;
  const suggestions = el.querySelector<HTMLElement>(".sugg")!;
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
  let suggested: string[] = [];
  let filter: Filter = "all";
  let query = "";
  /** Deleted, but not yet for the core (the toast is still there): left out of the list. */
  const deleting = new Set<number>();
  /** Deleted for good: a load that started before the delete must not bring the word back. */
  const deletions = new Deletions();
  /** Suggestions dismissed, but not yet for the core (the toast is still there): left out. */
  const dismissing = new Set<string>();
  let loads = 0;
  let pending = false;

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
    const words = visibleSuggestions(suggested, dismissing);
    suggestions.hidden = words.length === 0;
    suggestions.innerHTML = suggestionsHTML(words);
  }

  async function load(): Promise<void> {
    const mine = ++loads;
    pending = true;
    render();
    const read = deletions.startRead();
    const [listed, found] = await Promise.allSettled([api.dictionaryList(), api.dictionarySuggestions()]);
    if (mine !== loads) return;
    if (listed.status === "fulfilled") entries = deletions.filter(read, listed.value.entries);
    else toaster.info(t("dictionary.020", { p0: () => describeError(listed.reason) }));
    suggested = found.status === "fulfilled" ? found.value.words.map((w) => w.word) : [];
    pending = false;
    render();
  }

  async function add(word: string, misheard: string[]): Promise<boolean> {
    try {
      await api.dictionaryAdd(word, misheard);
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
    if (await add(word, parseMisheard(misheardInput.value))) {
      wordInput.value = "";
      misheardInput.value = "";
      wordInput.focus();
    }
  }

  function showAdd(open: boolean): void {
    addBox.hidden = !open;
    newButton.setAttribute("aria-expanded", String(open));
    if (open) wordInput.focus();
    else newButton.focus();
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

  function dismiss(word: string): void {
    dismissing.add(word);
    render();
    app.undo.begin({
      message: dismissMessage(word),
      commit: async () => {
        await api.dictionaryDismiss(word);
        suggested = suggested.filter((w) => w !== word);
        dismissing.delete(word);
      },
      restore: () => {
        dismissing.delete(word);
        render();
      },
      failure: (err) => t("dictionary.025", { p0: () => describeError(err) }),
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
    else if (act.dataset.act === "hint-dismiss") hints.dismiss(act.dataset.editor ?? "");
    else if (act.dataset.act === "suggest") void add(act.dataset.word ?? "", []);
    else if (act.dataset.act === "dismiss" && act.dataset.word) dismiss(act.dataset.word);
    else if (act.dataset.act === "delete") remove(Number(act.closest<HTMLElement>(".wrow")?.dataset.id));
  });
  el.addEventListener("keydown", (e) => {
    const target = e.target as Element;
    if (e.key === "Enter" && (target === wordInput || target === misheardInput)) {
      e.preventDefault();
      void addFromFields();
    } else if (e.key === "Escape" && addBox.contains(target)) {
      showAdd(false);
    }
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
    misheardInput.placeholder = t("dictionary.012");
    misheardInput.setAttribute("aria-label", t("dictionary.013"));
    searchInput.placeholder = t("dictionary.016");
    searchInput.setAttribute("aria-label", t("dictionary.016"));
    addBox.querySelector('[data-act="add"]')!.textContent = t("dictionary.014");
    addBox.querySelector('[data-act="new-cancel"]')!.textContent = t("onboarding.031");
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
  };
}
