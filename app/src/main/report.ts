// Raphael's report on the home page: one sentence, in the language of the window, about what is
// wrong or that nothing is. When something is wrong, the sentence says what
// happens and a button next to it fixes it.

import { t } from "./i18n";

import type { Status } from "./types";

export interface Report {
  /** The sentence as it is spoken, 《告。…》, in the interface language. */
  text: string;
  /** The button next to the report. `settings` goes to the settings page, `retry` asks OpenRouter again. */
  action?: { label: string; target: "settings" | "retry" };
}

const SET_KEY = { get label() { return t("report.001"); }, target: "settings" } as const;
const RETRY = { get label() { return t("report.002"); }, target: "retry" } as const;
const ADJUST_LIMIT = { get label() { return t("report.003"); }, target: "settings" } as const;

/** From this share of the monthly limit on, the report warns. */
const WARN_FROM = 0.8;

/** The report for a `status.get` result. First that applies: no key, OpenRouter does not work, the budget is used up or nearly, all well. */
export function buildReport(s: Status, nowMs: number): Report {
  const { openrouter, spend } = s;

  if (openrouter.state === "no_key") return { text: t("report.noKey"), action: SET_KEY };

  if (openrouter.state === "error") {
    if (openrouter.error === "auth") return { text: t("report.badKey"), action: SET_KEY };
    return { text: t("report.offline"), action: RETRY };
  }

  const { month_usd: month, limit_usd: limit } = spend;
  // As in the core: being at the limit is being over it.
  if (month >= limit) return { text: t("report.limitReached"), action: ADJUST_LIMIT };

  const p = Math.round((month / limit) * 100);
  if (month / limit >= WARN_FROM) {
    const { m, d } = runOutDay(month, limit, nowMs);
    return { text: t("report.limitSoon", { p0: p, p1: m, p2: d }), action: ADJUST_LIMIT };
  }

  return { text: t("report.allWell", { p0: p }) };
}

/** The day the limit is reached if every day costs what the days so far did on average. */
export function runOutDay(month: number, limit: number, nowMs: number): { m: number; d: number } {
  const now = new Date(nowMs);
  const perDay = month / now.getDate();
  const days = perDay > 0 ? Math.ceil((limit - month) / perDay) : 0;
  const day = new Date(now.getFullYear(), now.getMonth(), now.getDate() + days);
  return { m: day.getMonth() + 1, d: day.getDate() };
}

/** What Raphael reports when the core does not answer `status.get`. */
export function coreDownReport(): Report {
  return { text: t("report.coreDown"), action: RETRY };
}
