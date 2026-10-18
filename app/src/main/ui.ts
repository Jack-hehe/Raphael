// The small building blocks of the pages, ported from the mockup: rows, groups, switches,
// buttons, menus and toasts. The functions that make HTML take HTML for `t`, `d`, `ctl`, `rows`
// and `foot`: whatever a person or the core wrote goes through `esc` first.

import type { Notifier } from "./undo";

// ---------- HTML ----------

const ESCAPES: Record<string, string> = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

/** Text as it can go into HTML, in an attribute too. */
export const esc = (s: unknown): string => String(s).replace(/[&<>"']/g, (c) => ESCAPES[c]);

const ICON = {
  home: "M2.5 7.2 8 2.8l5.5 4.4V13a.7.7 0 0 1-.7.7H9.6V9.8H6.4v3.9H3.2a.7.7 0 0 1-.7-.7z",
  clock: "M8 2.2a5.8 5.8 0 1 1 0 11.6A5.8 5.8 0 0 1 8 2.2zM8 4.8V8l2.2 1.4",
  book: "M3 2.8h7.5A1.5 1.5 0 0 1 12 4.3v9H4.5A1.5 1.5 0 0 1 3 11.8zM3 11.8a1.5 1.5 0 0 1 1.5-1.5H12M6 5.6h3.5",
  sliders: "M2.5 5h6.5M11.5 5h2M2.5 11h2M7 11h6.5M10.2 3.4v3.2M5.8 9.4v3.2",
  down: "M3.8 6 8 10.2 12.2 6",
  plus: "M8 3.2v9.6M3.2 8h9.6",
  copy: "M5.5 5.5V3.8a1.3 1.3 0 0 1 1.3-1.3h5.4a1.3 1.3 0 0 1 1.3 1.3v5.4a1.3 1.3 0 0 1-1.3 1.3h-1.7M3.8 5.5h5.4a1.3 1.3 0 0 1 1.3 1.3v5.4a1.3 1.3 0 0 1-1.3 1.3H3.8a1.3 1.3 0 0 1-1.3-1.3V6.8a1.3 1.3 0 0 1 1.3-1.3z",
  trash: "M3 4.5h10M6.5 4.5V3h3v1.5M4.5 4.5l.6 8.3a1 1 0 0 0 1 .9h3.8a1 1 0 0 0 1-.9l.6-8.3",
  search: "M7 2.6a4.4 4.4 0 1 1 0 8.8 4.4 4.4 0 0 1 0-8.8zM10.2 10.2l3.2 3.2",
  x: "M4.8 4.8l6.4 6.4M11.2 4.8l-6.4 6.4",
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

/** A block of rows with a title and a line of small print below. `cls` adds classes to the block. */
export function group(title: string, rows: string[], foot = "", cls = ""): string {
  return (
    `<section class="group${cls ? " " + cls : ""}">${title ? `<h3>${title}</h3>` : ""}` +
    `<div class="list">${rows.join("")}</div>${foot ? `<p class="foot">${foot}</p>` : ""}</section>`
  );
}

/** An on/off switch. `attrs` is raw HTML for the button: a `data-act`, say. */
export const tg = (on: boolean, label: string, attrs = ""): string =>
  `<button type="button" class="tg" role="switch" aria-checked="${on}" aria-label="${esc(label)}"${attrs}></button>`;

export interface Choice {
  value: string;
  label: string;
  /** A second, quieter text after the label in a menu: a price, say. */
  detail?: string;
  /** A line under the label in a menu: what the choice does. */
  desc?: string;
}

/** The button that opens a menu; it shows `current`'s label. `attrs` names it for the page. */
export function sel(options: Choice[], current: string, label: string, attrs = ""): string {
  const shown = options.find((o) => o.value === current)?.label ?? current;
  return (
    `<button type="button" class="sel" aria-haspopup="menu" aria-expanded="false" aria-label="${esc(label)}"${attrs}>` +
    `<span>${esc(shown)}</span>${ico("down")}</button>`
  );
}

export const btn = (label: string, kind = "", attrs = ""): string =>
  `<button type="button" class="btn${kind ? " " + kind : ""}"${attrs}>${label}</button>`;

export const iconBtn = (name: IconName, label: string, attrs = ""): string =>
  `<button type="button" class="icon-btn" aria-label="${esc(label)}" title="${esc(label)}"${attrs}>${ico(name)}</button>`;

/** Keys as caps, such as 「右 Alt」 + 「V」. */
export const keys = (labels: string[]): string =>
  `<span class="keys">${labels.map((k) => `<kbd>${esc(k)}</kbd>`).join("<span>+</span>")}</span>`;

/** A status: always a dot and a word, never the color alone. */
export const st = (kind: "ok" | "warn" | "err" | "idle", text: string): string =>
  `<span class="st${kind === "warn" || kind === "err" ? " " + kind : ""}"><span class="dot ${kind}"></span>${esc(text)}</span>`;

const METER_SEGMENTS = 14;

/** The microphone meter; `setMeter` moves it. */
export const meter = (): string =>
  `<span class="meter" aria-hidden="true">${"<i></i>".repeat(METER_SEGMENTS)}</span>`;

/** Lights the meter's segments for a loudness of 0 to 1. */
export function setMeter(el: Element | null, level: number): void {
  if (!el) return;
  const lit = Math.round(Math.max(0, Math.min(1, level * 1.05)) * METER_SEGMENTS);
  el.querySelectorAll("i").forEach((segment, k) => segment.classList.toggle("on", k < lit));
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
}

export function fieldHTML(placeholder: string, value = "", o: FieldOptions = {}): string {
  return (
    `<label class="field"${o.w ? ` style="width:${o.w}px"` : ""}${o.invalid ? " data-invalid" : ""}>` +
    (o.icon ? ico(o.icon) : "") +
    (o.pre ? `<span class="pre">${esc(o.pre)}</span>` : "") +
    `<input ${o.id ? `id="${o.id}" ` : ""}type="${o.type ?? "text"}" value="${esc(value)}" placeholder="${esc(placeholder)}" ` +
    `aria-label="${esc(o.label ?? placeholder)}"${o.num ? ' inputmode="decimal"' : ""}${o.invalid ? ' aria-invalid="true"' : ""} ` +
    `autocomplete="off" spellcheck="false"></label>`
  );
}

/** Raphael's words, as the subtitles on the desktop have them: Chinese in bold, Japanese in italics. */
export const line = (zh: string, ja: string): string =>
  `<div class="line"><div class="sub-zh">${esc(zh)}</div><div class="sub-ja" lang="ja">${esc(ja)}</div></div>`;

// ---------- menu ----------

/** The menu or panel that is open, the button that opened it, and what it does with keys of its own. */
let menu: { el: HTMLElement; anchor: HTMLElement; onKey?: (e: KeyboardEvent) => boolean } | null = null;
let menuListening = false;

export function closeMenu(): void {
  if (!menu) return;
  menu.anchor.setAttribute("aria-expanded", "false");
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
    const items = menuItems(menu.el);
    if (items.length === 0) return;
    const i = items.indexOf(document.activeElement as HTMLButtonElement);
    if (e.key === "ArrowDown") {
      e.preventDefault();
      items[(i + 1) % items.length].focus();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      items[(i - 1 + items.length) % items.length].focus();
    } else if (e.key === "Home") {
      e.preventDefault();
      items[0].focus();
    } else if (e.key === "End") {
      e.preventDefault();
      items[items.length - 1].focus();
    } else if (e.key === "Escape") {
      e.preventDefault();
      const anchor = menu.anchor;
      closeMenu();
      anchor.focus();
    } else if (e.key === "Tab") {
      closeMenu();
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

/** Puts the open menu under its button (above it when there is no room below), right-aligned with it. */
export function placeMenu(): void {
  if (!menu) return;
  const { el, anchor } = menu;
  const r = anchor.getBoundingClientRect();
  const h = el.offsetHeight;
  const w = el.offsetWidth;
  el.style.left = `${Math.max(8, Math.min(r.right - w, innerWidth - w - 8))}px`;
  el.style.top = `${r.bottom + 4 + h > innerHeight - 8 ? Math.max(8, r.top - 4 - h) : r.bottom + 4}px`;
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
  el.setAttribute("role", o.role);
  if (o.label) el.setAttribute("aria-label", o.label);
  el.innerHTML = html;
  document.body.append(el);
  menu = { el, anchor, onKey: o.onKey };
  anchor.setAttribute("aria-expanded", "true");
  placeMenu();
  return el;
}

/**
 * Opens a menu of `options` under `anchor`, with `current` checked. `onPick` gets the value chosen.
 * `head` is a line of small print above the choices (what their details mean).
 */
export function openMenu(
  anchor: HTMLElement,
  options: Choice[],
  current: string,
  onPick: (value: string) => void,
  head?: string,
): void {
  const detailed = options.some((o) => o.detail);
  const described = options.some((o) => o.desc);
  const html =
    (head ? `<div class="menu-head" aria-hidden="true">${esc(head)}</div>` : "") +
    options
      .map(
        (o) =>
          `<button type="button" role="menuitemradio" data-value="${esc(o.value)}" aria-checked="${o.value === current}">` +
          `<span class="ck">${o.value === current ? CHECK : ""}</span>` +
          (o.desc
            ? `<span class="mtd"><span class="mt">${esc(o.label)}</span><span class="mdesc">${esc(o.desc)}</span></span>`
            : `<span class="mt">${esc(o.label)}</span>`) +
          (o.detail ? `<span class="md num">${esc(o.detail)}</span>` : "") +
          `</button>`,
      )
      .join("");
  const el = openPopover(anchor, html, {
    className: `menu${detailed ? " detailed" : ""}${described ? " described" : ""}`,
    role: "menu",
    label: anchor.getAttribute("aria-label") ?? undefined,
  });
  el.addEventListener("click", (e) => {
    const pick = (e.target as Element).closest("button");
    if (!pick) return;
    closeMenu();
    anchor.focus();
    onPick(pick.dataset.value ?? "");
  });
  (el.querySelector<HTMLElement>('[aria-checked="true"]') ?? el.querySelector<HTMLElement>("button"))?.focus();
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

  private container(): HTMLElement {
    if (!this.host) {
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
    const text = document.createElement("span");
    text.textContent = message;
    toast.append(text);
    if (undo) {
      this.undoToast?.remove();
      this.undoToast = toast;
      toast.insertAdjacentHTML("beforeend", btn("復原", "quiet"));
      toast.querySelector("button")?.addEventListener("click", undo);
    } else {
      setTimeout(() => toast.remove(), infoMs(message));
    }
    this.container().append(toast);
  }

  /** Same as `show(message)`. */
  info(message: string): void {
    this.show(message);
  }

  /** Takes the toast with 「復原」 away. */
  hide(): void {
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
  if (!copied) throw new Error("無法複製到剪貼簿");
}
