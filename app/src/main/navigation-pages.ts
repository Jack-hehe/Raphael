/** Merge seam for the Automations and Team pages, implemented in the team's other worktree.
 * Register their factories before the window starts; the shell keeps navigation/history stable.
 */
import type { AppContext, Page } from "./types";
import { t } from "./i18n";
import { esc } from "./ui";

type Area = "automations" | "team";
const factories: Partial<Record<Area, (ctx: AppContext) => Page>> = {};
export function registerNavigationPages(pages: Partial<Record<Area, (ctx: AppContext) => Page>>): void { Object.assign(factories, pages); }
export function createNavigationPage(id: Area, ctx: AppContext): Page {
  if (factories[id]) return factories[id]!(ctx);
  const el = document.createElement("section");
  el.className = "page";
  el.hidden = true;
  return { id, el, show() { el.innerHTML = `<h1>${esc(t(`nav.${id}`))}</h1><p class="lede">${esc(t("nav.pagePending"))}</p>`; }, hide() {} };
}
