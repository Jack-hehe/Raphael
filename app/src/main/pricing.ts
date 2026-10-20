// How the prices of the models read (`models.catalog`), for the model picker and the settings page.
// Prices are US$ per million tokens (language models, and speech models priced by token) or per
// minute of audio (most speech models).

import { t } from "./i18n";

import { dayLabel } from "./format";
import type { ModelCatalog } from "./types";

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

/** The prices are each model's headline price; what a call costs depends on the provider OpenRouter routes it to. */
const PROVIDER_NOTE = () => t("pricing.005");

/** Where the prices of the catalog come from, for the small print under them. */
export function pricingNote(pricing: Pick<ModelCatalog, "source" | "fetched_at_ms">, nowMs: number): string {
  const day = pricing.fetched_at_ms === null ? null : dayLabel(pricing.fetched_at_ms, nowMs, true);
  switch (pricing.source) {
    case "live":
      return t("pricing.006", { p0: day ?? t("format.todayInSentence"), p1: PROVIDER_NOTE() });
    case "cached":
      return day
        ? t("pricing.007", { p0: /^\d/.test(day) ? " " : "", p1: day, p2: PROVIDER_NOTE() })
        : t("pricing.008", { p0: PROVIDER_NOTE() });
    default:
      return t("pricing.009", { p0: PROVIDER_NOTE() });
  }
}
