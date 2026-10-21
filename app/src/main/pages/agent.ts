// 拉斐爾: the conversations with Raphael, to review (`agent.history.list`). The conversations are
// listed on the left, newest first under their days; the one chosen fills the right, a question at
// a time: what the person said, what Raphael did in between and what it answered. The page follows
// along while it is open: a turn written or completed (`AgentTurn`) and a background task that took
// a step (`WorkerChanged`) bring the newest turns again, and a task under way can be stopped here.
// The conversations are had by voice or in a chat app: this page only shows them.

import { onLocaleChange, t } from "../i18n";

import * as api from "../api";
import { type Session, groupSessions, mergeTurns, oldestId, sessionDays, sessionWith } from "../conversation";
import { describeError } from "../format";
import { bindingLabels } from "../keys";
import { loadingHTML } from "../loading";
import {
  EMPTY_ART,
  type TimelineState,
  emptyTimelineState,
  formatDuration,
  isLive,
  listMove,
  markSeen,
  requestStop,
  sessionListHTML,
  stepsOpen,
  timelineHTML,
  wantsRefresh,
} from "../timeline";
import type { AgentTurn, AppContext, Page } from "../types";
import { btn, esc, keys, toaster } from "../ui";
import "../agent.css";

/** How many turns one load brings. */
const PAGE_SIZE = 50;
/** Within this many px of the bottom, the conversation counts as read to the end and follows new turns. */
const BOTTOM_SLACK_PX = 80;
/** Events that come together (a task's step and its turn) bring the turns once. */
const REFRESH_DELAY_MS = 150;

