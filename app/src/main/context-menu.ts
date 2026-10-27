// Right click is the ⋯. Every row, card or item that has a ⋯ (overflow) button opens that button's menu when it is
// right-clicked anywhere on it, at the pointer, and for the focused item from the keyboard (Shift+F10, the Menu
// key): the very same menu, since the button itself is pressed and its own handler runs. Anywhere else the
// browser's menu (reload, back, ...) has no place in an app, so nothing opens.
//
// `OVERFLOW_ROWS` is the one table of them. A ⋯ drawn without a line here fails context-menu.test.ts, and so does a
// line that no ⋯ uses; look's audit finds any ⋯ in a rendered page that no line covers (R-UI-44).

import { pressOverflow } from "./ui";

export interface OverflowRow {
  /** The ⋯ button. */
  more: string;
  /** The row, card or item around it: a right click anywhere inside presses the ⋯. */
  row: string;
}

export const OVERFLOW_ROWS: readonly OverflowRow[] = [
  { more: "[data-member-more]", row: ".mb-item" },
  { more: "[data-group-more]", row: ".mb-ghead" },
  { more: "[data-conversation-more]", row: ".conversation-row" },
  { more: "[data-room-more]", row: ".au-room-row" },
  { more: "[data-room-menu]", row: ".au-room-pane > .page-head" },
  { more: '[data-det="routine-menu"]', row: ".mb-rt" },
  { more: '[data-dlg="routine-menu"]', row: ".mp-resource" },
  { more: '[data-dlg="memory-menu"]', row: ".mp-resource" },
  { more: '[data-res="skill-menu"]', row: ".mp-resource" },
  { more: '[data-res="command-menu"]', row: ".mp-resource" },
  { more: '[data-mem="more"]', row: "[data-mem-item]" },
  { more: '[data-rule="more"]', row: ".rules-row" },
  { more: '[data-act="word-menu"]', row: ".wrow" },
  { more: '[data-act="history-menu"]', row: ".hrow" },
  { more: '[data-act="computer-menu"]', row: "div.row" },
  { more: '[data-team="group-menu"]', row: ".team-group > header" },
  { more: '[data-conn="server-menu"]', row: ".cs-dlg-head" },
  { more: "[data-cli-menu]", row: ".cli-card" },
  { more: '[data-act="more-message"]', row: ".chat-message" },
];

/** The ⋯ of the innermost row around `target`, with that row; a ⋯ that cannot be pressed (disabled) has no menu to open. */
export function overflowAt(target: EventTarget | null): { button: HTMLButtonElement; row: Element } | null {
  for (let node = target instanceof Element ? target : null; node; node = node.parentElement) {
    for (const { more, row } of OVERFLOW_ROWS) {
      if (!node.matches(row)) continue;
      const button = [...node.querySelectorAll<HTMLButtonElement>(more)].find((candidate) => !candidate.disabled);
      if (button) return { button, row: node };
    }
  }
  return null;
}

/** The ⋯ buttons under `root` that a right click cannot reach: in no row of the table, or in a row that holds another one. */
export function uncoveredOverflows(root: ParentNode = document): HTMLElement[] {
  return [...root.querySelectorAll<HTMLElement>("[data-overflow]")].filter((button) => {
    for (let node = button.parentElement; node; node = node.parentElement) {
      for (const { more, row } of OVERFLOW_ROWS) {
        if (button.matches(more) && node.matches(row)) return node.querySelectorAll(more).length !== 1;
      }
    }
    return true;
  });
}

/** A right button that moved further than this (px) between its press and its release was dragged: a drag, not a click. */
const DRAG_PX = 4;

/**
 * Opens the menu of the ⋯ in the row that is right-clicked (at the pointer) or has the key (Shift+F10, Menu); returns how
 * to stop. Text fields keep the browser's own menu. A right click that something else already handled is left to it.
 */
export function installContextMenus(doc: Document = document): () => void {
  let press: { x: number; y: number } | null = null;
  const onPress = (event: MouseEvent): void => {
    press = event.button === 2 ? { x: event.clientX, y: event.clientY } : null;
  };
  const onMenu = (event: MouseEvent): void => {
    if (event.target instanceof HTMLInputElement) return;
    const claimed = event.defaultPrevented;
    event.preventDefault();
    const pressed = press;
    press = null;
    if (claimed) return;
    const found = overflowAt(event.target);
    if (!found) return;
    if (event.button !== 2) {
      // The key makes a right click that has no button of the mouse behind it, and no point of its own: the menu stands
      // under its ⋯, or in the row when that is not drawn.
      const box = found.row.getBoundingClientRect();
      pressOverflow(found.button, found.button.getClientRects().length > 0 ? undefined : { x: box.left + 16, y: box.top + box.height / 2 });
    } else if (pressed === null || Math.hypot(event.clientX - pressed.x, event.clientY - pressed.y) <= DRAG_PX) {
      pressOverflow(found.button, { x: event.clientX, y: event.clientY });
    }
  };
  doc.addEventListener("pointerdown", onPress, true);
  doc.addEventListener("contextmenu", onMenu);
  return () => {
    doc.removeEventListener("pointerdown", onPress, true);
    doc.removeEventListener("contextmenu", onMenu);
  };
}
