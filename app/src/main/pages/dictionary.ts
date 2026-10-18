// Dictionary: words Raphael should spell right, with the spellings the speech recognition tends to
// give for them (a fixed replacement). A filter (all, learned by itself, typed), a search, the
// words as a grid of chips (learned ones with a small sparkle), 「新增單字」 for a word of one's
// own, and the suggestions found in the latest dictations (spec §12.3).

import * as api from "../api";
import { describeError, parseMisheard } from "../format";
import type { AppContext, DictionaryEntry, Page } from "../types";
import { dismissMessage, suggestionsHTML, visibleSuggestions } from "../suggest";
import { Deletions } from "../undo";
import { btn, esc, fieldHTML, ico, iconBtn, toaster } from "../ui";

type Filter = "all" | "auto" | "manual";
const FILTERS: { id: Filter; name: string }[] = [
  { id: "all", name: "所有" },
  { id: "auto", name: "自動新增" },
  { id: "manual", name: "手動新增" },
];

/** Learned by itself (`source: "auto"`); anything else, a missing source too, was put in by a person. */
export const isAuto = (entry: DictionaryEntry): boolean => entry.source === "auto";

export function matches(entry: DictionaryEntry, filter: Filter, query: string): boolean {
  if (filter === "auto" && !isAuto(entry)) return false;
  if (filter === "manual" && isAuto(entry)) return false;
  if (!query) return true;
  const q = query.toLowerCase();
  return entry.word.toLowerCase().includes(q) || entry.misheard.some((m) => m.toLowerCase().includes(q));
}

const wordChip = (entry: DictionaryEntry): string =>
  `<div class="wchip wrow${isAuto(entry) ? " auto" : ""}" data-id="${entry.id}">` +
  (isAuto(entry) ? `<span class="spark" title="自動新增">${ico("sparkle")}</span>` : "") +
  `<div class="wtext"><span class="word">${esc(entry.word)}</span>` +
  (entry.misheard.length > 0 ? `<span class="mis">常被聽成 ${esc(entry.misheard.join("、"))}</span>` : "") +
  `</div><span class="acts">${iconBtn("trash", `刪除「${entry.word}」`, ' data-act="delete"')}</span></div>`;

export function createDictionary(app: AppContext): Page {
  const el = document.createElement("div");
  el.className = "page page-dict";
  el.dataset.page = "dictionary";
  el.hidden = true;
  el.innerHTML =
    `<div class="page-head"><h1>字典</h1>${btn(`${ico("plus")}新增單字`, "pri", ' data-act="new" aria-expanded="false"')}</div>` +
    `<p class="lede">放常用詞、專有名詞和常被聽錯的詞。Raphael 轉文字和整理時會優先用這些寫法。</p>` +
    `<div class="addword card" hidden>${fieldHTML("新增詞，例如：轉生史萊姆", "", { id: "nw-word", label: "新增詞" })}` +
    `${fieldHTML("常被聽成（選填），例如：拉菲爾", "", { id: "nw-mis", label: "常被聽成" })}` +
    `${btn("新增", "pri", ' data-act="add"')}${btn("取消", "", ' data-act="new-cancel"')}</div>` +
    `<div class="tools"><div class="tabs" role="group" aria-label="篩選">` +
    FILTERS.map((f) => `<button type="button" data-filter="${f.id}" aria-pressed="${f.id === "all"}">${f.name}</button>`).join("") +
    `</div><span class="count num"></span>` +
    `<div class="dsearch">${fieldHTML("搜尋字典", "", { icon: "search", label: "搜尋字典", id: "dict-q" })}</div></div>` +
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

  function render(): void {
    const kept = entries.filter((entry) => !deleting.has(entry.id));
    const shown = kept.filter((entry) => matches(entry, filter, query));
    count.textContent = `${shown.length} 個詞`;
    grid.innerHTML = shown.map(wordChip).join("");
    empty.hidden = shown.length > 0;
    empty.innerHTML =
      kept.length === 0
        ? `<div class="say">《告。字典還是空的。》</div>按「新增單字」加入常用詞，或從上面的建議加入。`
        : `<div class="say">《告。沒有符合的詞。》</div>換個篩選或關鍵字試試。`;
    const words = visibleSuggestions(suggested, dismissing);
    suggestions.hidden = words.length === 0;
    suggestions.innerHTML = suggestionsHTML(words);
  }

  async function load(): Promise<void> {
    const mine = ++loads;
    const read = deletions.startRead();
    const [listed, found] = await Promise.allSettled([api.dictionaryList(), api.dictionarySuggestions()]);
    if (mine !== loads) return;
    if (listed.status === "fulfilled") entries = deletions.filter(read, listed.value.entries);
    else toaster.info(`無法讀取字典：${describeError(listed.reason)}`);
    suggested = found.status === "fulfilled" ? found.value.words.map((w) => w.word) : [];
    render();
  }

  async function add(word: string, misheard: string[]): Promise<boolean> {
    try {
      await api.dictionaryAdd(word, misheard);
    } catch (err) {
      toaster.info(`無法加入：${describeError(err)}`);
      return false;
    }
    toaster.info(`已加入「${word}」`);
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
      message: "已刪除",
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
      failure: (err) => `無法刪除：${describeError(err)}`,
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
      failure: (err) => `無法略過：${describeError(err)}`,
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
