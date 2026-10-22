// The small building blocks of the pages: rows, groups, switches, buttons, menus and toasts. The
// functions that make HTML take HTML for `t`, `d`, `ctl`, `rows` and `foot`: whatever a person or
// the core wrote goes through `esc` first.

import { onLocaleChange, t, translatedMessage } from "./i18n";

import type { Notifier } from "./undo";

// ---------- HTML ----------

const ESCAPES: Record<string, string> = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

/** Text as it can go into HTML, in an attribute too. */
export const esc = (s: unknown): string => String(s).replace(/[&<>"']/g, (c) => ESCAPES[c]);

const ICON = {
  home: "M2.5 7.2 8 2.8l5.5 4.4V13a.7.7 0 0 1-.7.7H9.6V9.8H6.4v3.9H3.2a.7.7 0 0 1-.7-.7z",
  clock: "M8 2.2a5.8 5.8 0 1 1 0 11.6A5.8 5.8 0 0 1 8 2.2zM8 4.8V8l2.2 1.4",
  book: "M3 2.8h7.5A1.5 1.5 0 0 1 12 4.3v9H4.5A1.5 1.5 0 0 1 3 11.8zM3 11.8a1.5 1.5 0 0 1 1.5-1.5H12M6 5.6h3.5",
  down: "M3.8 6 8 10.2 12.2 6",
  plus: "M8 3.2v9.6M3.2 8h9.6",
  copy: "M5.5 5.5V3.8a1.3 1.3 0 0 1 1.3-1.3h5.4a1.3 1.3 0 0 1 1.3 1.3v5.4a1.3 1.3 0 0 1-1.3 1.3h-1.7M3.8 5.5h5.4a1.3 1.3 0 0 1 1.3 1.3v5.4a1.3 1.3 0 0 1-1.3 1.3H3.8a1.3 1.3 0 0 1-1.3-1.3V6.8a1.3 1.3 0 0 1 1.3-1.3z",
  trash: "M3 4.5h10M6.5 4.5V3h3v1.5M4.5 4.5l.6 8.3a1 1 0 0 0 1 .9h3.8a1 1 0 0 0 1-.9l.6-8.3",
  search: "M7 2.6a4.4 4.4 0 1 1 0 8.8 4.4 4.4 0 0 1 0-8.8zM10.2 10.2l3.2 3.2",
  x: "M4.8 4.8l6.4 6.4M11.2 4.8l-6.4 6.4",
  gear: "M6.8 1.8h2.4l.2 1.6.7.3 1.3-.9 1.8 1.8-.9 1.3.3.7 1.6.2v2.4l-1.6.2-.3.7.9 1.3-1.8 1.8-1.3-.9-.7.3-.2 1.6H6.8l-.2-1.6-.7-.3-1.3.9-1.8-1.8.9-1.3-.3-.7-1.6-.2V6.8l1.6-.2.3-.7-.9-1.3 1.8-1.8 1.3.9.7-.3zM8 5.8a2.2 2.2 0 1 1 0 4.4 2.2 2.2 0 0 1 0-4.4z",
  mic: "M8 2.2a2 2 0 0 1 2 2v3.6a2 2 0 0 1-4 0V4.2a2 2 0 0 1 2-2zM4 7.6a4 4 0 0 0 8 0M8 11.6v2.2",
  wallet: "M2.4 4.6h10.4a.8.8 0 0 1 .8.8v6.4a.8.8 0 0 1-.8.8H3.2a.8.8 0 0 1-.8-.8zM2.4 4.6 10.6 2.6v2M11 8.6h.6",
  sparkle: "M8 2.2l1.3 3.6 3.6 1.3-3.6 1.3L8 12l-1.3-3.6L3.1 7.1l3.6-1.3zM12.6 10.8l.5 1.3 1.3.5-1.3.5-.5 1.3-.5-1.3-1.3-.5 1.3-.5z",
  info: "M8 2.2a5.8 5.8 0 1 1 0 11.6A5.8 5.8 0 0 1 8 2.2zM8 7.2v3.8M8 5h.01",
  lock: "M4.2 7.2h7.6a.6.6 0 0 1 .6.6v5a.6.6 0 0 1-.6.6H4.2a.6.6 0 0 1-.6-.6v-5a.6.6 0 0 1 .6-.6zM5.6 7.2V5.2a2.4 2.4 0 0 1 4.8 0v2",
  left: "M10 3.8 5.8 8l4.2 4.2",
  right: "M6 3.8 10.2 8 6 12.2",
  refresh: "M13 6.5A5.2 5.2 0 0 0 3.4 4.3L2 6M2 2.8V6h3.2M3 9.5a5.2 5.2 0 0 0 9.6 2.2L14 10M10.8 10H14v3.2",
  link: "M6.8 9.2a2.4 2.4 0 0 0 3.4 0l2.2-2.2a2.4 2.4 0 0 0-3.4-3.4l-.8.8M9.2 6.8a2.4 2.4 0 0 0-3.4 0L3.6 9a2.4 2.4 0 0 0 3.4 3.4l.8-.8",
  chat: "M3.2 3.2h9.6a.8.8 0 0 1 .8.8v5.6a.8.8 0 0 1-.8.8H8.4L5.4 13v-2.6H3.2a.8.8 0 0 1-.8-.8V4a.8.8 0 0 1 .8-.8zM5.6 6.2h4.8M5.6 8h3",
  globe: "M8 2.2a5.8 5.8 0 1 1 0 11.6A5.8 5.8 0 0 1 8 2.2zM2.4 8h11.2M8 2.2c1.6 1.6 2.4 3.6 2.4 5.8S9.6 12.2 8 13.8M8 2.2C6.4 3.8 5.6 5.8 5.6 8s.8 4.2 2.4 5.8",
  monitor: "M2.6 3.2h10.8a.6.6 0 0 1 .6.6v6.6a.6.6 0 0 1-.6.6H2.6a.6.6 0 0 1-.6-.6V3.8a.6.6 0 0 1 .6-.6zM6 13.6h4M8 11v2.6",
  shield: "M8 2.2 13 4v3.8c0 3-2.2 5.2-5 6-2.8-.8-5-3-5-6V4zM5.8 8l1.6 1.6 2.8-3",
  star: "M8 2.4l1.8 3.7 4 .6-2.9 2.8.7 4L8 11.6l-3.6 1.9.7-4L2.2 6.7l4-.6z",
  grid: "M2.8 2.8h4v4h-4zM9.2 2.8h4v4h-4zM2.8 9.2h4v4h-4zM9.2 9.2h4v4h-4z",
  check: "M3.6 8.4 6.6 11.2 12.4 4.8",
  sliders: "M2.6 4.6h6.2M12 4.6h1.4M2.6 11.4h1.6M7.4 11.4h6M10.4 3v3.2M5.8 9.8V13",
  keyboard: "M2.2 4.4h11.6a.6.6 0 0 1 .6.6v6a.6.6 0 0 1-.6.6H2.2a.6.6 0 0 1-.6-.6V5a.6.6 0 0 1 .6-.6zM4.4 6.8h.01M6.8 6.8h.01M9.2 6.8h.01M11.6 6.8h.01M5.4 9.2h5.2",
  cloud: "M4.8 12.6h6.8a2.8 2.8 0 0 0 .4-5.6 4 4 0 0 0-7.6-1A3.3 3.3 0 0 0 4.8 12.6z",
  send: "M14 2 7.2 8.8M14 2l-4.3 12-2.5-5.2L2 6.3z",
  stop: "M5 5h6v6H5z",
  bot: "M4 6.4h8a1 1 0 0 1 1 1v4.2a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V7.4a1 1 0 0 1 1-1zM8 6.4V4.2M8 3.4h.01M6 9.4h.01M10 9.4h.01",
  pencil: "M10.6 3.2l2.2 2.2-7.4 7.4H3.2v-2.2zM9.2 4.6l2.2 2.2",
  steps: "M3 4.2h.01M3 8h.01M3 11.8h.01M5.8 4.2h7.2M5.8 8h7.2M5.8 11.8h7.2",
} as const;

export type IconName = keyof typeof ICON;

/** A 16px line icon. */
export const ico = (name: IconName): string =>
  `<svg class="i" viewBox="0 0 16 16" aria-hidden="true"><path d="${ICON[name]}"/></svg>`;

/** A row: the name (and a line of explanation) on the left, the control on the right. */
export function row(t: string, d = "", ctl = "", o: { cls?: string; attrs?: string } = {}): string {
  return (
    `<div class="row${o.cls ? " " + o.cls : ""}"${o.attrs ?? ""}>` +
    `<div class="lbl"><div class="t">${t}</div>${d ? `<div class="d">${d}</div>` : ""}</div>` +
    (ctl ? `<div class="ctl">${ctl}</div>` : "") +
    `</div>`
  );
}

/** A line under a row of a list: a warning about it, or what is wrong with what was typed. */
export const note = (html: string, attrs = ""): string => `<div class="note"${attrs}>${html}</div>`;

/** A block of rows with a line of small print below. */
export function group(rows: string[], foot = ""): string {
  return `<section class="group"><div class="list">${rows.join("")}</div>${foot ? `<p class="foot">${foot}</p>` : ""}</section>`;
}

/** An on/off switch. `attrs` is raw HTML for the button: a `data-act`, say. */
export const tg = (on: boolean, label: string, attrs = ""): string =>
  `<button type="button" class="tg" role="switch" aria-checked="${on}" aria-label="${esc(label)}"${attrs}></button>`;

export interface Choice {
  value: string;
  label: string;
  /**
   * What the closed dropdown shows when the label is too long for it (a microphone's driver in
   * brackets): the menu lists the label, and the dropdown's tooltip has it too.
   */
  short?: string;
  /**
   * The whole text, for the tooltip, when the label itself shortens it (the automatic microphone,
   * by its short name). The label is the tooltip otherwise.
   */
  title?: string;
  /** A second, quieter text after the label in a menu: a price, say. */
  detail?: string;
  /** A line under the label in a menu: what the choice does. */
  desc?: string;
  /** A small tag after the label in a menu: 「建議」, say. */
  tag?: string;
}

/** The text on a closed dropdown for `choice`, and its tooltip: the whole text. */
export const selText = (choice: Choice | undefined, current: string): { shown: string; full: string } => ({
  shown: choice?.short ?? choice?.label ?? current,
  full: choice?.title ?? choice?.label ?? current,
});

/** The button that opens a menu; it shows `current`'s label. `attrs` names it for the page. */
export function sel(options: Choice[], current: string, label: string, attrs = ""): string {
  const { shown, full } = selText(options.find((o) => o.value === current), current);
  return (
    `<button type="button" class="sel" aria-haspopup="menu" aria-expanded="false" aria-label="${esc(label)}" title="${esc(full)}"${attrs}>` +
    `<span>${esc(shown)}</span>${ico("down")}</button>`
  );
}

export const btn = (label: string, kind = "", attrs = ""): string =>
  `<button type="button" class="btn${kind ? " " + kind : ""}"${attrs}>${label}</button>`;

/** A bordered button with an icon only: what it does is its label and its tooltip. */
export const iconButton = (name: IconName, label: string, attrs = "", cls = ""): string =>
  `<button type="button" class="btn icon${cls ? " " + cls : ""}" aria-label="${esc(label)}" title="${esc(label)}"${attrs}>${ico(name)}</button>`;

export const iconBtn = (name: IconName, label: string, attrs = ""): string =>
  `<button type="button" class="icon-btn" aria-label="${esc(label)}" title="${esc(label)}"${attrs}>${ico(name)}</button>`;

/** Keys as caps, such as 「右 Alt」 + 「V」. */
export const keys = (labels: string[]): string =>
  `<span class="keys">${labels.map((k) => `<kbd>${esc(k)}</kbd>`).join("<span>+</span>")}</span>`;

/** A status: always a dot and a word, never the color alone. */
export const st = (kind: "ok" | "warn" | "err" | "idle", text: string): string =>
  `<span class="st${kind === "warn" || kind === "err" ? " " + kind : ""}"><span class="dot ${kind}"></span>${esc(text)}</span>`;

const METER_SEGMENTS = 14;

/**
 * The microphone meter, a row of small bars that light up from the left with its loudness;
 * `setMeter` moves it. `segments` makes a longer one, such as the one as wide as a dropdown.
 */
export const meter = (segments = METER_SEGMENTS): string =>
  `<span class="meter" aria-hidden="true">${"<i></i>".repeat(segments)}</span>`;

/** Lights the meter's segments for a loudness of 0 to 1 (a little headroom, so a loud voice fills it). */
export function setMeter(el: Element | null, level: number): void {
  if (!el) return;
  const segments = el.querySelectorAll("i");
  const lit = Math.round(Math.max(0, Math.min(1, level * 1.05)) * segments.length);
  segments.forEach((segment, k) => segment.classList.toggle("on", k < lit));
}

export interface FieldOptions {
  /** Width in px. */
  w?: number;
  icon?: IconName;
  /** Text before the input, such as `US$`. */
  pre?: string;
  id?: string;
  /** What the input is called for a screen reader (defaults to the placeholder). */
  label?: string;
  num?: boolean;
  type?: "text" | "password";
  invalid?: boolean;
  disabled?: boolean;
}

export function fieldHTML(placeholder: string, value = "", o: FieldOptions = {}): string {
  return (
    `<label class="field"${o.w ? ` style="width:${o.w}px"` : ""}${o.invalid ? " data-invalid" : ""}>` +
    (o.icon ? ico(o.icon) : "") +
    (o.pre ? `<span class="pre">${esc(o.pre)}</span>` : "") +
    `<input ${o.id ? `id="${o.id}" ` : ""}type="${o.type ?? "text"}" value="${esc(value)}" placeholder="${esc(placeholder)}" ` +
    `aria-label="${esc(o.label ?? placeholder)}"${o.num ? ' inputmode="decimal"' : ""}${o.invalid ? ' aria-invalid="true"' : ""} ` +
    `autocomplete="off" spellcheck="false"${o.disabled ? " disabled" : ""}></label>`
  );
}

/** Raphael's words, as the subtitles on the desktop have them: one line, in the language of the window. */
export const line = (text: string): string => `<div class="line"><div class="sub-line">${esc(text)}</div></div>`;

// ---------- menu ----------

/** The menu or panel that is open, the button that opened it, and what it does with keys of its own. */
let menu: { el: HTMLElement; anchor: HTMLElement; onKey?: (e: KeyboardEvent) => boolean } | null = null;
let menuListening = false;

export function closeMenu(): void {
  if (!menu) return;
  menu.anchor.setAttribute("aria-expanded", "false");
  menu.anchor.removeAttribute("aria-controls");
  menu.el.remove();
  menu = null;
}

/** The buttons of the open menu that can be reached with the arrow keys. */
const menuItems = (el: HTMLElement): HTMLButtonElement[] =>
  Array.from(el.querySelectorAll<HTMLButtonElement>("button")).filter((b) => !b.disabled && b.offsetParent !== null);

function listenForMenuEvents(): void {
  if (menuListening) return;
  menuListening = true;
  document.addEventListener(
    "pointerdown",
    (e) => {
      // A click on the button that opened the menu closes it (the page's click handler toggles).
      if (menu && e.target instanceof Node && !menu.el.contains(e.target) && !menu.anchor.contains(e.target)) closeMenu();
    },
    true,
  );
  document.addEventListener("keydown", (e) => {
    if (!menu) return;
    if (menu.onKey?.(e)) {
      e.preventDefault();
      return;
    }
    if (e.key === "Escape" || e.key === "Tab") {
      if (e.key === "Escape") e.preventDefault();
      const anchor = menu.anchor;
      closeMenu();
      anchor.focus({ preventScroll: true });
      return;
    }
    const items = menuItems(menu.el);
    if (items.length === 0) return;
    const i = items.indexOf(document.activeElement as HTMLButtonElement);
    if (e.key === "ArrowDown") {
      e.preventDefault();
      items[(i + 1) % items.length].focus();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      items[i < 0 ? items.length - 1 : (i - 1 + items.length) % items.length].focus();
    } else if (e.key === "Home") {
      e.preventDefault();
      items[0].focus();
    } else if (e.key === "End") {
      e.preventDefault();
      items[items.length - 1].focus();
    }
  });
  window.addEventListener("blur", closeMenu);
  window.addEventListener("resize", closeMenu);
  // Scrolling inside the menu (a long list) leaves it alone; scrolling the page carries it along
  // with its button, and closes it once the button is out of sight.
  document.addEventListener(
    "scroll",
    (e) => {
      if (!menu || (e.target instanceof Node && menu.el.contains(e.target))) return;
      const r = menu.anchor.getBoundingClientRect();
      if (!menu.anchor.isConnected || r.bottom < 0 || r.top > innerHeight) closeMenu();
      else placeMenu();
    },
    true,
  );
}

/** Whether a menu is open for `anchor`. */
export const menuIsOpenFor = (anchor: HTMLElement): boolean => menu?.anchor === anchor;

/** Opens a selector from the keyboard without changing its page's click behavior. */
export function openMenuOnArrow(e: KeyboardEvent): boolean {
  if (e.defaultPrevented || (e.key !== "ArrowDown" && e.key !== "ArrowUp") || !(e.target instanceof Element)) return false;
  const anchor = e.target.closest<HTMLButtonElement>(".sel");
  if (!anchor || anchor.disabled || menuIsOpenFor(anchor)) return false;
  e.preventDefault();
  // The Settings listens on `document`, like the menu: stopping only the propagation would still
  // let a menu listener added after the page's (the first Settings after launch) see this key and
  // move on from the item just focused.
  e.stopImmediatePropagation();
  anchor.click();
  if (menu?.anchor === anchor) {
    const items = menuItems(menu.el);
    (e.key === "ArrowUp" ? items[items.length - 1] : items[0])?.focus();
  }
  return true;
}

/** Keep anchored menus inside the usable viewport, below any custom window controls. */
export function menuPlacement(
  anchor: Pick<DOMRect, "top" | "bottom" | "right">,
  size: { width: number; height: number },
  viewport: { width: number; height: number; headerBottom: number },
): { left: number; top: number; maxHeight: number } {
  const upper = viewport.headerBottom + 8;
  const lower = Math.max(upper, viewport.height - 8);
  const belowTop = Math.max(upper, Math.min(anchor.bottom + 4, lower));
  const aboveBottom = Math.min(lower, Math.max(upper, anchor.top - 4));
  const below = lower - belowTop;
  const above = aboveBottom - upper;
  const useBelow = size.height <= below || (size.height > above && below >= above);
  const maxHeight = useBelow ? below : above;
  return {
    left: Math.max(8, Math.min(anchor.right - size.width, viewport.width - size.width - 8)),
    top: useBelow ? belowTop : aboveBottom - Math.min(size.height, maxHeight),
    maxHeight,
  };
}

/** Prefer below the trigger, then above it; long lists scroll on the roomier side. */
export function placeMenu(): void {
  if (!menu) return;
  const { el, anchor } = menu;
  const r = anchor.getBoundingClientRect();
  const headerBottom = document.getElementById("window-chrome")?.getBoundingClientRect().bottom ?? 0;
  // Release the previous side's height limit before measuring a menu after scrolling or redraw.
  el.style.maxHeight = `${Math.max(0, innerHeight - headerBottom - 16)}px`;
  const placement = menuPlacement(r, { width: el.offsetWidth, height: el.offsetHeight }, {
    width: innerWidth, height: innerHeight, headerBottom,
  });
  el.style.maxHeight = `${placement.maxHeight}px`;
  el.style.left = `${placement.left}px`;
  el.style.top = `${placement.top}px`;
}

export interface PopoverOptions {
  /** The classes of the panel, `menu` among them. */
  className: string;
  role: string;
  label?: string;
  /** Keys the panel handles itself (return true when it did); the others move between its buttons. */
  onKey?: (e: KeyboardEvent) => boolean;
}

/**
 * Opens a panel with `html` under `anchor`: it closes on Escape, Tab, a click outside or the window
 * going away, and the arrow keys move between its buttons. Returns the panel; the caller focuses in it.
 */
export function openPopover(anchor: HTMLElement, html: string, o: PopoverOptions): HTMLElement {
  listenForMenuEvents();
  closeMenu();
  const el = document.createElement("div");
  el.className = o.className;
  el.id = "app-popover";
  el.tabIndex = -1;
  el.setAttribute("role", o.role);
  if (o.label) el.setAttribute("aria-label", o.label);
  el.innerHTML = html;
  document.body.append(el);
  menu = { el, anchor, onKey: o.onKey };
  anchor.setAttribute("aria-expanded", "true");
  anchor.setAttribute("aria-controls", el.id);
  placeMenu();
  return el;
}

/** An item at the foot of a menu that does something rather than choose (「重新搜尋麥克風」). */
export interface MenuAction {
  label: string;
  icon?: IconName;
  /** What it does is under way: the item stays where it is but does nothing until it is done. */
  busy?: boolean;
  /** Runs when the item is chosen. The menu stays open, so that what the action finds shows in it. */
  run(): void;
}

/**
 * Opens a menu of `options` under `anchor`, with `current` checked. `onPick` gets the value chosen.
 * `head` is a line of small print above the choices (what their details mean), and `actions` are
 * items below them, after a line, that do something instead of choosing.
 */
export function openMenu(
  anchor: HTMLElement,
  options: Choice[],
  current: string,
  onPick: (value: string) => void,
  head?: string,
  actions: readonly MenuAction[] = [],
): void {
  const active = document.activeElement as HTMLElement | null;
  const again = menu && (menu.anchor === anchor || (!menu.anchor.isConnected && menu.anchor.dataset.k === anchor.dataset.k)) &&
    active && menu.el.contains(active) ? active : null;
  const detailed = options.some((o) => o.detail);
  const described = options.some((o) => o.desc);
  const html =
    (head ? `<div class="menu-head" aria-hidden="true">${esc(head)}</div>` : "") +
    options
      .map(
        (o) =>
          `<button type="button" role="menuitemradio" tabindex="-1" data-value="${esc(o.value)}" aria-checked="${o.value === current}"` +
          `${o.title ? ` title="${esc(o.title)}"` : ""}>` +
          `<span class="ck">${o.value === current ? CHECK : ""}</span>` +
          (o.desc
            ? `<span class="mtd"><span class="mt">${esc(o.label)}</span><span class="mdesc">${esc(o.desc)}</span></span>`
            : `<span class="mt">${esc(o.label)}</span>`) +
          (o.tag ? `<span class="mtag">${esc(o.tag)}</span>` : "") +
          (o.detail ? `<span class="md num">${esc(o.detail)}</span>` : "") +
          `</button>`,
      )
      .join("") +
    (actions.length ? `<div class="menu-sep" role="separator"></div>` : "") +
    actions
      .map(
        (a, i) =>
          `<button type="button" role="menuitem" tabindex="-1" class="menu-action" data-action="${i}"${a.busy ? ' aria-disabled="true"' : ""}>` +
          `<span class="ck">${a.icon ? ico(a.icon) : ""}</span><span class="mt">${esc(a.label)}</span></button>`,
      )
      .join("");
  const el = openPopover(anchor, html, {
    className: `menu${detailed ? " detailed" : ""}${described ? " described" : ""}`,
    role: "menu",
    label: anchor.getAttribute("aria-label") ?? undefined,
  });
  // The menu is only so wide (styles.css): a label it cuts short has its whole text as a tooltip.
  el.querySelectorAll<HTMLElement>("button").forEach((button) => {
    const label = button.querySelector<HTMLElement>(".mt");
    if (label && !button.hasAttribute("title") && label.scrollWidth > label.clientWidth) button.setAttribute("title", label.textContent ?? "");
  });
  el.addEventListener("click", (e) => {
    const pick = (e.target as Element).closest("button");
    if (!pick) return;
    if (pick.dataset.action !== undefined) {
      const action = actions[Number(pick.dataset.action)];
      if (action && !action.busy) action.run();
      return;
    }
    closeMenu();
    anchor.focus();
    onPick(pick.dataset.value ?? "");
  });
  // The item that had the focus before the menu was drawn again keeps it: a choice by its value, an
  // action by its place.
  const remembered = again === null ? null : Array.from(el.querySelectorAll<HTMLElement>("button")).find((button) =>
    again.dataset.action !== undefined ? button.dataset.action === again.dataset.action : button.dataset.value === again.dataset.value);
  (remembered ?? el.querySelector<HTMLElement>('[aria-checked="true"]') ?? el.querySelector<HTMLElement>("button") ?? el).focus();
}

/** The check mark of a chosen item: drawn, so that it is the same in every font. */
export const CHECK = `<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3.6 8.4 6.6 11.2 12.4 4.8"/></svg>`;

// ---------- toasts ----------

/** How long a short note stays: a little more time for a longer one, which has to be read. */
const infoMs = (message: string): number => Math.min(8000, 2000 + 100 * message.length);

/** The toasts at the bottom of the window: short notes, and the ones with 「復原」. */
class Toaster implements Notifier {
  private host: HTMLElement | null = null;
  private undoToast: HTMLElement | null = null;
  private messages = new Map<HTMLElement, () => string>();

  constructor() {
    onLocaleChange(() => {
      for (const [toast, message] of this.messages) {
        if (!toast.isConnected) { this.messages.delete(toast); continue; }
        toast.querySelector("span")!.textContent = message();
        const undo = toast.querySelector("button");
        if (undo) undo.textContent = t("ui.001");
      }
    });
  }

  private container(): HTMLElement {
    if (!this.host?.isConnected) {
      this.host = document.createElement("div");
      this.host.className = "toasts";
      document.body.append(this.host);
    }
    return this.host;
  }

  /** Shows `message`. With `undo` the toast stays until `hide()` and has a 「復原」 button; without, it goes by itself. */
  show(message: string, undo?: () => void): void {
    const toast = document.createElement("div");
    toast.className = "toast";
    toast.setAttribute("role", "status");
    toast.setAttribute("aria-atomic", "true");
    const text = document.createElement("span");
    this.messages.set(toast, translatedMessage(message));
    text.textContent = message;
    toast.append(text);
    if (undo) {
      if (this.undoToast) this.messages.delete(this.undoToast);
      this.undoToast?.remove();
      this.undoToast = toast;
      toast.insertAdjacentHTML("beforeend", btn(t("ui.001"), "sm"));
      toast.querySelector("button")?.addEventListener("click", undo);
    } else {
      setTimeout(() => { toast.remove(); this.messages.delete(toast); }, infoMs(message));
    }
    this.container().append(toast);
  }

  /** Same as `show(message)`. */
  info(message: string): void {
    this.show(message);
  }

  /** Takes the toast with 「復原」 away. */
  hide(): void {
    if (this.undoToast) this.messages.delete(this.undoToast);
    this.undoToast?.remove();
    this.undoToast = null;
  }
}

export const toaster = new Toaster();

// ---------- clipboard ----------

/** Puts `text` on the clipboard. */
export async function copyText(text: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text);
    return;
  } catch {
    // not allowed here: try the old way
  }
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.style.position = "fixed";
  area.style.opacity = "0";
  document.body.append(area);
  area.select();
  const copied = document.execCommand("copy");
  area.remove();
  if (!copied) throw new Error(t("ui.002"));
}
