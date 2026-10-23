// The small building blocks of the pages: rows, groups, switches, buttons, menus and toasts. The
// functions that make HTML take HTML for `t`, `d`, `ctl`, `rows` and `foot`: whatever a person or
// the core wrote goes through `esc` first.

import { onLocaleChange, t, translatedMessage } from "./i18n";

import { type Notifier, UNDO_MS } from "./undo";

// ---------- HTML ----------

const ESCAPES: Record<string, string> = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

/** Text as it can go into HTML, in an attribute too. */
export const esc = (s: unknown): string => String(s).replace(/[&<>"']/g, (c) => ESCAPES[c]);

// The app's line icons, one 16 x 16 drawing each: drawn to show at 16 CSS px (20 where a larger one
// is meant), with a 1.5 stroke, round caps and joins and no fill (styles.css `svg.i`). Straight
// lines sit on .25 / .75 so that one edge of each falls on a whole pixel; an icon shown at 14 or
// 15 px, or moved by a fractional transform, loses that and blurs. Dots (`info`, `steps`, `more`)
// are small closed circles the stroke fills.
const ICON = {
  home: "M2.25 6.75 7.75 2.25 13.25 6.75M3.75 5.75V11.75A1.5 1.5 0 0 0 5.25 13.25H6.25V9.25H9.25V13.25H10.25A1.5 1.5 0 0 0 11.75 11.75V5.75",
  clock: "M13.25 7.75A5.5 5.5 0 1 1 2.25 7.75A5.5 5.5 0 1 1 13.25 7.75ZM7.75 4.25V7.75L10.25 9.25",
  book: "M4.75 2.75H10.75A1.5 1.5 0 0 1 12.25 4.25V13.25H4.75A1.5 1.5 0 0 1 3.25 11.75V4.25A1.5 1.5 0 0 1 4.75 2.75M3.25 11.75A1.5 1.5 0 0 1 4.75 10.25H12.25M6.25 5.75H9.25",
  down: "M3.75 5.75 7.75 9.75 11.75 5.75",
  up: "M3.75 9.75 7.75 5.75 11.75 9.75",
  plus: "M7.75 3.25V12.25M3.25 7.75H12.25",
  copy: "M6.25 3.75A1.5 1.5 0 0 1 7.75 2.25H11.75A1.5 1.5 0 0 1 13.25 3.75V7.75A1.5 1.5 0 0 1 11.75 9.25M3.75 6.25H7.75A1.5 1.5 0 0 1 9.25 7.75V11.75A1.5 1.5 0 0 1 7.75 13.25H3.75A1.5 1.5 0 0 1 2.25 11.75V7.75A1.5 1.5 0 0 1 3.75 6.25Z",
  trash: "M2.75 4.75H12.75M5.75 4.75V2.75H9.75V4.75M4.25 4.75V11.75A1.5 1.5 0 0 0 5.75 13.25H9.75A1.5 1.5 0 0 0 11.25 11.75V4.75M7.75 7.75V10.25",
  search: "M10.75 6.75A4 4 0 1 1 2.75 6.75A4 4 0 1 1 10.75 6.75ZM9.75 9.75 13.25 13.25",
  x: "M4.25 4.25 11.25 11.25M11.25 4.25 4.25 11.25",
  gear: "M6.25 2.25H9.25L9.75 4.25 11.75 3.75 13.25 6.25 11.75 7.75 13.25 9.25 11.75 11.75 9.75 11.25 9.25 13.25H6.25L5.75 11.25 3.75 11.75 2.25 9.25 3.75 7.75 2.25 6.25 3.75 3.75 5.75 4.25ZM9.75 7.75A2 2 0 1 1 5.75 7.75A2 2 0 1 1 9.75 7.75Z",
  mic: "M7.75 2.25A2 2 0 0 1 9.75 4.25V7.25A2 2 0 0 1 5.75 7.25V4.25A2 2 0 0 1 7.75 2.25ZM3.75 7.25A4 4 0 0 0 11.75 7.25M7.75 11.25V13.25M5.75 13.25H9.75",
  wallet: "M12.25 5.25V2.75L3.75 4.75M3.75 5.25H11.75A1.5 1.5 0 0 1 13.25 6.75V11.75A1.5 1.5 0 0 1 11.75 13.25H3.75A1.5 1.5 0 0 1 2.25 11.75V6.75A1.5 1.5 0 0 1 3.75 5.25ZM10.75 9.25H11.25",
  sparkle: "M7.75 2.25 9.25 6.25 13.25 7.75 9.25 9.25 7.75 13.25 6.25 9.25 2.25 7.75 6.25 6.25Z",
  info: "M13.25 7.75A5.5 5.5 0 1 1 2.25 7.75A5.5 5.5 0 1 1 13.25 7.75ZM7.75 7.75V10.75M8.35 4.75A.6 .6 0 1 1 7.15 4.75A.6 .6 0 1 1 8.35 4.75Z",
  lock: "M4.75 7.25H10.75A1.5 1.5 0 0 1 12.25 8.75V11.75A1.5 1.5 0 0 1 10.75 13.25H4.75A1.5 1.5 0 0 1 3.25 11.75V8.75A1.5 1.5 0 0 1 4.75 7.25ZM5.25 7.25V4.75A2.5 2.5 0 0 1 10.25 4.75V7.25M7.75 9.75V10.75",
  left: "M9.75 3.75 5.75 7.75 9.75 11.75",
  right: "M5.75 3.75 9.75 7.75 5.75 11.75",
  refresh: "M12.75 6.25A5.25 5.25 0 0 0 3.25 4.75L2.25 6.25M2.25 2.75V6.25H5.75M2.75 9.25A5.25 5.25 0 0 0 12.25 10.75L13.25 9.25M9.75 9.25H13.25V12.75",
  link: "M6.25 5.25 7.75 3.75A3 3 0 0 1 12 8L10.5 9.5M5 6 3.5 7.5A3 3 0 0 0 7.75 11.75L9.25 10.25M5.75 9.75 9.75 5.75",
  chat: "M3.75 2.75H11.75A1.5 1.5 0 0 1 13.25 4.25V8.75A1.5 1.5 0 0 1 11.75 10.25H7.75L4.25 13.25V10.25H3.75A1.5 1.5 0 0 1 2.25 8.75V4.25A1.5 1.5 0 0 1 3.75 2.75ZM5.25 5.75H10.25M5.25 7.75H8.25",
  globe: "M13.25 7.75A5.5 5.5 0 1 1 2.25 7.75A5.5 5.5 0 1 1 13.25 7.75ZM2.25 7.75H13.25M7.75 2.25C4.75 5.25 4.75 10.25 7.75 13.25C10.75 10.25 10.75 5.25 7.75 2.25Z",
  monitor: "M3.75 2.75H11.75A1.5 1.5 0 0 1 13.25 4.25V9.25A1.5 1.5 0 0 1 11.75 10.75H3.75A1.5 1.5 0 0 1 2.25 9.25V4.25A1.5 1.5 0 0 1 3.75 2.75ZM7.75 10.75V13.25M5.25 13.25H10.25",
  shield: "M7.75 2.25 12.75 4.25V7.75C12.75 10.25 10.75 12.25 7.75 13.25C4.75 12.25 2.75 10.25 2.75 7.75V4.25ZM5.25 7.75 7.25 9.75 10.25 6.75",
  star: "M7.75 2.25 9.75 6.25 13.75 6.75 10.75 9.75 11.25 13.75 7.75 11.75 4.25 13.75 4.75 9.75 1.75 6.75 5.75 6.25Z",
  grid: "M3.75 2.25H5.25A1.5 1.5 0 0 1 6.75 3.75V5.25A1.5 1.5 0 0 1 5.25 6.75H3.75A1.5 1.5 0 0 1 2.25 5.25V3.75A1.5 1.5 0 0 1 3.75 2.25ZM10.25 2.25H11.75A1.5 1.5 0 0 1 13.25 3.75V5.25A1.5 1.5 0 0 1 11.75 6.75H10.25A1.5 1.5 0 0 1 8.75 5.25V3.75A1.5 1.5 0 0 1 10.25 2.25ZM3.75 8.75H5.25A1.5 1.5 0 0 1 6.75 10.25V11.75A1.5 1.5 0 0 1 5.25 13.25H3.75A1.5 1.5 0 0 1 2.25 11.75V10.25A1.5 1.5 0 0 1 3.75 8.75ZM10.25 8.75H11.75A1.5 1.5 0 0 1 13.25 10.25V11.75A1.5 1.5 0 0 1 11.75 13.25H10.25A1.5 1.5 0 0 1 8.75 11.75V10.25A1.5 1.5 0 0 1 10.25 8.75Z",
  check: "M3.25 7.75 6.25 10.75 12.25 4.75",
  sliders: "M2.25 4.75H8.25M11.25 4.75H13.25M2.25 10.75H4.25M7.25 10.75H13.25M9.75 3.25V6.25M5.75 9.25V12.25",
  keyboard: "M3.75 3.75H11.75A1.5 1.5 0 0 1 13.25 5.25V10.25A1.5 1.5 0 0 1 11.75 11.75H3.75A1.5 1.5 0 0 1 2.25 10.25V5.25A1.5 1.5 0 0 1 3.75 3.75ZM4.75 6.25H5.25M7.75 6.25H8.25M10.75 6.25H11.25M5.25 9.25H10.25",
  cloud: "M4.75 12.25H11.25A2.5 2.5 0 0 0 11.75 7.25A4 4 0 0 0 4.25 5.75A3.25 3.25 0 0 0 4.75 12.25Z",
  send: "M13.25 2.25 9.25 13.25 7.25 8.25 2.25 6.25ZM7.25 8.25 13.25 2.25",
  stop: "M5.25 3.75H10.25A1.5 1.5 0 0 1 11.75 5.25V10.25A1.5 1.5 0 0 1 10.25 11.75H5.25A1.5 1.5 0 0 1 3.75 10.25V5.25A1.5 1.5 0 0 1 5.25 3.75Z",
  bot: "M4.75 5.75H10.75A1.5 1.5 0 0 1 12.25 7.25V11.25A1.5 1.5 0 0 1 10.75 12.75H4.75A1.5 1.5 0 0 1 3.25 11.25V7.25A1.5 1.5 0 0 1 4.75 5.75ZM7.75 5.75V2.75M6.75 2.75H8.75M5.75 8.75V9.25M9.75 8.75V9.25M1.75 8.25V10.25M13.75 8.25V10.25",
  pencil: "M9.75 2.75 12.75 5.75 5.25 13.25H2.25V10.25ZM8.25 4.25 11.25 7.25",
  steps: "M3.35 3.75A.6 .6 0 1 1 2.15 3.75A.6 .6 0 1 1 3.35 3.75ZM3.35 7.75A.6 .6 0 1 1 2.15 7.75A.6 .6 0 1 1 3.35 7.75ZM3.35 11.75A.6 .6 0 1 1 2.15 11.75A.6 .6 0 1 1 3.35 11.75ZM6.25 3.75H12.75M6.25 7.75H12.75M6.25 11.75H12.75",
  plug: "M5.25 2.25V5.25M10.25 2.25V5.25M3.25 5.25H12.25M4.25 5.25V7.75A3.5 3.5 0 0 0 11.25 7.75V5.25M7.75 11.25V13.75",
  bell: "M3.25 11.25 4.25 9.75V6.25A3.5 3.5 0 0 1 11.25 6.25V9.75L12.25 11.25ZM6.25 13.25H9.25M7.75 2.75V1.75",
  calendar: "M3.75 3.75H11.75A1.5 1.5 0 0 1 13.25 5.25V11.75A1.5 1.5 0 0 1 11.75 13.25H3.75A1.5 1.5 0 0 1 2.25 11.75V5.25A1.5 1.5 0 0 1 3.75 3.75ZM5.25 2.25V5.25M10.25 2.25V5.25M2.25 7.25H13.25M5.25 10.25H6.25M9.25 10.25H10.25",
  news: "M2.25 3.25H8.75A1.5 1.5 0 0 1 10.25 4.75V11.25A1.5 1.5 0 0 0 13.25 11.25V5.75H10.25M2.25 3.25V11.25A1.5 1.5 0 0 0 3.75 12.75H11.75M4.75 6.25H7.25M4.75 9.25H7.25",
  folder: "M2.25 4.25A1.5 1.5 0 0 1 3.75 2.75H6.25L8.25 4.75H11.75A1.5 1.5 0 0 1 13.25 6.25V11.25A1.5 1.5 0 0 1 11.75 12.75H3.75A1.5 1.5 0 0 1 2.25 11.25ZM2.25 6.75H13.25",
  memory: "M4.75 2.75H10.75A1.5 1.5 0 0 1 12.25 4.25V13.25H4.75A1.5 1.5 0 0 1 3.25 11.75V4.25A1.5 1.5 0 0 1 4.75 2.75ZM3.25 11.75A1.5 1.5 0 0 1 4.75 10.25H12.25M6.25 2.75V7.25L7.75 5.75 9.25 7.25V2.75",
  attach: "M12.75 7.25 7.25 12.75A3.5 3.5 0 0 1 2.3 7.8L7.8 2.3A2.25 2.25 0 0 1 10.98 5.48L5.48 10.98A1 1 0 0 1 4.07 9.57L9.25 4.39",
  voice: "M2.75 6.25V9.25M5.25 3.75V11.75M7.75 2.25V13.25M10.25 4.75V10.75M12.75 6.25V9.25",
  database: "M12.75 4.25A5 2 0 1 1 2.75 4.25A5 2 0 1 1 12.75 4.25ZM2.75 4.25V11.25A5 2 0 0 0 12.75 11.25V4.25M2.75 7.75A5 2 0 0 0 12.75 7.75",
  play: "M4.75 2.75 12.75 7.75 4.75 12.75Z",
  pause: "M4.75 3.25V12.25M10.75 3.25V12.25",
  external: "M7.25 2.75H3.75A1.5 1.5 0 0 0 2.25 4.25V11.75A1.5 1.5 0 0 0 3.75 13.25H11.25A1.5 1.5 0 0 0 12.75 11.75V8.25M8.75 2.25H13.25V6.75M13.25 2.25 7.25 8.25",
  minus: "M3.25 7.75H12.25",
  image: "M3.75 2.75H11.75A1.5 1.5 0 0 1 13.25 4.25V11.25A1.5 1.5 0 0 1 11.75 12.75H3.75A1.5 1.5 0 0 1 2.25 11.25V4.25A1.5 1.5 0 0 1 3.75 2.75ZM2.25 10.25 5.75 6.75 9.75 10.75M8.75 9.75 10.25 8.25 13.25 11.25M10.35 5.75A.6 .6 0 1 1 9.15 5.75A.6 .6 0 1 1 10.35 5.75Z",
  file: "M4.25 1.75H9.25L12.25 4.75V12.75A1.5 1.5 0 0 1 10.75 14.25H4.25A1.5 1.5 0 0 1 2.75 12.75V3.25A1.5 1.5 0 0 1 4.25 1.75ZM9.25 1.75V4.75H12.25",
  more: "M3.85 7.75A.6 .6 0 1 1 2.65 7.75A.6 .6 0 1 1 3.85 7.75ZM8.35 7.75A.6 .6 0 1 1 7.15 7.75A.6 .6 0 1 1 8.35 7.75ZM12.85 7.75A.6 .6 0 1 1 11.65 7.75A.6 .6 0 1 1 12.85 7.75Z",
  repeat: "M10.75 1.75 12.75 3.75 10.75 5.75M2.75 7.25V6.25A2.5 2.5 0 0 1 5.25 3.75H12.75M4.75 13.75 2.75 11.75 4.75 9.75M12.75 8.25V9.25A2.5 2.5 0 0 1 10.25 11.75H2.75",
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

export interface TabItem {
  id: string;
  label: string;
  count?: number;
  /** Extra attributes for the tab's button, with a leading space. */
  attrs?: string;
}

/**
 * The one tab / segmented component. `nav` switches between views (bordered, joined, role=tablist);
 * `filter` narrows one list (a pill; History and Dictionary only; role=group, aria-pressed).
 * `fill` stretches the nav tabs across the width of their container. `plain` draws the nav tabs as
 * quiet words with a soft fill under the one that is open, for a bar that also holds a search (the
 * 「已安裝」 and 「商店」 of 連接): still tabs, with the same roles and keys.
 */
export function tabs(items: TabItem[], selected: string, o: { label: string; kind: "nav" | "filter"; fill?: boolean; plain?: boolean }): string {
  const nav = o.kind === "nav";
  const one = (it: TabItem): string => {
    const on = it.id === selected;
    const state = nav ? `role="tab" aria-selected="${on}"` : `aria-pressed="${on}"`;
    const n = it.count !== undefined && (it.count > 0 || o.plain) ? `<span class="n">${it.count}</span>` : "";
    return `<button type="button" class="tab" ${state}${it.attrs ?? ""}>${esc(it.label)}${n}</button>`;
  };
  const cls = nav ? `tabs ${o.plain ? "plain" : "switch"}${o.fill ? " fill" : ""}` : "tabs seg";
  return `<div class="${cls}" role="${nav ? "tablist" : "group"}" aria-label="${esc(o.label)}">${items.map(one).join("")}</div>`;
}

/** How long 「中斷連線」 (of a connection or a messaging app) waits for its second press. */
export const DISCONNECT_CONFIRM_MS = 5000;

export const btn = (label: string, kind = "", attrs = ""): string =>
  `<button type="button" class="btn${kind ? " " + kind : ""}"${attrs}>${label}</button>`;

/**
 * A button with an icon only (trash, ×, chevron, search, settings, sliders): what it does is its
 * label and its tooltip. It has no border, only a fill under the pointer and the focus ring
 * (styles.css `.btn.icon`): a border is what tells a button with words in it.
 */
export const iconButton = (name: IconName, label: string, attrs = "", cls = ""): string =>
  `<button type="button" class="btn icon${cls ? " " + cls : ""}" aria-label="${esc(label)}" title="${esc(label)}"${attrs}>${ico(name)}</button>`;

/** The small icon button on a row (copy, delete, close): `iconButton` at the row's size, borderless too. */
export const iconBtn = (name: IconName, label: string, attrs = "", cls = ""): string => iconButton(name, label, attrs, `row${cls ? " " + cls : ""}`);

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

// ---------- motion ----------
// The design's table of motion: a page that changes fades in and rises 6 px (180 ms), the rows of a
// list that is new come in one after another (25 ms apart, 300 ms at most), a dialog grows in from
// 96 % (200 ms) and goes the same way out (140 ms), a switch slides and tints its row (160 ms), a
// toast slides in from the corner with a bar that counts down. Only where something changes; the
// keyframes are in styles.css, and under `prefers-reduced-motion` nothing moves (the rule at the end
// of styles.css), and nothing waits for an animation that does not play.

/** Whether Windows asks for less motion. */
export const reducedMotion = (): boolean =>
  typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;

/**
 * Plays the way out of `el` (its `leaving` class, which styles.css animates over `ms`) and then calls
 * `done`, at once when nothing may move. Returns what stops the way out and leaves `el` as it was.
 */
export function leave(el: HTMLElement | null, ms: number, done: () => void): () => void {
  if (!el || reducedMotion()) {
    done();
    return () => {};
  }
  el.classList.add("leaving");
  const timer = setTimeout(() => {
    el.classList.remove("leaving");
    done();
  }, ms);
  return () => {
    clearTimeout(timer);
    el.classList.remove("leaving");
  };
}

/** Plays a one-off animation class on `el` again from its start (a page change, say). */
export function replay(el: HTMLElement, cls: string): void {
  el.classList.remove(cls);
  void el.offsetWidth; // the class goes, so that its animation starts over when it comes back
  el.classList.add(cls);
}

/**
 * Puts `html` into `host` in place of what it holds (as `innerHTML` does) and lets only what changed
 * move, since a page is drawn again whole after every change:
 * - a list marked `data-stagger="<key>"` has its rows come in one after another when it is new; the
 *   same list drawn again (the same key) is marked `again` and stands still;
 * - a dialog marked `data-dialog="<key>"` grows in when it opens, not each time it is drawn again;
 * - a switch whose state changed since the last drawing slides to it (`flip`) and tints its row for a
 *   moment (`tinted`); one drawn in the state it had stands still. Switches are told apart by their
 *   `aria-label`, which names what each one switches.
 */
export function swapHTML(host: HTMLElement, html: string): void {
  const keys = (attr: string): Set<string> =>
    new Set(Array.from(host.querySelectorAll<HTMLElement>(`[${attr}]`), (node) => node.getAttribute(attr) ?? ""));
  const lists = keys("data-stagger");
  const dialogs = keys("data-dialog");
  const switches = new Map(Array.from(host.querySelectorAll<HTMLElement>(".tg"), (s) => [s.getAttribute("aria-label") ?? "", s.getAttribute("aria-checked")]));
  host.innerHTML = html;
  for (const [attr, seen] of [["data-stagger", lists], ["data-dialog", dialogs]] as const) {
    host.querySelectorAll<HTMLElement>(`[${attr}]`).forEach((node) => {
      if (seen.has(node.getAttribute(attr) ?? "")) node.classList.add("again");
    });
  }
  host.querySelectorAll<HTMLElement>(".tg").forEach((s) => {
    const before = switches.get(s.getAttribute("aria-label") ?? "");
    if (before === undefined || before === s.getAttribute("aria-checked")) return;
    s.classList.add("flip");
    s.closest(".row, .cs-item")?.classList.add("tinted");
  });
}

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

/** The check mark of a chosen item: drawn, so that it is the same in every font; the `check` of ICON,
 * drawn at 16 px by `.menu .ck svg`. */
export const CHECK = `<svg viewBox="0 0 16 16" aria-hidden="true"><path d="${ICON.check}"/></svg>`;

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

  /**
   * Shows `message`. With `undo` the toast stays until `hide()` (the 10 seconds of `Undoable`) and has
   * a 「復原」 button; without, it goes by itself. Either way a thin bar along its foot counts down the
   * time it has left (styles.css), so that its going is no surprise.
   */
  show(message: string, undo?: () => void): void {
    const toast = document.createElement("div");
    toast.className = "toast";
    toast.setAttribute("role", "status");
    toast.setAttribute("aria-atomic", "true");
    const text = document.createElement("span");
    this.messages.set(toast, translatedMessage(message));
    text.textContent = message;
    toast.append(text);
    const ms = undo ? UNDO_MS : infoMs(message);
    if (undo) {
      if (this.undoToast) this.messages.delete(this.undoToast);
      this.undoToast?.remove();
      this.undoToast = toast;
      toast.insertAdjacentHTML("beforeend", btn(t("ui.001"), "sm"));
      toast.querySelector("button")?.addEventListener("click", undo);
    } else {
      setTimeout(() => { toast.remove(); this.messages.delete(toast); }, ms);
    }
    const bar = document.createElement("i");
    bar.className = "toast-bar";
    bar.setAttribute("aria-hidden", "true");
    bar.style.animationDuration = `${ms}ms`;
    toast.append(bar);
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
