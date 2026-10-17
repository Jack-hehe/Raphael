// Dictionary: words Raphael should spell right, with the spellings the speech recognition tends to
// give for them (a fixed replacement), suggestions found in the latest dictations, and the list
// (spec §12.3).

import * as api from "../api";
import { describeError, parseMisheard } from "../format";
import type { AppContext, DictionaryEntry, Page } from "../types";
import { btn, esc, fieldHTML, ico, iconBtn, row, toaster } from "../ui";

const wordRow = (entry: DictionaryEntry): string =>
  row(
    `<span class="word">${esc(entry.word)}</span>` +
      (entry.misheard.length > 0 ? `<span class="mis">常被聽成 ${esc(entry.misheard.join("、"))}</span>` : ""),
    "",
    `<span class="acts">${iconBtn("trash", "刪除", ' data-act="delete"')}</span>`,
    { cls: "wrow", attrs: ` data-id="${entry.id}"` },
  );

export function createDictionary(app: AppContext): Page {
  const el = document.createElement("div");
  el.className = "page";
  el.dataset.page = "dictionary";
  el.hidden = true;
  el.innerHTML =
    `<h1>字典</h1><p class="lede">放常用詞、專有名詞和常被聽錯的詞。拉斐爾轉文字和整理時會優先用這些寫法。</p>` +
    `<div class="addword">${fieldHTML("新增詞，例如：拉斐爾", "", { id: "nw-word", label: "新增詞" })}` +
    `${fieldHTML("常被聽成（選填），例如：拉菲爾", "", { id: "nw-mis", label: "常被聽成" })}` +
    `${btn("新增", "pri", ' data-act="add"')}</div>` +
    `<div class="sugg" hidden></div>` +
    // not `group()`: the title is a count that changes
    `<section class="group"><h3 class="count"></h3><div class="list"></div></section>`;
  const wordInput = el.querySelector<HTMLInputElement>("#nw-word")!;
  const misheardInput = el.querySelector<HTMLInputElement>("#nw-mis")!;
  const suggestions = el.querySelector<HTMLElement>(".sugg")!;
  const count = el.querySelector<HTMLElement>(".count")!;
  const list = el.querySelector<HTMLElement>(".list")!;

  let entries: DictionaryEntry[] = [];
  let suggested: string[] = [];
  /** Deleted, but not yet for the core (the toast is still there): left out of the list. */
  const deleting = new Set<number>();
  let loads = 0;

  function render(): void {
    const shown = entries.filter((entry) => !deleting.has(entry.id));
    count.textContent = `${shown.length} 個詞`;
    list.innerHTML = shown.map(wordRow).join("");
    suggestions.hidden = suggested.length === 0;
    suggestions.innerHTML =
      `<span>從最近的聽寫找到：</span>` +
      suggested
        .map((word) => `<button type="button" class="chip" data-act="suggest" data-word="${esc(word)}">${ico("plus")}${esc(word)}</button>`)
        .join("");
  }

  async function load(): Promise<void> {
    const mine = ++loads;
    const [listed, found] = await Promise.allSettled([api.dictionaryList(), api.dictionarySuggestions()]);
    if (mine !== loads) return;
    if (listed.status === "fulfilled") entries = listed.value.entries;
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

  function remove(id: number): void {
    deleting.add(id);
    render();
    app.undo.begin({
      message: "已刪除",
      commit: async () => {
        await api.dictionaryDelete(id);
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

  el.addEventListener("click", (e) => {
    const act = (e.target as Element).closest<HTMLElement>("[data-act]");
    if (!act) return;
    if (act.dataset.act === "add") void addFromFields();
    else if (act.dataset.act === "suggest") void add(act.dataset.word ?? "", []);
    else if (act.dataset.act === "delete") remove(Number(act.closest<HTMLElement>(".wrow")?.dataset.id));
  });
  el.addEventListener("keydown", (e) => {
    const target = e.target as Element;
    if (e.key === "Enter" && (target === wordInput || target === misheardInput)) {
      e.preventDefault();
      void addFromFields();
    }
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
