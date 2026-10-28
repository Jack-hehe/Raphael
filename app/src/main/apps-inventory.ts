// The owner's apps as the sidebar and the 連接 tab show them. The number is the core's
// (`integrations.inventory`), the one the roster of every model is made from: nothing here counts apps,
// and nothing guesses a number the core did not give. An inventory that cannot be read says so.

import { t } from "./i18n";
import type { AppState, AppsInventory, InventoryApp } from "./types";

/** What was read: being read, read, or not to be had. */
export type InventoryView = AppsInventory | "loading" | "failed";

/** The number of connected apps when the core gave one (it is null while the inventory is being read, or when it is not whole). */
export function connectedCount(inventory: InventoryView): number | null {
  return typeof inventory === "object" && inventory.state === "complete" ? inventory.count : null;
}

/** The number cannot be known: the inventory could not be read, or only a part of it could. (Not: it is still being read.) */
export const countUnknown = (inventory: InventoryView): boolean =>
  inventory === "failed" || (typeof inventory === "object" && connectedCount(inventory) === null);

/** The number as a badge or a tab shows it: nothing while it is read, 「—」 when it cannot be known. */
export function countText(inventory: InventoryView): string {
  const count = connectedCount(inventory);
  return count !== null ? String(count) : countUnknown(inventory) ? "—" : "";
}

/** What a screen reader and the tooltip of the rail say of the sidebar's entry: its name and how many apps are connected. */
export function entryLabel(inventory: InventoryView): string {
  const count = connectedCount(inventory);
  if (count !== null) return t("nav.appsCount", { p0: count });
  return countUnknown(inventory) ? t("nav.appsUnknown") : t("nav.apps");
}

/** The app of the inventory with this id, if the inventory was read. */
export const appOf = (inventory: InventoryView, id: string): InventoryApp | undefined =>
  typeof inventory === "object" ? inventory.apps.find((app) => app.id === id) : undefined;

/** Whether the app with this id is one of the connected ones; null when that is not known. */
export function isConnected(inventory: InventoryView, id: string): boolean | null {
  const app = appOf(inventory, id);
  return app ? app.connected : null;
}

/**
 * The line that says why an app that is on and signed in is not one of the connected ones; none for a connected one, and none for what
 * the row says by itself (a switch that is off, a sign-in that is waited for, a failure).
 */
export function whyNot(state: AppState): "conn.state.duplicate" | "conn.state.noTools" | "conn.state.unknown" | null {
  switch (state) {
    case "duplicate": return "conn.state.duplicate";
    case "no_tools": return "conn.state.noTools";
    case "unknown": return "conn.state.unknown";
    default: return null;
  }
}
