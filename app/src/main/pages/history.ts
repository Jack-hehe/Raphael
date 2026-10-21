// History: how long it is kept and that it stays on this computer, then a filter and search, and
// the dictations grouped by day, each row its time, the cleaned text and the target program.
// Hover for copy and delete; a click shows what the speech recognition heard. A day of
// the activity grid on Home opens the page at that day (`setDay`): a chip says so and takes the
// filter off, and the page shows all days again the next time it is opened.

import { onLocaleChange, t } from "../i18n";

import * as api from "../api";
import { historyEmptyHint } from "../dictation";
import { loadingHTML } from "../loading";
import { dayBounds, dayLabel, describeError, formatClock, formatRetention, KEEP_FOREVER_DAYS } from "../format";
import { bindingLabels } from "../keys";
import type { AppContext, HistoryItem, Page } from "../types";
import { Deletions, debounce } from "../undo";
import { type Choice, btn, closeMenu, copyText, esc, fieldHTML, ico, iconBtn, menuIsOpenFor, openMenu, openMenuOnArrow, row, sel, toaster } from "../ui";

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

/** At least a day (the core only checks that it is a number); "keep" is a hundred years. */
export const RETENTIONS: Choice[] = [1, 7, 30, 365, KEEP_FOREVER_DAYS].map((days) => ({
  value: String(days),
  get label() { return formatRetention(days); },
}));

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
    `<div class="ctl acts">${iconBtn("copy", t("history.003"), ' data-act="copy"')}${iconBtn("trash", t("history.004"), ' data-act="delete"')}</div></div>` +
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
    `<section class="card hist-top"></section>` +
    t("dictionary.015") +
    FILTERS.map((f) => `<button type="button" data-filter="${f.id}" aria-pressed="${f.id === "all"}">${f.name}</button>`).join("") +
    `</div>${fieldHTML(t("history.007"), "", { icon: "search", get label() { return t("history.008"); }, id: "hist-q" })}</div>` +
    `<div class="day-row" hidden></div>` +
    `<div class="days"></div>` +
    `<div class="more" hidden>${btn(t("history.009"), "", ' data-act="more"')}</div>` +
    `<div class="empty" hidden></div>`;
  const search = el.querySelector<HTMLInputElement>("#hist-q")!;
  const top = el.querySelector<HTMLElement>(".hist-top")!;
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
  let retentionDays: number | null = null;
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
      paintTop();
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
    days.innerHTML = groups
      .map(
        (g) =>
          `<section class="group day"><h3>${esc(g.label)}</h3><div class="list card">` +
          g.items.map((item) => itemHTML(item, expanded.has(item.id))).join("") +
          `</div></section>`,
      )
      .join("");

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

    paintTop();
  }

  /** The card at the top: how long the history is kept, and that it stays on this computer. */
  function paintTop(): void {
    const current = String(retentionDays ?? 30);
    top.innerHTML =
      row(
        t("history.014", { p0: ico("clock") }),
        retentionDays !== null && retentionDays >= KEEP_FOREVER_DAYS ? t("history.015") : t("history.016"),
        retentionDays === null ? "" : sel(retentionChoices(), current, t("history.017"), ' data-k="retention"'),
      ) + row(t("history.018", { p0: ico("lock") }), t("history.019"));
  }

  /** The choices, with the one in force too if it is none of them. */
  function retentionChoices(): Choice[] {
    const current = String(retentionDays ?? 30);
    return RETENTIONS.some((c) => c.value === current)
      ? RETENTIONS
      : [...RETENTIONS, { value: current, label: formatRetention(Number(current)) }];
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

  async function changeRetention(value: string): Promise<void> {
    try {
      retentionDays = (await api.settingsSet("history_retention_days", Number(value))).settings.history_retention_days;
      void load(false);
    } catch (err) {
      toaster.info(describeError(err));
    }
    paintTop();
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
    const choose = target.closest<HTMLElement>('.sel[data-k="retention"]');
    if (choose) {
      if (menuIsOpenFor(choose)) closeMenu();
      else openMenu(choose, retentionChoices(), String(retentionDays ?? 30), (v) => void changeRetention(v));
      return;
    }
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
      else if (act.dataset.act === "delete") remove(id);
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
      void loadRetention();
    },
    hide() {
      closeMenu();
      runSearch.cancel();
      loads++;
      day = null;
    },
  };
}
