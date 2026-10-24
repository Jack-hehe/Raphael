// The conversation with Raphael as the main window shows it (`agent.history.list`): one thread of
// every turn, by voice, from a chat app or typed in the 拉斐爾 page, oldest first under the days
// they were had. The person's words are in 「…」 and Raphael's
// in 《prefix。text》. The functions that make HTML put every word that came from the core or from
// the person through `esc` first.

import { esc } from "./ui";
import { dayLabel } from "./format";
import type { AgentTurn } from "./types";

/** A question said to the emblem (the key and the overlay): the core's default source. */
export const VOICE = "voice";
/** A question typed in the composer of the 拉斐爾 page. */
export const TYPED = "app";

/** Where a turn came from: `voice`, `app` (typed in the page) or the chat app (`telegram`). */
export const sourceOf = (turn: AgentTurn): string => turn.source || VOICE;

/** One line of the thread: the heading of a day, or a turn. */
export type ThreadItem =
  | { kind: "day"; key: string; label: string }
  | { kind: "turn"; key: string; turn: AgentTurn; inner?: AgentTurn[] };

/** Whether a turn is one member asking another (`ask_member`), not the owner's own words. */
const exchangeTurn = (turn: AgentTurn): boolean => turn.route === "exchange" || sourceOf(turn) === "member";

/**
 * The thread: the turns (in any order) oldest first, each day under its heading (「今天」, 「昨天」, the
 * date). A turn's key is its id, so that the page can tell the lines it drew before. An exchange
 * that was had while a turn of the thread was being answered (its speaker asking a member before it
 * answered) goes inside that turn (`inner`), so that the answer comes after what the members said.
 */
export function threadItems(turns: readonly AgentTurn[], nowMs: number): ThreadItem[] {
  const oldestFirst = [...turns].sort((a, b) => a.created_at_ms - b.created_at_ms || a.id - b.id);
  const items: ThreadItem[] = [];
  let day: string | null = null;
  let host: { turn: AgentTurn; inner: AgentTurn[] } | null = null;
  for (const turn of oldestFirst) {
    if (exchangeTurn(turn)) {
      if (host && turn.id > host.turn.id && turn.created_at_ms <= answeredAt(host.turn)) {
        host.inner.push(turn);
        continue;
      }
    } else {
      host = (turn.messages?.length ?? 0) > 0 ? { turn, inner: [] } : null;
    }
    const label = dayLabel(turn.created_at_ms, nowMs);
    if (label !== day) {
      day = label;
      items.push({ kind: "day", key: `day:${label}`, label });
    }
    items.push(host?.turn === turn ? { kind: "turn", key: `turn:${turn.id}`, turn, inner: host.inner } : { kind: "turn", key: `turn:${turn.id}`, turn });
  }
  return items;
}

/** When a turn was answered: its last message, or never while it is still being answered. */
function answeredAt(turn: AgentTurn): number {
  if (turn.status === "running" || turn.status === "confirm") return Infinity;
  return Math.max(turn.created_at_ms, ...(turn.messages ?? []).map((message) => message.at_ms));
}

/** Text as the search compares it: the same width and case for every way of typing it. */
const fold = (text: string): string => text.normalize("NFKC").toLocaleLowerCase();

/** Whether a turn holds `query`: in what was asked, what was answered or its longer version. Blank matches all. */
export function turnMatches(turn: AgentTurn, query: string): boolean {
  const q = fold(query.trim());
  if (q === "") return true;
  return [turn.question, turn.text, turn.detail ?? "", ...(turn.messages ?? []).map((message) => message.text)].some((text) => fold(text).includes(q));
}

/**
 * `text` as HTML with every place that holds `query` marked (`<mark>`). Each piece goes through
 * `esc`; the marks are the only markup. The comparison folds width and case like `turnMatches`,
 * and marks the text as it is when folding keeps its length (which it does for the scripts the
 * app is used in); otherwise nothing is marked.
 */
export function highlightHTML(text: string, query: string): string {
  const q = fold(query.trim());
  if (q === "") return esc(text);
  const folded = fold(text);
  if (folded.length !== text.length) return esc(text);
  let html = "";
  let from = 0;
  for (let at = folded.indexOf(q); at !== -1; at = folded.indexOf(q, at + q.length)) {
    html += esc(text.slice(from, at)) + `<mark>${esc(text.slice(at, at + q.length))}</mark>`;
    from = at + q.length;
  }
  return html + esc(text.slice(from));
}

/** Whether a turn has more to say than its line: a longer version that is not the line again. */
export function hasDetail(turn: AgentTurn): turn is AgentTurn & { detail: string } {
  return typeof turn.detail === "string" && turn.detail.trim() !== "" && turn.detail.trim() !== turn.text.trim();
}

/** The older turns that `page` (newest first) brings, added to the ones already loaded, without a turn twice. */
export function mergeTurns(loaded: readonly AgentTurn[], page: readonly AgentTurn[]): AgentTurn[] {
  const known = new Set(loaded.map((turn) => turn.id));
  return [...loaded, ...page.filter((turn) => !known.has(turn.id))].sort((a, b) => b.id - a.id);
}

/**
 * What is loaded once the newest page (`newest`, newest first, at most `pageSize` turns) is brought
 * again. The page replaces the turns it covers, and the older ones loaded before are kept only when
 * they join it: when the page is a full one and reaches back to them (it holds a turn as old as one
 * of them). A page shorter than a page is the whole history, so nothing older is left to keep. And
 * when more turns came meanwhile than one page holds, the old ones would leave a gap that paging up
 * (from the oldest turn loaded) never fills, so the page alone is kept and paging up starts again
 * under it. `keptOlder` says whether the older ones stayed, and with them what the caller knew of
 * the turns before them.
 */
export function refreshTurns(
  loaded: readonly AgentTurn[],
  newest: readonly AgentTurn[],
  pageSize: number,
): { turns: AgentTurn[]; keptOlder: boolean } {
  const floor = oldestId(newest);
  const older = floor === undefined ? [] : loaded.filter((turn) => turn.id < floor);
  const joins = floor !== undefined && newest.length >= pageSize && loaded.some((turn) => turn.id >= floor);
  if (older.length === 0 || !joins) return { turns: mergeTurns(newest, []), keptOlder: false };
  return { turns: mergeTurns(newest, older), keptOlder: true };
}

/** The id to ask the core for the turns before, or undefined for the first page. */
export function oldestId(turns: readonly AgentTurn[]): number | undefined {
  return turns.length === 0 ? undefined : Math.min(...turns.map((turn) => turn.id));
}
