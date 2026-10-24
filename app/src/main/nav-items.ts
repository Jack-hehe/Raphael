// The pages of the sidebar (main.ts): a button for each, its icon or Raphael's face, then its name.
// The name has a class of its own (`nav-t`): Raphael's face is a span too (avatar.ts), and a new
// language must name the page without writing over his face.

import { brandIcon } from "./brand";
import { t } from "./i18n";
import { esc } from "./ui";

/** The app emblem returns to Raphael's conversations. */
export const navHomeHTML = (): string =>
  `<button type="button" class="btn brand" data-go="agent" aria-label="${esc(t("nav.home"))}" title="${esc(t("nav.home"))}">${brandIcon()}<span>Raphael</span></button>`;

/** One page of the sidebar: `lead` is its icon, or Raphael's face for 拉斐爾, and `name` its name. */
export const navItemHTML = (id: string, name: string, lead: string): string =>
  `<button type="button" class="nav" data-go="${id}" aria-current="false">${lead}<span class="nav-t">${name}</span></button>`;

/** Names the page `id` of the sidebar `nav` in the language of the window. */
export function labelNavItem(nav: ParentNode, id: string, name: string): void {
  nav.querySelector<HTMLElement>(`[data-go="${id}"] .nav-t`)!.textContent = name;
}
