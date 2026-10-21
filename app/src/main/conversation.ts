// The conversations with Raphael as the main window shows them (`agent.history.list`): the turns
// gathered into conversations for the list of the 拉斐爾 page, and the small list of the home page.
// The person's words are in 「…」 and Raphael's in 《prefix。text》. The functions that make HTML put
// every word that came from the core or from the person through `esc` first.

import { t } from "./i18n";

import { replyLine, userLine } from "../shared/anime";
import { dayLabel, formatClock } from "./format";
import type { AgentTurn } from "./types";
import { esc } from "./ui";

/**
 * A conversation ends after this much silence. The core's `session_id` is one press of the key (a
 * new one for every question), so it cannot say which questions belong together; time can. Half an
 * hour keeps a back-and-forth together and starts a new conversation for a new errand.
 */
export const SESSION_GAP_MS = 30 * 60_000;

export const VOICE = "voice";

/** One conversation: the turns of one source, none further from the one before than the gap. */
export interface Session {
  /** Stable while turns are added at its end: the source and its first turn. */
  key: string;
  /** `voice`, or the chat app the turns came from (`telegram`). */
  source: string;
  /** Oldest first. */
  turns: AgentTurn[];
  startMs: number;
  /** When the last turn was made: the list is ordered by it. */
  endMs: number;
}

/** A heading of the list and the conversations under it, newest first. */
export interface SessionDay {
  label: string;
  sessions: Session[];
}

const sourceOf = (turn: AgentTurn): string => turn.source || VOICE;

/**
 * Where a turn's conversation runs. Spoken turns share one lane: every press of the key has its own
 * `session_id`. A chat app's turns run in the lane of their chat, which the core gives as the
 * `session_id`, so two chats never mix.
 */
const laneOf = (turn: AgentTurn): string => {
  const source = sourceOf(turn);
  return source === VOICE ? VOICE : `${source}:${turn.session_id ?? ""}`;
};

/**
 * The turns the core lists (newest first) gathered into conversations, newest first. A turn joins
 * the latest conversation of its lane when it came within `gapMs` of that conversation's last turn;
 * otherwise it starts a new one.
 */
export function groupSessions(newestFirst: readonly AgentTurn[], gapMs = SESSION_GAP_MS): Session[] {
  const oldestFirst = [...newestFirst].sort((a, b) => a.created_at_ms - b.created_at_ms || a.id - b.id);
  const open = new Map<string, Session>();
  const sessions: Session[] = [];
  for (const turn of oldestFirst) {
    const lane = laneOf(turn);
    const current = open.get(lane);
    if (current && turn.created_at_ms - current.endMs <= gapMs) {
      current.turns.push(turn);
      current.endMs = turn.created_at_ms;
      continue;
    }
    const session: Session = {
      key: `${lane}#${turn.id}`,
      source: sourceOf(turn),
      turns: [turn],
      startMs: turn.created_at_ms,
      endMs: turn.created_at_ms,
    };
    open.set(lane, session);
    sessions.push(session);
  }
  return sessions.sort((a, b) => b.endMs - a.endMs || b.turns[0].id - a.turns[0].id);
}

/** The conversations under 「今天」, 「昨天」 and the earlier dates, by their last turn; newest first. */
export function sessionDays(sessions: readonly Session[], nowMs: number): SessionDay[] {
  const days: SessionDay[] = [];
  for (const session of sessions) {
    const label = dayLabel(session.endMs, nowMs);
    const last = days[days.length - 1];
    if (last?.label === label) last.sessions.push(session);
    else days.push({ label, sessions: [session] });
  }
  return days;
}

/** The conversation that holds the turn `turnId`, if one does. */
export const sessionWith = (sessions: readonly Session[], turnId: number): Session | undefined =>
  sessions.find((session) => session.turns.some((turn) => turn.id === turnId));

/** What a conversation is called: its first question, or a word for one that was not heard. */
export function sessionTitle(session: Session): string {
  const first = session.turns.find((turn) => turn.question.trim() !== "");
  return first ? first.question.trim().replace(/\s+/g, " ") : t("agent.untitled");
}

/** Whether a background task of the conversation is still under way. */
export const sessionBusy = (session: Session): boolean =>
  session.turns.some((turn) => turn.task?.state === "running" || turn.task?.state === "queued");

/** Whether a turn has more to say than its line: a longer version that is not the line again. */
export function hasDetail(turn: AgentTurn): turn is AgentTurn & { detail: string } {
  return typeof turn.detail === "string" && turn.detail.trim() !== "" && turn.detail.trim() !== turn.text.trim();
}

/** The older turns that `page` (newest first) brings, added to the ones already loaded, without a turn twice. */
export function mergeTurns(loaded: readonly AgentTurn[], page: readonly AgentTurn[]): AgentTurn[] {
  const known = new Set(loaded.map((turn) => turn.id));
  return [...loaded, ...page.filter((turn) => !known.has(turn.id))].sort((a, b) => b.id - a.id);
}

/** The id to ask the core for the turns before, or undefined for the first page. */
export function oldestId(turns: readonly AgentTurn[]): number | undefined {
  return turns.length === 0 ? undefined : Math.min(...turns.map((turn) => turn.id));
}

/** When a turn happened, short: the time today, 「昨天」, or the date. */
export function turnStamp(ms: number, nowMs: number): string {
  const label = dayLabel(ms, nowMs);
  return label === t("format.001") ? formatClock(ms) : label;
}

/** A turn in the small list of the home page: when, what was said, what was answered. */
export function miniTurnHTML(turn: AgentTurn, nowMs: number): string {
  return (
    `<div class="mini-turn" data-id="${esc(turn.id)}">` +
    `<div class="mini-time num">${esc(turnStamp(turn.created_at_ms, nowMs))}</div>` +
    (turn.question.trim() !== "" ? `<div class="mini-q">${esc(userLine(turn.question))}</div>` : "") +
    `<div class="mini-a">${esc(replyLine(turn.prefix, turn.text))}</div>` +
    `</div>`
  );
}
