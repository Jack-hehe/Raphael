// History: a filter and search, then the dictations grouped by day, each row its time, the cleaned
// text and the target program. Hover for copy and the deletion menu; a click shows what recognition
// heard. A day of the activity grid on Home opens the page at that day (`setDay`): a chip says so
// and takes the filter off, and the page shows all days again the next time it is opened. How long
// the history is kept, and clearing it all, are in Settings › 資料.

import { onLocaleChange, t } from "../i18n";

import * as api from "../api";
import { historyEmptyHint } from "../dictation";
import { openActions } from "../member-column";
import { loadingHTML } from "../loading";
import { dayBounds, dayLabel, describeError, formatClock } from "../format";
import { bindingLabels } from "../keys";
import type { AppContext, HistoryItem, Page } from "../types";
import { Deletions, debounce } from "../undo";
import { btn, closeMenu, copyText, esc, fieldHTML, ico, iconBtn, openMenuOnArrow, swapHTML, tabs, toaster } from "../ui";

/** The search waits this long after the last key. */
const SEARCH_DELAY_MS = 200;
/** How many items one load brings. */
const PAGE_SIZE = 50;

/** `notepad.exe` is shown as `notepad`. */
const programName = (app: string): string => app.replace(/\.exe$/i, "");

/**
 * What a load asks the core for: the search text, the one day if there is one (from its midnight to
 * the next) and, when more is wanted, the time of the last item shown as the cursor, which lies
 * inside the day and so takes the place of its end.
 */
export function historyQuery(query: string, day: string | null, cursor: number | null): api.HistoryQuery {
  const params: api.HistoryQuery = { limit: PAGE_SIZE };
  if (query) params.query = query;
  if (day !== null) [params.after_ms, params.before_ms] = dayBounds(day);
  if (cursor !== null) params.before_ms = cursor;
  return params;
}

type Filter = "all" | "cleaned" | "raw";
const FILTERS: { id: Filter; name: string }[] = [
  { id: "all", get name() { return t("dictionary.001"); } },
  { id: "cleaned", get name() { return t("history.001"); } },
  { id: "raw", get name() { return t("history.002"); } },
];

function itemHTML(item: HistoryItem, expanded: boolean): string {
  const meta = [item.target_app ? programName(item.target_app) : "", item.cleaned ? "" : t("history.002")]
    .filter(Boolean)
    .map(esc)
    .join("　");
  return (
    `<div class="row hrow" data-id="${item.id}" tabindex="0" aria-expanded="${expanded}">` +
    `<div class="time num">${esc(formatClock(item.created_at_ms))}</div>` +
    `<div class="lbl"><div class="txt">${esc(item.text)}</div>${meta ? `<div class="meta">${meta}</div>` : ""}</div>` +
    `<div class="ctl acts">${iconBtn("copy", t("history.003"), ' data-act="copy"')}${iconBtn("more", t("members.more", { p0: formatClock(item.created_at_ms) }), ' data-act="history-menu" aria-haspopup="menu" aria-expanded="false"')}</div></div>` +
    t("history.005", { p0: expanded ? "" : " hidden", p1: esc(item.raw_text) })
  );
}

/** The history page, which can also be opened at one day. */
export interface HistoryPage extends Page {
  /** Shows only the dictations of the day `2026-10-14` (or all days, with null) from the next time the page is shown. */
  setDay(date: string | null): void;
  /** The day the page shows, or will show when it is shown next; null for all of them. */
  day(): string | null;
}

