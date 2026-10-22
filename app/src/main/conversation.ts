// The conversation with Raphael as the main window shows it (`agent.history.list`): one thread of
// every turn, by voice, from a chat app or typed in the 拉斐爾 page, oldest first under the days
// they were had; and the small list of the home page. The person's words are in 「…」 and Raphael's
// in 《prefix。text》. The functions that make HTML put every word that came from the core or from
// the person through `esc` first.

import { t } from "./i18n";

import { replyLine, userLine } from "../shared/anime";
import { dayLabel, formatClock } from "./format";
import type { AgentTurn } from "./types";
import { esc } from "./ui";

/** A question said to the emblem (the key and the overlay): the core's default source. */
export const VOICE = "voice";
/** A question typed in the composer of the 拉斐爾 page. */
export const TYPED = "app";

/** Where a turn came from: `voice`, `app` (typed in the page) or the chat app (`telegram`). */
export const sourceOf = (turn: AgentTurn): string => turn.source || VOICE;

/** One line of the thread: the heading of a day, or a turn. */
export type ThreadItem = { kind: "day"; key: string; label: string } | { kind: "turn"; key: string; turn: AgentTurn };

/**
 * The thread: the turns (in any order) oldest first, each day under its heading (「今天」, 「昨天」, the
 * date). A turn's key is its id, so that the page can tell the lines it drew before.
 */
export function threadItems(turns: readonly AgentTurn[], nowMs: number): ThreadItem[] {
  const oldestFirst = [...turns].sort((a, b) => a.created_at_ms - b.created_at_ms || a.id - b.id);
  const items: ThreadItem[] = [];
  let day: string | null = null;
  for (const turn of oldestFirst) {
    const label = dayLabel(turn.created_at_ms, nowMs);
    if (label !== day) {
      day = label;
      items.push({ kind: "day", key: `day:${label}`, label });
    }
    items.push({ kind: "turn", key: `turn:${turn.id}`, turn });
  }
  return items;
}

/** Text as the search compares it: the same width and case for every way of typing it. */
const fold = (text: string): string => text.normalize("NFKC").toLocaleLowerCase();

/** Whether a turn holds `query`: in what was asked, what was answered or its longer version. Blank matches all. */
export function turnMatches(turn: AgentTurn, query: string): boolean {
  const q = fold(query.trim());
  if (q === "") return true;
  return [turn.question, turn.text, turn.detail ?? ""].some((text) => fold(text).includes(q));
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
