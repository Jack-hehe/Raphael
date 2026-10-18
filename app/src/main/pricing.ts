// The prices of the models, as the settings page shows them next to each choice (`pricing.get`).
// Prices are US$ per million tokens (cleanup models, and speech models priced by token) or per
// minute of audio (most speech models).

import { t } from "./i18n";

import { dayLabel } from "./format";
import type { CleanupPrice, Pricing, SttPrice } from "./types";

/**
 * A price per million tokens, to 2 or 3 significant digits: `0.1` -> `$0.10`, `2.5` -> `$2.50`,
 * `0.075` -> `$0.075`, `15` -> `$15`, `12.5` -> `$12.5`.
 */
export function formatPerMtok(n: number): string {
  if (n === 0) return "$0";
  if (n >= 100) return `$${Math.round(n)}`;
  const rounded = Number(n.toPrecision(3));
  if (rounded >= 10) return `$${rounded}`;
  const decimals = (String(rounded).split(".")[1] ?? "").length;
  return `$${decimals < 2 ? rounded.toFixed(2) : String(rounded)}`;
}

/**
 * A price per minute: four decimals under a cent (`0.0019` -> `$0.0019`), two significant digits
 * under a tenth of a cent so the price never reads as nothing (`0.00045` -> `$0.00045`,
 * `0.0001998` -> `$0.00020`), else as per token.
 */
export function formatPerMinute(n: number): string {
  if (n > 0 && n < 0.001) return `$${n.toPrecision(2)}`;
  if (n > 0 && n < 0.01) return `$${n.toFixed(4)}`;
  return formatPerMtok(n);
}

const UNKNOWN = () => t("pricing.001");

const hasTokens = (p: CleanupPrice): boolean => p.input_per_mtok !== null || p.output_per_mtok !== null;
const tokenPair = (p: CleanupPrice): string =>
  `${p.input_per_mtok === null ? "?" : formatPerMtok(p.input_per_mtok)} / ${p.output_per_mtok === null ? "?" : formatPerMtok(p.output_per_mtok)}`;

/** The full price of a cleanup model, for the line under the setting: 「輸入 $0.10 / 輸出 $0.50 每百萬 token」. */
export function cleanupPriceText(p: CleanupPrice | undefined): string {
  if (!p || !hasTokens(p)) return UNKNOWN();
  const input = p.input_per_mtok === null ? "?" : formatPerMtok(p.input_per_mtok);
  const output = p.output_per_mtok === null ? "?" : formatPerMtok(p.output_per_mtok);
  return t("pricing.002", { p0: input, p1: output });
}

/** The short price of a cleanup model, for its menu item: 「$0.10 / $0.50」 (the menu's heading says what they are). */
export function cleanupPriceShort(p: CleanupPrice | undefined): string {
  return p && hasTokens(p) ? tokenPair(p) : UNKNOWN();
}

/** The price of a speech model: 「每分鐘 $0.0019」, or per token for the ones priced that way. */
export function sttPriceText(p: SttPrice | undefined): string {
  if (!p) return UNKNOWN();
  if (p.per_minute !== null) return t("pricing.003", { p0: formatPerMinute(p.per_minute) });
  if (hasTokens(p)) return cleanupPriceText(p);
  return UNKNOWN();
}

/** The same, short enough for a menu item. */
export function sttPriceShort(p: SttPrice | undefined): string {
  if (!p) return UNKNOWN();
  if (p.per_minute !== null) return t("pricing.003", { p0: formatPerMinute(p.per_minute) });
  if (hasTokens(p)) return t("pricing.004", { p0: tokenPair(p) });
  return UNKNOWN();
}

/** The prices are each model's headline price; what a call costs depends on the provider OpenRouter routes it to. */
const PROVIDER_NOTE = () => t("pricing.005");

/** Where the prices come from, for the small print under them. */
export function pricingNote(pricing: Pricing, nowMs: number): string {
  const day = pricing.fetched_at_ms === null ? null : dayLabel(pricing.fetched_at_ms, nowMs);
  switch (pricing.source) {
    case "live":
      return t("pricing.006", { p0: day ?? t("format.001"), p1: PROVIDER_NOTE() });
    case "cached":
      return day
        ? t("pricing.007", { p0: /^\d/.test(day) ? " " : "", p1: day, p2: PROVIDER_NOTE() })
        : t("pricing.008", { p0: PROVIDER_NOTE() });
    default:
      return t("pricing.009", { p0: PROVIDER_NOTE() });
  }
}

/** The price of model `id`, if the list has it. */
export const priceOf = <T extends CleanupPrice>(list: T[] | undefined, id: string): T | undefined =>
  list?.find((p) => p.id === id);