export function createHistory(app: AppContext): HistoryPage {
  const el = document.createElement("div");
  el.className = "page";
  el.dataset.page = "history";
  el.hidden = true;
  el.innerHTML =
    t("history.006") +
    `<div class="tools">` +
    tabs(FILTERS.map((f) => ({ id: f.id, label: f.name, attrs: ` data-filter="${f.id}"` })), "all", { label: t("common.filter"), kind: "filter" }) +
    `${fieldHTML(t("history.007"), "", { icon: "search", get label() { return t("history.008"); }, id: "hist-q" })}</div>` +
    `<div class="day-row" hidden></div>` +
    `<div class="days"></div>` +
    `<div class="more" hidden>${btn(t("history.009"), "", ' data-act="more"')}</div>` +
    `<div class="empty" hidden></div>`;
  const search = el.querySelector<HTMLInputElement>("#hist-q")!;
  const dayRow = el.querySelector<HTMLElement>(".day-row")!;
  const days = el.querySelector<HTMLElement>(".days")!;
  const more = el.querySelector<HTMLElement>(".more")!;
  const empty = el.querySelector<HTMLElement>(".empty")!;

  /** What is loaded, newest first. */
  let items: HistoryItem[] = [];
  /** Deleted, but not yet for the core (the toast is still there): left out of the list. */
  const deleting = new Set<number>();
  /** Deleted for good: a load that started before the delete must not bring the item back. */
  const deletions = new Deletions();
  const expanded = new Set<number>();
  let query = "";
  let canLoadMore = false;
  let failure: (() => string) | null = null;
  let loads = 0;
  let filter: Filter = "all";
  let pending = false;
  /** The one day shown, `2026-10-14`; null for all of them. */
  let day: string | null = null;

  /** The chip that names the day shown and takes the filter off. */
  function paintDay(): void {
    dayRow.hidden = day === null;
    if (day === null) return;
    const all = esc(t("history.dayAll"));
    dayRow.innerHTML =
      `<span class="chip day-chip"><span class="chip-label">${esc(dayLabel(dayBounds(day)[0], Date.now()))}</span>` +
      `<button type="button" class="chip-x" data-act="all-days" aria-label="${all}" title="${all}">${ico("x")}</button></span>`;
  }

  function render(): void {
    paintDay();
    days.setAttribute("aria-busy", String(pending));
    if (pending && items.length === 0) {
      days.innerHTML = loadingHTML();
      empty.hidden = more.hidden = true;
      return;
    }
    const shown = items.filter(
      (item) => !deleting.has(item.id) && (filter === "all" || (filter === "cleaned") === item.cleaned),
    );
    const now = Date.now();
    const groups: { label: string; items: HistoryItem[] }[] = [];
    for (const item of shown) {
      const label = dayLabel(item.created_at_ms, now);
      const last = groups[groups.length - 1];
      if (last?.label === label) last.items.push(item);
      else groups.push({ label, items: [item] });
    }
    // The days come in one after another when the list is new (a load, a search, a filter); drawn
    // again after a delete or with more of it, they stand still (ui.ts `swapHTML`).
    swapHTML(
      days,
      `<div data-stagger="${esc(`${query}|${filter}|${day ?? ""}`)}">` +
        groups
          .map(
            (g) =>
              `<section class="group day"><h3>${esc(g.label)}</h3><div class="list card">` +
              g.items.map((item) => itemHTML(item, expanded.has(item.id))).join("") +
              `</div></section>`,
          )
          .join("") +
        `</div>`,
    );

    more.hidden = !canLoadMore || shown.length === 0;
    empty.hidden = shown.length > 0;
    if (shown.length === 0) {
      if (failure) {
        empty.innerHTML = t("history.010", { p0: esc(failure()) });
      } else if (query || filter !== "all") {
        empty.innerHTML = t("history.011");
      } else if (day !== null) {
        empty.innerHTML = t("history.dayEmpty");
      } else {
        const dictate = app.device().dictation_keys[0];
        const key = dictate ? bindingLabels(dictate).join(" + ") : t("history.012");
        empty.innerHTML = t("history.013", { p0: esc(historyEmptyHint(app.device().dictation_mode, key)) });
      }
    }
  }

  /** Loads the newest items (or, with `append`, the ones after what is loaded). */
  async function load(append: boolean): Promise<void> {
    const mine = ++loads;
    pending = true;
    render();
    const read = deletions.startRead();
    const params = historyQuery(query, day, append && items.length > 0 ? items[items.length - 1].created_at_ms : null);
    try {
      const { items: got } = await api.historyList(params);
      if (mine !== loads) return;
      const alive = deletions.filter(read, got);
      items = append ? [...items, ...alive.filter((g) => !items.some((i) => i.id === g.id))] : alive;
      canLoadMore = got.length === PAGE_SIZE;
      failure = null;
    } catch (err) {
      if (mine !== loads) return;
      if (!append) items = [];
      canLoadMore = false;
      failure = () => describeError(err);
    }
    pending = false;
    render();
  }

  // Settings › 資料 emptied the history: what is loaded goes, and a load already under way brings nothing back.
  app.cleared.on("dictation", () => {
    loads++;
    items = [];
    expanded.clear();
    canLoadMore = false;
    failure = null;
    pending = false;
    render();
  });

  function remove(id: number): void {
    deleting.add(id);
    render();
    app.undo.begin({
      message: t("history.020"),
      commit: async () => {
        await api.historyDelete(id);
        deletions.confirm(id);
        items = items.filter((item) => item.id !== id);
        deleting.delete(id);
      },
      restore: () => {
        deleting.delete(id);
        render();
      },
      failure: (err) => t("dictionary.024", { p0: () => describeError(err) }),
    });
  }

  async function copy(id: number): Promise<void> {
    const item = items.find((i) => i.id === id);
    if (!item) return;
    try {
      await copyText(item.text);
      toaster.info(t("history.021"));
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

  onLocaleChange(() => {
    el.querySelector("h1")!.textContent = t("main.002");
    el.querySelector(".tabs")!.setAttribute("aria-label", t("common.filter"));
    for (const f of FILTERS) el.querySelector<HTMLElement>(`[data-filter="${f.id}"]`)!.textContent = f.name;
    search.placeholder = t("history.007");
    search.setAttribute("aria-label", t("history.008"));
    more.querySelector("button")!.textContent = t("history.009");
    render();
  });

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
    const row = target.closest<HTMLElement>(".hrow");
    if (act?.dataset.act === "all-days") {
      day = null;
      void load(false);
      el.querySelector<HTMLElement>("#hist-q")?.focus();
    } else if (act?.dataset.act === "more") {
      void load(true);
    } else if (act && row) {
      const id = Number(row.dataset.id);
      if (act.dataset.act === "copy") void copy(id);
      else if (act.dataset.act === "history-menu") openActions(act, [{ id: "delete", label: t("history.004"), icon: "trash" }], () => remove(id));
    } else if (row) {
      toggle(row);
    }
  });
  el.addEventListener("keydown", (e) => {
    if (openMenuOnArrow(e)) return;
    if ((e.key === "Enter" || e.key === " ") && (e.target as Element).matches(".hrow")) {
      e.preventDefault();
      toggle(e.target as HTMLElement);
    }
  });

  return {
    id: "history",
    el,
    setDay(date) {
      day = date;
    },
    day: () => day,
    show() {
      void load(false);
    },
    hide() {
      closeMenu();
      runSearch.cancel();
      loads++;
      day = null;
    },
  };
}
