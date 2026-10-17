// History: search, grouped by day, each row the cleaned text with its time and target program.
// Hover for copy and delete; a click shows what the speech recognition heard (spec §12.3).

import * as api from "../api";
import { dayLabel, describeError, formatClock, formatRetention, KEEP_FOREVER_DAYS } from "../format";
import { bindingLabels } from "../keys";
import type { AppContext, HistoryItem, Page } from "../types";
import { debounce } from "../undo";
import { btn, copyText, esc, fieldHTML, iconBtn, toaster } from "../ui";

/** The search waits this long after the last key. */
const SEARCH_DELAY_MS = 200;
/** How many items one load brings. */
const PAGE_SIZE = 50;

/** `notepad.exe` is shown as `notepad`. */
const programName = (app: string): string => app.replace(/\.exe$/i, "");

function itemHTML(item: HistoryItem, expanded: boolean): string {
  const meta = [formatClock(item.created_at_ms), item.target_app ? programName(item.target_app) : ""]
    .filter(Boolean)
    .map(esc)
    .join("　");
  return (
    `<div class="row hrow" data-id="${item.id}" tabindex="0" aria-expanded="${expanded}">` +
    `<div class="lbl"><div class="txt">${esc(item.text)}</div><div class="meta num">${meta}</div></div>` +
    `<div class="ctl acts">${iconBtn("copy", "複製", ' data-act="copy"')}${iconBtn("trash", "刪除", ' data-act="delete"')}</div></div>` +
    `<div class="hdetail"${expanded ? "" : " hidden"}><h4>原始辨識</h4><div class="raw">${esc(item.raw_text)}</div></div>`
  );
}

