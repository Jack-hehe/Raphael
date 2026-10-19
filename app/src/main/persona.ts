// The personalization card of the home page as HTML: the rose of the kinds of programs one
// dictates into, a table of the shares with the busiest programs of each kind, and one sentence of
// Raphael's about it. The numbers come from categories.ts, the drawing from rose.ts.

import { t } from "./i18n";

import { replyLine } from "../shared/anime";
import { type CategoryId, type Insight, type Personalization, insightOf } from "./categories";
import { roseGeometry, roseSVG } from "./rose";
import { esc } from "./ui";

/** What a kind is called, in the language of the window. */
export function categoryName(id: CategoryId): string {
  switch (id) {
    case "ai":
      return t("category.ai");
    case "dev":
      return t("category.dev");
    case "docs":
      return t("category.docs");
    case "chat":
      return t("category.chat");
    case "web":
      return t("category.web");
    case "other":
      return t("category.other");
  }
}

/** Raphael's sentence under the chart: 《解。…》 in the language of the window. */
export function personaSentence(insight: Insight): string {
  const body =
    insight.kind === "spread"
      ? t("persona.spread")
      : t(insight.kind === "half" ? "persona.halfIn" : "persona.mostIn", { p0: categoryName(insight.id), p1: insight.percent });
  return replyLine(t("persona.prefix"), body);
}

/** A share as the table writes it; a kind with something in it never reads 0%. */
const shareText = (percent: number): string => (percent > 0 ? `${percent}%` : "<1%");

/** The card. `p` is never empty: the caller leaves the card out when there is nothing to show. */
export function personaHTML(p: Personalization): string {
  const rose = roseGeometry(p.categories.map((c) => c.weight));
  const svg = roseSVG(
    rose,
    p.categories.map((c) => ({ id: c.id, title: `${categoryName(c.id)}: ${shareText(c.percent)}` })),
    t("home.personaChart"),
  );
  // The biggest first; kinds with nothing are not listed.
  const rows = p.categories
    .filter((c) => c.weight > 0)
    .map((c, index) => ({ c, index }))
    .sort((a, b) => b.c.percent - a.c.percent || a.index - b.index)
    .map(({ c }) => {
      const apps = c.apps.length > 0 ? `<span class="apps">${c.apps.map(esc).join(esc(t("home.listSep")))}</span>` : "";
      return (
        `<tr data-kind="${c.id}"><td><span class="sw p-${c.id}"></span>${esc(categoryName(c.id))}${apps}</td>` +
        `<td class="num">${esc(shareText(c.percent))}</td></tr>`
      );
    })
    .join("");
  return (
    `<section class="card persona" aria-labelledby="persona-h">` +
    `<div class="card-head"><h2 id="persona-h">${esc(t("home.persona"))}</h2><span class="card-note">${esc(t("home.personaRange"))}</span></div>` +
    `<div class="persona-body">${svg}<table class="persona-table"><tbody>${rows}</tbody></table></div>` +
    `<p class="why">${esc(personaSentence(insightOf(p)))}</p>` +
    `</section>`
  );
}
