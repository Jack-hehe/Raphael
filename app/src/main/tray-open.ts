// What the tray's menu asks of the main window: a page, and perhaps one thing to do on it.

import type { MainEvent, PageId } from "./types";

/** What the window lets the tray do. */
export interface TrayTarget {
  /** The first-run guide is on screen. It takes the place of every page. */
  inGuide(): boolean;
  go(page: PageId): void;
  /** Opens the fields for a new word on the dictionary page and puts the cursor in the word. */
  addWord(): void;
}

/**
 * Opens the page that a `TrayOpen` event names, and does what it asks there. Any other event is
 * not the tray's. During the first-run guide the window stays on the guide: the tray still shows
 * the window, and the person finishes the guide first.
 */
export function followTray(event: MainEvent, target: TrayTarget): void {
  if (event.type !== "TrayOpen" || target.inGuide()) return;
  target.go(event.page);
  // The page is on screen by now, so the field can take the cursor.
  if (event.page === "dictionary" && event.action === "add-word") target.addWord();
}