export function createHistory(app: AppContext): Page {
  const el = document.createElement("div");
  el.className = "page";
  el.dataset.page = "history";
  el.hidden = true;
  el.innerHTML =
    `<h1>歷史紀錄</h1>` +
    `<div class="tools">${fieldHTML("搜尋", "", { icon: "search", label: "搜尋歷史紀錄", id: "hist-q" })}</div>` +
    `<div class="days"></div>` +
    `<div class="more" hidden>${btn("顯示更多", "", ' data-act="more"')}</div>` +
    `<div class="empty" hidden></div>` +
    `<p class="foot"></p>`;
  const search = el.querySelector<HTMLInputElement>("#hist-q")!;
  const days = el.querySelector<HTMLElement>(".days")!;
  const more = el.querySelector<HTMLElement>(".more")!;
  const empty = el.querySelector<HTMLElement>(".empty")!;
  const foot = el.querySelector<HTMLElement>(".foot")!;

  /** What is loaded, newest first. */
  let items: HistoryItem[] = [];
  /** Deleted, but not yet for the core (the toast is still there): left out of the list. */
  const deleting = new Set<number>();
  const expanded = new Set<number>();
  let query = "";
  let canLoadMore = false;
  let failure: string | null = null;
  let loads = 0;
  let retentionDays: number | null = null;

  function render(): void {
    const shown = items.filter((item) => !deleting.has(item.id));
    const now = Date.now();
    const groups: { label: string; items: HistoryItem[] }[] = [];
    for (const item of shown) {
      const label = dayLabel(item.created_at_ms, now);
      const last = groups[groups.length - 1];
      if (last?.label === label) last.items.push(item);
      else groups.push({ label, items: [item] });
    }
    days.innerHTML = groups
      .map(
        (g) =>
          `<section class="group"><h3>${esc(g.label)}</h3><div class="list">` +
          g.items.map((item) => itemHTML(item, expanded.has(item.id))).join("") +
          `</div></section>`,
      )
      .join("");

    more.hidden = !canLoadMore || shown.length === 0;
    empty.hidden = shown.length > 0;
    if (shown.length === 0) {
      if (failure) {
        empty.innerHTML = `<div class="say">《告。無法讀取歷史紀錄。》</div>${esc(failure)}`;
      } else if (query) {
        empty.innerHTML = `<div class="say">《告。沒有符合的紀錄。》</div>換個關鍵字試試。`;
      } else {
        const dictate = app.device().dictation_keys[0];
        const key = dictate ? bindingLabels(dictate).join(" + ") : "快捷鍵";
        empty.innerHTML = `<div class="say">《告。尚無紀錄。》</div>按住${esc(key)}說話，結果會出現在這裡。`;
      }
    }

    const keep =
      retentionDays === null
        ? ""
        : retentionDays >= KEEP_FOREVER_DAYS
          ? "會一直保留"
          : `保留 ${formatRetention(retentionDays)}`;
    foot.textContent = keep ? `紀錄只存在這台電腦上，${keep}，可以在設定裡改。` : "紀錄只存在這台電腦上。";
  }

  /** Loads the newest items (or, with `append`, the ones after what is loaded). */
  async function load(append: boolean): Promise<void> {
    const mine = ++loads;
    const params: api.HistoryQuery = { limit: PAGE_SIZE };
    if (query) params.query = query;
    if (append && items.length > 0) params.before_ms = items[items.length - 1].created_at_ms;
    try {
      const { items: got } = await api.historyList(params);
      if (mine !== loads) return;
      items = append ? [...items, ...got.filter((g) => !items.some((i) => i.id === g.id))] : got;
      canLoadMore = got.length === PAGE_SIZE;
      failure = null;
    } catch (err) {
      if (mine !== loads) return;
      if (!append) items = [];
      canLoadMore = false;
      failure = describeError(err);
    }
    render();
  }

  async function loadRetention(): Promise<void> {
    try {
      retentionDays = (await api.settingsGet()).settings.history_retention_days;
    } catch {
      retentionDays = null;
    }
    render();
  }

  function remove(id: number): void {
    deleting.add(id);
    render();
    app.undo.begin({
      message: "已刪除 1 筆紀錄",
      commit: async () => {
        await api.historyDelete(id);
        items = items.filter((item) => item.id !== id);
        deleting.delete(id);
      },
      restore: () => {
        deleting.delete(id);
        render();
      },
      failure: (err) => `無法刪除：${describeError(err)}`,
    });
  }

  async function copy(id: number): Promise<void> {
    const item = items.find((i) => i.id === id);
    if (!item) return;
    try {
      await copyText(item.text);
      toaster.info("已複製");
    } catch (err) {
      toaster.info(describeError(err));
    }
  }

  function toggle(row: HTMLElement): void {
    const id = Number(row.dataset.id);
    const detail = row.nextElementSibling as HTMLElement;
    detail.hidden = !detail.hidden;
    row.setAttribute("aria-expanded", String(!detail.hidden));
    if (detail.hidden) expanded.delete(id);
    else expanded.add(id);
  }

  const runSearch = debounce(() => {
    const text = search.value.trim();
    if (text === query) return;
    query = text;
    void load(false);
  }, SEARCH_DELAY_MS);

  search.addEventListener("input", runSearch);

  el.addEventListener("click", (e) => {
    const target = e.target as Element;
    const act = target.closest<HTMLElement>("[data-act]");
    const row = target.closest<HTMLElement>(".hrow");
    if (act?.dataset.act === "more") {
      void load(true);
    } else if (act && row) {
      const id = Number(row.dataset.id);
      if (act.dataset.act === "copy") void copy(id);
      else if (act.dataset.act === "delete") remove(id);
    } else if (row) {
      toggle(row);
    }
  });
  el.addEventListener("keydown", (e) => {
    if ((e.key === "Enter" || e.key === " ") && (e.target as Element).matches(".hrow")) {
      e.preventDefault();
      toggle(e.target as HTMLElement);
    }
  });

  return {
    id: "history",
    el,
    show() {
      void load(false);
      void loadRetention();
    },
    hide() {
      runSearch.cancel();
      loads++;
    },
  };
}
