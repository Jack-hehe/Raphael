// Raphael's report on the home page: one sentence, in Chinese and Japanese, about what is wrong or
// that nothing is (spec §12.1, point 3). When something is wrong, the sentence says what happens
// and a button next to it fixes it.

import type { Status } from "./types";

export interface Report {
  zh: string;
  ja: string;
  /** The button next to the report. `settings` goes to the settings page, `retry` asks OpenRouter again. */
  action?: { label: string; target: "settings" | "retry" };
}

const SET_KEY = { label: "設定金鑰", target: "settings" } as const;
const RETRY = { label: "重新連線", target: "retry" } as const;
const ADJUST_LIMIT = { label: "調整上限", target: "settings" } as const;

/** From this share of the monthly limit on, the report warns. */
const WARN_FROM = 0.8;

/** The report for a `status.get` result. First that applies: no key, OpenRouter does not work, the budget is used up or nearly, all well. */
export function buildReport(s: Status, nowMs: number): Report {
  const { openrouter, spend } = s;

  if (openrouter.state === "no_key") {
    return {
      zh: "《告。尚未設定 OpenRouter 金鑰。設定前，聽寫只會貼上未整理的文字。》",
      ja: "《告。OpenRouter のキーが未設定です。整形前のテキストのみ貼り付けます。》",
      action: SET_KEY,
    };
  }

  if (openrouter.state === "error") {
    if (openrouter.error === "auth") {
      return {
        zh: "《告。OpenRouter 金鑰無效或無法讀取。聽寫會先貼上未整理的文字。》",
        ja: "《告。OpenRouter のキーが無効です。整形前のテキストを貼り付けます。》",
        action: SET_KEY,
      };
    }
    return {
      zh: "《告。無法連線到 OpenRouter。聽寫會先貼上未整理的文字。》",
      ja: "《告。OpenRouter に接続できません。整形前のテキストを貼り付けます。》",
      action: RETRY,
    };
  }

  const { month_usd: month, limit_usd: limit } = spend;
  // As in the core: being at the limit is being over it.
  if (month >= limit) {
    return {
      zh: "《告。本月額度已用完，聽寫先不整理。》",
      ja: "《告。今月の予算を使い切りました。整形せずに貼り付けます。》",
      action: ADJUST_LIMIT,
    };
  }

  const p = Math.round((month / limit) * 100);
  if (month / limit >= WARN_FROM) {
    const { m, d } = runOutDay(month, limit, nowMs);
    return {
      zh: `《告。本月額度已使用 ${p}%。照目前的速度，${m} 月 ${d} 日會用完。》`,
      ja: `《告。今月の予算の ${p}% を使用。このペースでは ${m}月${d}日に上限です。》`,
      action: ADJUST_LIMIT,
    };
  }

  return {
    zh: `《告。一切正常。本月額度已使用 ${p}%。》`,
    ja: `《告。全システム正常。今月の予算は ${p}% 使用済みです。》`,
  };
}

/** The day the limit is reached if every day costs what the days so far did on average. */
export function runOutDay(month: number, limit: number, nowMs: number): { m: number; d: number } {
  const now = new Date(nowMs);
  const perDay = month / now.getDate();
  const days = perDay > 0 ? Math.ceil((limit - month) / perDay) : 0;
  const day = new Date(now.getFullYear(), now.getMonth(), now.getDate() + days);
  return { m: day.getMonth() + 1, d: day.getDate() };
}

/**
 * The line at the top of the home page when something keeps dictation from working fully: no key,
 * a key that does not work, no connection, the monthly limit reached. Null when all is well (the
 * details of the key and the spend are on the settings page). First that applies, as in `buildReport`.
 */
export function blockingAlert(s: Status): { text: string; action: string } | null {
  const { openrouter, spend } = s;
  if (openrouter.state === "no_key") return { text: "還沒設定 OpenRouter 金鑰，聽寫只會貼上未整理的文字。", action: "設定金鑰" };
  if (openrouter.state === "error") {
    if (openrouter.error === "auth") return { text: "OpenRouter 金鑰無效，聽寫只會貼上未整理的文字。", action: "更換金鑰" };
    return { text: "連不上 OpenRouter，聽寫先貼上未整理的文字。", action: "查看設定" };
  }
  if (spend.month_usd >= spend.limit_usd) return { text: "本月額度已用完，聽寫先不整理。", action: "調整上限" };
  return null;
}

/** What Raphael reports when the core does not answer `status.get`. */
export function coreDownReport(): Report {
  return {
    zh: "《告。拉斐爾的核心沒有回應，聽寫暫時無法使用。》",
    ja: "《告。コアが応答しません。音声入力は今は使えません。》",
    action: RETRY,
  };
}
