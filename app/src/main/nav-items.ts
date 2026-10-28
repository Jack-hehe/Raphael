// The pages of the sidebar (main.ts): a button for each, its icon or Raphael's face, then its name.
// The name has a class of its own (`nav-t`): Raphael's face is a span too (avatar.ts), and a new
// language must name the page without writing over his face.

import { type InventoryView, countText, countUnknown, entryLabel } from "./apps-inventory";
import { brandIcon } from "./brand";
import { t } from "./i18n";
import { esc, ico } from "./ui";

/** The app emblem returns to the home overview. */
export const navHomeHTML = (): string =>
  `<button type="button" class="btn brand" data-go="home" aria-label="${esc(t("nav.home"))}" title="${esc(t("nav.home"))}">${brandIcon()}<span>Raphael</span></button>`;

/** One page of the sidebar: `lead` is its icon, or Raphael's face for 拉斐爾, and `name` its name. */
export const navItemHTML = (id: string, name: string, lead: string): string =>
  `<button type="button" class="nav" data-go="${id}" aria-current="false">${lead}<span class="nav-t">${name}</span></button>`;

/** Names the page `id` of the sidebar `nav` in the language of the window. */
export function labelNavItem(nav: ParentNode, id: string, name: string): void {
  // The entry for the connections opens the settings too, at its own tab: it names itself (`renderAppsEntry`).
  nav.querySelector<HTMLElement>(`[data-go="${id}"]:not([data-pane]) .nav-t`)!.textContent = name;
}

/**
 * 「連結應用程式」 at the bottom of the sidebar: the owner's apps, how many are connected, and the way to the connections
 * (the settings' tab, not a page of its own). `renderAppsEntry` names it and draws the number.
 */
export const navAppsHTML = (): string =>
  `<button type="button" class="nav nav-apps" data-go="settings" data-pane="connections" data-k="nav-apps">${ico("plug")}<span class="nav-t"></span><span class="nav-count" hidden></span></button>`;

/** Names the entry in the language of the window and draws the number of connected apps: none while it is read, 「—」 when it cannot be known. */
export function renderAppsEntry(nav: ParentNode, inventory: InventoryView): void {
  const entry = nav.querySelector<HTMLElement>(".nav-apps");
  if (!entry) return;
  entry.querySelector(".nav-t")!.textContent = t("nav.apps");
  const badge = entry.querySelector<HTMLElement>(".nav-count")!;
  const text = countText(inventory);
  badge.textContent = text;
  badge.hidden = !text;
  badge.toggleAttribute("data-unknown", countUnknown(inventory));
  const label = entryLabel(inventory);
  entry.setAttribute("aria-label", label);
  // The rail of icons shows the name and the number in its tooltip (main.ts `nameRailItems`).
  entry.dataset.navTitle = label;
}
