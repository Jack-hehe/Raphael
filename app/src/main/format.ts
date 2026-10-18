// Numbers, times and dates as the main window shows them. Times are local.

const pad = (n: number): string => String(n).padStart(2, "0");

/** `3.42` -> `US$3.42`. Under a cent shows four decimals, so a month of a few dictations
 * (`0.0037`) does not read as nothing spent. */
export function formatUsd(n: number): string {
  if (n > 0 && n < 0.01) return `US$${n.toFixed(4)}`;
  return `US$${n.toFixed(2)}`;
}

/** The local time of day: `21:42` */
export function formatClock(ms: number): string {
  const d = new Date(ms);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** The local calendar day `ms` falls on, as a number that grows by one with each day. */
function dayNumber(ms: number): number {
  const d = new Date(ms);
  // Date.UTC of the local date: whole days apart whatever the clock changes in between
  return Math.floor(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()) / 86_400_000);
}

/** `今天`, `昨天`, else `10 月 15 日` (with the year when it is not this year's). */
export function dayLabel(ms: number, nowMs: number): string {
  const days = dayNumber(nowMs) - dayNumber(ms);
  if (days === 0) return "今天";
  if (days === 1) return "昨天";
  const d = new Date(ms);
  const date = `${d.getMonth() + 1} 月 ${d.getDate()} 日`;
  return d.getFullYear() === new Date(nowMs).getFullYear() ? date : `${d.getFullYear()} 年 ${date}`;
}

/** `1340` -> `1.34 秒` */
export function formatSeconds(ms: number): string {
  return `${(ms / 1000).toFixed(2)} 秒`;
}

/** "Keep forever" is a retention this long (days); the core takes it as an ordinary number. */
export const KEEP_FOREVER_DAYS = 36_500;

/** How long the history is kept, as the settings page says it. */
export function formatRetention(days: number): string {
  if (days >= KEEP_FOREVER_DAYS) return "一直保留";
  if (days === 1) return "24 小時";
  if (days === 365) return "1 年";
  return `${days} 天`;
}

/**
 * What went wrong, in words the page can show. `err` is what a call to the Rust side rejected
 * with: a string such as `[-32000] the core exited` (the core's error code and message), or
 * `Raphael's core is not running`, or one of the Chinese messages the Rust side writes itself.
 */
export function describeError(err: unknown): string {
  const text = err instanceof Error ? err.message : typeof err === "string" ? err : JSON.stringify(err);
  // CORE_EXITED and TIMEOUT of rpc.rs, and the message of core_call for a core that is missing.
  if (text.includes("core is not running") || text.startsWith("[-32000]")) return "Raphael 的核心沒有在執行";
  if (text.startsWith("[-32001]")) return "Raphael 的核心沒有及時回應";
  return text.replace(/^\[-?\d+\] /, "");
}

/** The monthly limit as the home page shows it next to the spend: `20`, `7.50`. */
export function formatLimit(limit: number): string {
  return Number.isInteger(limit) ? String(limit) : limit.toFixed(2);
}

/** The number typed in the monthly limit field, or null if it is not a number of 0 or more. */
export function parseLimit(text: string): number | null {
  const trimmed = text.trim();
  if (!/^\d+(\.\d+)?$/.test(trimmed)) return null;
  const limit = Number(trimmed);
  return Number.isFinite(limit) ? limit : null;
}

/** Why `secrets.set_openrouter_key` did not take the key (`reason` of its answer), for the line under the field. */
export function keyReasonText(reason: string): string {
  if (reason === "auth") return "金鑰無效";
  if (reason === "credits") return "這把金鑰在 OpenRouter 上已經沒有額度";
  return "連不上 OpenRouter，請稍後再試";
}

/** The spellings typed in the second field of the dictionary: `拉菲爾、拉非爾` is two. */
export function parseMisheard(text: string): string[] {
  return text
    .split(/[、,，;；\n]/)
    .map((form) => form.trim())
    .filter(Boolean);
}
