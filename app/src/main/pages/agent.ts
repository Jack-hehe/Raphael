// 拉斐爾: a calm review of the conversations with Raphael (`agent.history.list`). The days run from
// the oldest at the top to today at the bottom, where the page opens; each turn has what was said
// in 「…」, what Raphael answered in 《prefix。text》 and, when there is more, a detail that opens. The
// conversations are had at the desktop, with the key and the overlay: this page only shows them.

import { onLocaleChange, t } from "../i18n";

import * as api from "../api";
import { daysHTML, groupByDay, mergeTurns, oldestId } from "../conversation";
import { describeError } from "../format";
import { loadingHTML } from "../loading";
import type { AgentTurn, AppContext, Page } from "../types";
import { btn, esc, toaster } from "../ui";
import "../agent.css";

/** How many turns one load brings. */
const PAGE_SIZE = 50;
/** Within this many px of the bottom, the page counts as being at the bottom and follows new turns. */
const BOTTOM_SLACK_PX = 80;

export function createAgent(app: AppContext): Page {
  const el = document.createElement("div");
  el.className = "page page-agent";
  el.dataset.page = "agent";
  el.hidden = true;
  el.innerHTML =
    `<div class="page-head"><h1>${esc(t("agent.title"))}</h1>${btn(t("agent.clear"), "quiet sm", ' data-act="clear" hidden')}</div>` +
    `<div class="more" hidden>${btn(t("agent.earlier"), "sm", ' data-act="earlier"')}</div>` +
    `<div class="days"></div>` +
    `<div class="empty" hidden></div>`;
  const heading = el.querySelector<HTMLElement>("h1")!;
  const clearButton = el.querySelector<HTMLElement>('[data-act="clear"]')!;
  const more = el.querySelector<HTMLElement>(".more")!;
  const days = el.querySelector<HTMLElement>(".days")!;
  const empty = el.querySelector<HTMLElement>(".empty")!;

  /** What is loaded, newest first. */
  let turns: AgentTurn[] = [];
  let canLoadEarlier = false;
  let failure: (() => string) | null = null;
  let pending = false;
  let loads = 0;
  let visible = false;
  /** The page has not yet been drawn since it came on screen: it opens at the bottom. */
  let opening = true;
  const expanded = new Set<number>();
  /** While the toast of 「清除對話」 waits: the turns up to this id are left out. */
  let clearedUpTo = 0;

  /** The main window's scrolling area, which the page sits in. */
  const scroller = (): HTMLElement | null => el.closest<HTMLElement>(".main");
  const atBottom = (): boolean => {
    const box = scroller();
    return !box || box.scrollHeight - box.scrollTop - box.clientHeight <= BOTTOM_SLACK_PX;
  };

  function render(): void {
    days.setAttribute("aria-busy", String(pending));
    if (pending && turns.length === 0) {
      days.innerHTML = loadingHTML();
      empty.hidden = more.hidden = clearButton.hidden = true;
      return;
    }
    const shown = turns.filter((turn) => turn.id > clearedUpTo);
    days.innerHTML = daysHTML(groupByDay(shown, Date.now()), expanded);
    more.hidden = !canLoadEarlier || shown.length === 0;
    clearButton.hidden = shown.length === 0;
    empty.hidden = shown.length > 0;
    if (shown.length === 0) {
      empty.innerHTML = failure
        ? `<div class="say">${esc(t("agent.loadFailed"))}</div><p>${esc(failure())}</p>`
        : `<div class="say">${esc(t("agent.empty"))}</div>`;
    }
  }

  /**
   * Loads the newest turns, or with `older` the ones before what is loaded. The page stays where the
   * reader is, except that it opens at the bottom (and stays there if it was at the bottom).
   */
  async function load(older: boolean): Promise<void> {
    const mine = ++loads;
    const follow = !older && (opening || atBottom());
    pending = true;
    if (turns.length === 0) render();
    else days.setAttribute("aria-busy", "true");
    let got: AgentTurn[];
    try {
      const before = older ? oldestId(turns) : undefined;
      got = (await api.agentHistoryList({ limit: PAGE_SIZE, ...(before === undefined ? {} : { before_id: before }) })).turns;
    } catch (err) {
      if (mine !== loads) return;
      pending = false;
      opening = false;
      if (turns.length === 0) {
        // nothing was ever loaded: the page says so
        canLoadEarlier = false;
        failure = () => describeError(err);
      } else {
        // what is on screen stays, and so does the way to ask for more
        toaster.info(describeError(err));
      }
      render();
      return;
    }
    if (mine !== loads) return;
    if (older) {
      turns = mergeTurns(turns, got);
      canLoadEarlier = got.length === PAGE_SIZE;
    } else {
      // The newest page replaces what it covers; what was paged back to beyond it stays.
      const floor = oldestId(got);
      const kept = floor === undefined ? [] : turns.filter((turn) => turn.id < floor);
      canLoadEarlier = kept.length > 0 ? canLoadEarlier : got.length === PAGE_SIZE;
      turns = mergeTurns(got, kept);
    }
    failure = null;
    pending = false;
    opening = false;
    const box = scroller();
    // what is below the reader's eye stays the same height when older turns come in above it
    const fromBottom = box ? box.scrollHeight - box.scrollTop : 0;
    render();
    if (box && follow) box.scrollTop = box.scrollHeight;
    else if (box && older) box.scrollTop = box.scrollHeight - fromBottom;
  }

  /** 「清除對話」: the turns go from the page at once and from the core when the toast has run out. */
  function clearAll(): void {
    const shown = turns.filter((turn) => turn.id > clearedUpTo);
    if (shown.length === 0) return;
    clearedUpTo = Math.max(...shown.map((turn) => turn.id));
    render();
    app.undo.begin({
      message: t("agent.cleared"),
      commit: async () => {
        await api.agentHistoryClear();
        turns = [];
        clearedUpTo = 0;
        canLoadEarlier = false;
        if (visible) render();
      },
      restore: () => {
        clearedUpTo = 0;
        render();
      },
      failure: (err) => t("undo.001", { p0: () => describeError(err) }),
    });
  }

  function toggleDetail(button: HTMLElement): void {
    const turn = button.closest<HTMLElement>(".turn");
    const detail = turn?.querySelector<HTMLElement>(".turn-detail");
    if (!turn || !detail) return;
    const id = Number(turn.dataset.id);
    detail.hidden = !detail.hidden;
    button.setAttribute("aria-expanded", String(!detail.hidden));
    if (detail.hidden) expanded.delete(id);
    else expanded.add(id);
  }

  el.addEventListener("click", (e) => {
    const act = (e.target as Element).closest<HTMLElement>("[data-act]");
    if (!act) return;
    switch (act.dataset.act) {
      case "clear":
        clearAll();
        break;
      case "earlier":
        void load(true);
        break;
      case "detail":
        toggleDetail(act);
        break;
    }
  });

  onLocaleChange(() => {
    heading.textContent = t("agent.title");
    clearButton.textContent = t("agent.clear");
    more.querySelector("button")!.textContent = t("agent.earlier");
    render();
  });

  return {
    id: "agent",
    el,
    show() {
      visible = true;
      void load(false);
    },
    hide() {
      visible = false;
      opening = true;
      loads++;
    },
  };
}
