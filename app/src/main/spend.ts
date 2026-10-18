// This month's spend against the monthly limit, as the settings page draws it. It is kept apart so
// that the picture can be replaced (a grid of squares, say) without touching the page: it takes
// the two numbers and gives HTML, and nothing else depends on its inside.

import { formatLimit, formatUsd } from "./format";
import { esc } from "./ui";

/** From this share of the monthly limit on, the spend is drawn in the warning color. */
export const WARN_FROM = 0.8;

/** The share of the limit that is spent, from 0 up (1 or more is all of it; no limit counts as all). */
export const spentShare = (month: number, limit: number): number => (limit > 0 ? month / limit : 1);

/** The picture of the spend: a bar and 「US$3.42 / US$20」. */
export function spendVisual(month: number, limit: number): string {
  const used = spentShare(month, limit);
  const width = (Math.min(1, Math.max(0, used)) * 100).toFixed(1);
  return (
    `<div class="spend"><div class="bar"><i class="${used >= WARN_FROM ? "warn" : ""}" style="width:${width}%"></i></div>` +
    `<span class="val num">${esc(formatUsd(month))} / ${esc(formatLimit(limit))}</span></div>`
  );
}
