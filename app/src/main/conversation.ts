// The conversations with Raphael as the main window shows them (`agent.history.list`): the turns
// grouped by day for reading, oldest first and the newest at the bottom, each with the person's
// words in 「…」 and Raphael's in 《prefix。text》. The functions make HTML; every word that came
// from the core or from the person goes through `esc` first.

import { t } from "./i18n";

import { replyLine, userLine } from "../shared/anime";
import { dayLabel, formatClock } from "./format";
import type { AgentTurn } from "./types";
import { esc, iconBtn } from "./ui";

export interface DayGroup {
  /** The local day, `2026-10-20`: turns of one day share it. */
  key: string;
  /** What the heading says: 「今天」, 「昨天」 or the date. */
  label: string;
  /** Oldest first. */
  turns: AgentTurn[];
}

const pad = (n: number): string => String(n).padStart(2, "0");

/** The local calendar day of `ms`, as `2026-10-20`. */
function dayKey(ms: number): string {
  const d = new Date(ms);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/**
 * The turns the core lists (newest first) laid out for reading: the days oldest first, the turns of
 * a day oldest first, so that the newest is at the bottom, where a conversation ends.
 */
export function groupByDay(newestFirst: readonly AgentTurn[], nowMs: number): DayGroup[] {
  const oldestFirst = [...newestFirst].sort((a, b) => a.created_at_ms - b.created_at_ms || a.id - b.id);
  const groups: DayGroup[] = [];
  for (const turn of oldestFirst) {
    const key = dayKey(turn.created_at_ms);
    const last = groups[groups.length - 1];
    if (last?.key === key) last.turns.push(turn);
    else groups.push({ key, label: dayLabel(turn.created_at_ms, nowMs), turns: [turn] });
  }
  return groups;
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

/** One turn: the time, what was said, what Raphael answered, and a way to open the detail when there is one. */
export function turnHTML(turn: AgentTurn, expanded: boolean): string {
  const detail = hasDetail(turn);
  const id = esc(turn.id);
  return (
    `<div class="turn" data-id="${id}" data-status="${esc(turn.status)}">` +
    `<div class="turn-time num">${esc(formatClock(turn.created_at_ms))}</div>` +
    `<div class="turn-body">` +
    (turn.question.trim() !== "" ? `<div class="turn-q">${esc(userLine(turn.question))}</div>` : "") +
    `<div class="turn-a">${esc(replyLine(turn.prefix, turn.text))}</div>` +
    (detail ? `<div class="turn-detail" id="turn-detail-${id}"${expanded ? "" : " hidden"}>${esc(turn.detail)}</div>` : "") +
    `</div>` +
    `<div class="turn-acts">` +
    (detail
      ? iconBtn("down", t("agent.detail"), ` data-act="detail" aria-expanded="${expanded}" aria-controls="turn-detail-${id}"`)
      : "") +
    `</div></div>`
  );
}

/** The day groups of a conversation as HTML. */
export function daysHTML(groups: readonly DayGroup[], expanded: ReadonlySet<number>): string {
  return groups
    .map(
      (group) =>
        `<section class="group day"><h3>${esc(group.label)}</h3><div class="list card turns">` +
        group.turns.map((turn) => turnHTML(turn, expanded.has(turn.id))).join("") +
        `</div></section>`,
    )
    .join("");
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
