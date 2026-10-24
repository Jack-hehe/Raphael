// The way back through the main window, as in a browser: the places the person has been, in the
// order they went, and a step back or forward through them. The mouse's side buttons and
// Alt+Left / Alt+Right take the steps (nav-input.ts); main.ts says where the window is and puts it
// where a step lands. Nothing here reads the page, so the rules can be tested without a browser.

import type { DictationTab, PageId, SettingsPane } from "./types";

/** How many places are kept; the oldest go first. */
export const NAV_LIMIT = 50;

/** One place of the window: a page, and what is needed to show it again the same way. */
export interface Place {
  /** The page on screen, which is the page under the settings panel while the panel is open. */
  page: Exclude<PageId, "settings">;
  /** The tab of the settings panel, while the panel is open over the page. */
  pane?: SettingsPane;
  /** The one day, `2026-10-14`, that the history shows; absent for all days, and on any other page. */
  day?: string;
  tab?: DictationTab;
}

/** Whether the page on screen is the same, whatever is open over it. */
export const samePage = (a: Place, b: Place): boolean => a.page === b.page && a.day === b.day && a.tab === b.tab;

/** Whether the window looks the same at both: the same page, and the same tab open over it if any. */
export const samePlace = (a: Place, b: Place): boolean => samePage(a, b) && a.pane === b.pane;

export type NavStep = "back" | "forward";

/**
 * The places in the order they were visited, and the one the window is at. Going to a new place
 * from the middle forgets the places that were ahead, as a browser does. Two places side by side
 * are never alike, so a step always lands somewhere that looks different.
 */
export class NavHistory {
  private places: Place[] = [];
  private at = -1;

  constructor(private readonly limit = NAV_LIMIT) {}

  /** The person went to `place`. Going to the place it is at adds nothing. */
  push(place: Place): void {
    if (this.at >= 0 && samePlace(this.places[this.at], place)) return;
    this.places.splice(this.at + 1, this.places.length, { ...place });
    if (this.places.length > this.limit) this.places.shift();
    this.at = this.places.length - 1;
  }

  /**
   * The window is at `place` now without a step having taken it there: a tab was chosen in the
   * panel, or the panel was closed with its own button. The place it is at follows. When that
   * makes it the same as the place before or after it (the panel is closed, so it is the page
   * under it again), the two are one: going back to the page you are on would change nothing.
   */
  replace(place: Place): void {
    if (this.at < 0) return;
    this.places[this.at] = { ...place };
    if (this.at > 0 && samePlace(this.places[this.at - 1], place)) {
      this.places.splice(this.at, 1);
      this.at--;
    }
    if (this.at < this.places.length - 1 && samePlace(this.places[this.at + 1], place)) {
      this.places.splice(this.at + 1, 1);
    }
  }

  /** The place before this one, which the window is at from now on; null when there is none. */
  back(): Place | null {
    return this.move(this.at - 1);
  }

  forward(): Place | null {
    return this.move(this.at + 1);
  }

  private move(to: number): Place | null {
    if (to < 0 || to >= this.places.length) return null;
    this.at = to;
    return { ...this.places[to] };
  }
}

/** What the history needs of the main window. */
export interface NavTarget {
  /** The first-run guide is on screen. It takes the place of every page, and is no part of the history. */
  inGuide(): boolean;
  /** Where the window is now; null before its first page. */
  where(): Place | null;
  /** Puts the window at `place`. The history has stepped to it already: this must not record it again. */
  show(place: Place): void;
}

/** The history of the main window: it records what the person does and takes the steps back and forward. */
export class Navigation {
  private readonly history: NavHistory;

  constructor(private readonly target: NavTarget, limit = NAV_LIMIT) {
    this.history = new NavHistory(limit);
  }

  /** Runs `change`, a move to another page or tab that the person asked for, and records where it led. */
  visit(change: () => void): void {
    const left = this.target.where();
    // The place being left may differ from what was recorded: a tab was chosen, the panel was closed.
    if (left) this.history.replace(left);
    change();
    const arrived = this.target.where();
    if (arrived) this.history.push(arrived);
  }

  /** Takes one step. The window shows the place it lands on and nothing is recorded. */
  step(direction: NavStep): void {
    if (this.target.inGuide()) return;
    const here = this.target.where();
    if (here) this.history.replace(here);
    const place = this.history[direction]();
    if (place) this.target.show(place);
  }
}