export function createAgent(app: AppContext): Page {
  const el = document.createElement("div");
  el.className = "page page-agent";
  el.dataset.page = "agent";
  el.hidden = true;
  el.innerHTML =
    `<div class="page-head"><h1>${esc(t("agent.title"))}</h1>${btn(t("agent.clear"), "sm", ' data-act="clear" hidden')}</div>` +
    `<div class="agent-body" hidden>` +
    `<nav class="sess-pane"><div class="sess-list"></div><div class="more" hidden>${btn(t("agent.earlier"), "sm", ' data-act="earlier"')}</div></nav>` +
    `<section class="tl-pane"><div class="tl-col"></div></section>` +
    `</div>` +
    `<div class="agent-wait"></div>` +
    `<div class="empty agent-empty" hidden></div>`;
  const heading = el.querySelector<HTMLElement>("h1")!;
  const clearButton = el.querySelector<HTMLElement>('[data-act="clear"]')!;
  const body = el.querySelector<HTMLElement>(".agent-body")!;
  const nav = el.querySelector<HTMLElement>(".sess-pane")!;
  const list = el.querySelector<HTMLElement>(".sess-list")!;
  const more = el.querySelector<HTMLElement>(".more")!;
  const pane = el.querySelector<HTMLElement>(".tl-pane")!;
  const column = el.querySelector<HTMLElement>(".tl-col")!;
  const wait = el.querySelector<HTMLElement>(".agent-wait")!;
  const empty = el.querySelector<HTMLElement>(".empty")!;

  /** What is loaded, newest first. */
  let turns: AgentTurn[] = [];
  let canLoadEarlier = false;
  let failure: (() => string) | null = null;
  let pending = false;
  let loads = 0;
  let visible = false;
  /** A turn of the conversation on screen: it is found by it whatever the grouping does around it. */
  let anchor: number | null = null;
  /** The newest conversation is the one shown, and a newer one takes its place when it starts. */
  let followNewest = true;
  /** The conversation drawn last, so that a switch starts at its end and comes in as a whole. */
  let drawnKey: string | null = null;
  const details = new Set<number>();
  const state: TimelineState = emptyTimelineState();
  /** While the toast of 「清除對話」 waits: the turns up to this id are left out. */
  let clearedUpTo = 0;
  let refreshTimer: ReturnType<typeof setTimeout> | null = null;
  let ticker: ReturnType<typeof setInterval> | null = null;

  const atBottom = (): boolean => pane.scrollHeight - pane.scrollTop - pane.clientHeight <= BOTTOM_SLACK_PX;
  const shownTurns = (): AgentTurn[] => turns.filter((turn) => turn.id > clearedUpTo);

  function current(sessions: Session[]): Session | undefined {
    if (followNewest || anchor === null) return sessions[0];
    return sessionWith(sessions, anchor) ?? sessions[0];
  }

  function emptyHTML(): string {
    if (failure) return `<div class="say">${esc(t("agent.loadFailed"))}</div><p>${esc(failure())}</p>`;
    const binding = app.device().agent_keys?.[0];
    const how = binding
      ? `<p class="how">${esc(t("agent.emptyBefore"))}${keys(bindingLabels(binding))}${esc(t("agent.emptyAfter"))}</p>`
      : `<p class="how">${esc(t("agent.emptyNoKeys"))}</p>`;
    return `${EMPTY_ART}<div class="say">${esc(t("agent.empty"))}</div>${how}`;
  }

  function render(): void {
    body.setAttribute("aria-busy", String(pending));
    if (pending && turns.length === 0) {
      wait.innerHTML = loadingHTML();
      wait.hidden = false;
      body.hidden = empty.hidden = clearButton.hidden = true;
      return;
    }
    wait.hidden = true;
    const shown = shownTurns();
    clearButton.hidden = shown.length === 0;
    if (shown.length === 0) {
      body.hidden = true;
      empty.hidden = false;
      empty.innerHTML = emptyHTML();
      drawnKey = null;
      stopTicker();
      return;
    }
    empty.hidden = true;
    body.hidden = false;
    state.nowMs = Date.now();
    const sessions = groupSessions(shown);
    const session = current(sessions)!;
    anchor = session.turns[0].id;
    // The list: the focus stays on the conversation it was on, which the arrow keys move.
    const focused = (document.activeElement as HTMLElement | null)?.closest<HTMLElement>(".sess")?.dataset.key;
    list.innerHTML = sessionListHTML(sessionDays(sessions, state.nowMs), session.key);
    if (focused) (list.querySelector<HTMLElement>(`.sess[data-key="${CSS.escape(focused)}"]`) ?? list.querySelector<HTMLElement>('.sess[aria-current="true"]'))?.focus();
    more.hidden = !canLoadEarlier;
    // The conversation: a new one starts at its end and comes in as a whole; the same one keeps the
    // reader's place, and follows new turns when they were at the end.
    const switched = session.key !== drawnKey;
    if (switched) markSeen(session.turns, state);
    const follow = switched || atBottom();
    const top = pane.scrollTop;
    const focusKey = (document.activeElement as HTMLElement | null)?.closest<HTMLElement>("[data-act][data-focus]")?.dataset.focus;
    column.innerHTML = timelineHTML(session, state, details);
    markSeen(session.turns, state);
    for (const control of column.querySelectorAll<HTMLElement>("[data-act]")) control.dataset.focus = focusKeyOf(control);
    if (focusKey) column.querySelector<HTMLElement>(`[data-focus="${CSS.escape(focusKey)}"]`)?.focus({ preventScroll: true });
    pane.scrollTop = follow ? pane.scrollHeight : top;
    if (switched) {
      column.classList.remove("is-entering");
      void column.offsetWidth; // the class comes off and back on: the movement plays again
      column.classList.add("is-entering");
    }
    drawnKey = session.key;
    if (session.turns.some((turn) => isLive(turn.task))) startTicker();
    else stopTicker();
  }

  /** What a control is, across draws: what it does and the turn or task it does it to. */
  function focusKeyOf(control: HTMLElement): string {
    const owner = control.closest<HTMLElement>(".tn")?.dataset.id ?? "";
    return `${control.dataset.act}:${control.dataset.task ?? owner}`;
  }

  /** The time since a task began, each second, in the lines that show it. */
  function tick(): void {
    const now = Date.now();
    for (const line of column.querySelectorAll<HTMLElement>("[data-since]")) line.textContent = formatDuration(now - Number(line.dataset.since));
  }
  function startTicker(): void {
    if (ticker === null && visible) ticker = setInterval(tick, 1000);
  }
  function stopTicker(): void {
    if (ticker !== null) clearInterval(ticker);
    ticker = null;
  }

  /** Loads the newest turns, or with `older` the ones before what is loaded. */
  async function load(older: boolean): Promise<void> {
    const mine = ++loads;
    pending = true;
    if (turns.length === 0) render();
    else body.setAttribute("aria-busy", "true");
    let got: AgentTurn[];
    try {
      const before = older ? oldestId(turns) : undefined;
      got = (await api.agentHistoryList({ limit: PAGE_SIZE, ...(before === undefined ? {} : { before_id: before }) })).turns;
    } catch (err) {
      if (mine !== loads) return;
      pending = false;
      if (turns.length === 0) {
        canLoadEarlier = false;
        failure = () => describeError(err);
      } else {
        toaster.info(describeError(err)); // what is on screen stays
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
    // A task that ended is no longer being stopped.
    for (const turn of turns) if (turn.task && !isLive(turn.task)) state.stopping.delete(turn.task.id);
    failure = null;
    pending = false;
    render();
  }

  function scheduleRefresh(): void {
    if (!visible || refreshTimer !== null) return;
    refreshTimer = setTimeout(() => {
      refreshTimer = null;
      void load(false);
    }, REFRESH_DELAY_MS);
  }

  function select(key: string, focus: boolean): void {
    const sessions = groupSessions(shownTurns());
    const session = sessions.find((s) => s.key === key);
    if (!session) return;
    followNewest = session === sessions[0];
    anchor = session.turns[0].id;
    render();
    if (focus) list.querySelector<HTMLElement>(`.sess[data-key="${CSS.escape(session.key)}"]`)?.focus();
  }

  /** 「清除對話」: the turns go from the page at once and from the core when the toast has run out. */
  function clearAll(): void {
    const shown = shownTurns();
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

  /** Opens or closes what a turn did, where it is: the box slides, nothing else is drawn again. */
  function toggleSteps(button: HTMLElement): void {
    const id = Number(button.closest<HTMLElement>(".tn")?.dataset.id);
    const turn = turns.find((candidate) => candidate.id === id);
    const box = button.closest<HTMLElement>(".steps");
    const steps = box?.querySelector<HTMLElement>(".steps-body");
    if (!turn || !box || !steps) return;
    const open = !stepsOpen(turn, state);
    state.open.set(id, open);
    box.dataset.open = String(open);
    button.setAttribute("aria-expanded", String(open));
    steps.toggleAttribute("inert", !open);
  }

  function toggleDetail(button: HTMLElement): void {
    const turn = button.closest<HTMLElement>(".tn");
    const detail = turn?.querySelector<HTMLElement>(".tn-detail");
    if (!turn || !detail) return;
    const id = Number(turn.dataset.id);
    detail.hidden = !detail.hidden;
    button.setAttribute("aria-expanded", String(!detail.hidden));
    if (detail.hidden) details.delete(id);
    else details.add(id);
  }

  async function stop(button: HTMLElement): Promise<void> {
    const taskId = Number(button.dataset.task);
    const asked = requestStop(state, taskId, api.workerCancel);
    render(); // the button says it is stopping
    const outcome = await asked;
    if (outcome.error !== undefined) toaster.info(describeError(outcome.error));
    if (visible) void load(false);
  }

  el.addEventListener("click", (e) => {
    const act = (e.target as Element).closest<HTMLElement>("[data-act]");
    if (!act || (act as HTMLButtonElement).disabled) return;
    switch (act.dataset.act) {
      case "clear":
        clearAll();
        break;
      case "earlier":
        void load(true);
        break;
      case "session":
        select(act.dataset.key!, false);
        break;
      case "steps":
        toggleSteps(act);
        break;
      case "detail":
        toggleDetail(act);
        break;
      case "stop":
        void stop(act);
        break;
    }
  });

  // The list is one stop of the Tab key; the arrows go through it, and the conversation follows.
  nav.addEventListener("keydown", (e) => {
    const from = (e.target as Element).closest<HTMLElement>(".sess");
    if (!from) return;
    const all = [...list.querySelectorAll<HTMLElement>(".sess")];
    const to = listMove(e.key, all.indexOf(from), all.length);
    if (to === null) return;
    e.preventDefault();
    const next = all[to];
    if (next && next !== from) select(next.dataset.key!, true);
  });

  app.onEvent((event) => {
    if (wantsRefresh(event)) scheduleRefresh();
  });

  onLocaleChange(() => {
    heading.textContent = t("agent.title");
    clearButton.textContent = t("agent.clear");
    more.querySelector("button")!.textContent = t("agent.earlier");
    nav.setAttribute("aria-label", t("agent.sessions"));
    drawnKey = null;
    render();
  });
  nav.setAttribute("aria-label", t("agent.sessions"));

  return {
    id: "agent",
    el,
    show() {
      visible = true;
      // The page opens on the newest conversation, at its end.
      followNewest = true;
      drawnKey = null;
      void load(false);
    },
    hide() {
      visible = false;
      loads++;
      stopTicker();
      if (refreshTimer !== null) clearTimeout(refreshTimer);
      refreshTimer = null;
    },
  };
}
