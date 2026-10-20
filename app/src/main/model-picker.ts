// The model picker: a button that shows the model chosen for one of Raphael's roles and its price,
// and a panel to choose another from every model OpenRouter offers (`models.catalog`). The panel
// has a search field, the order and the maker as two small dropdowns, and the models of the role's
// kind: those the role can use first, then the others, greyed, with the reason in plain words.
// Keys: the arrows and Enter in the list, typing searches, Tab goes between the search and the two
// dropdowns, Esc closes. The panel is the app's popover (ui.ts), so it closes the way menus do.
//
// The settings page draws its rows as HTML, and draws them again often: a row holds a slot
// (`modelPickerSlot`) and the page mounts a picker in it after each drawing. A list that was open
// stays open on the new button, with what was being searched.

import "./model-picker.css";
import { t } from "./i18n";
import { formatPerMinute, formatPerMtok, pricingNote } from "./pricing";
import type { CatalogModel, ModelCatalog, ModelRole } from "./types";
import { CHECK, closeMenu, esc, ico, menuIsOpenFor, openPopover } from "./ui";

// ---------- what the list shows ----------

/** How the list is ordered. */
export type PickerSort = "price" | "name" | "newest";

/** What the list is narrowed to and how it is ordered. */
export interface PickerState {
  query: string;
  sort: PickerSort;
  /** A maker, or "" for all of them. */
  provider: string;
}

export const DEFAULT_PICKER_STATE: PickerState = { query: "", sort: "price", provider: "" };

/** The kind of model each role chooses among. */
const KIND: Record<ModelRole, "llm" | "stt"> = { cleanup: "llm", answer: "llm", stt: "stt" };

/** The models of the catalog that `role` chooses among: those of its kind. */
export const modelsFor = (catalog: ModelCatalog, role: ModelRole): CatalogModel[] => catalog[KIND[role]];

/** Whether `role` can use `model`. */
export const usableFor = (model: CatalogModel, role: ModelRole): boolean => model.roles[role]?.usable === true;

// Names sort as people read them: "GPT-5" before "GPT-10", whatever the case.
const collator = new Intl.Collator("en", { numeric: true, sensitivity: "base" });

const perMtok = (n: number | null): string => (n === null ? "?" : formatPerMtok(n));
const tokenPair = (m: CatalogModel): string => `${perMtok(m.input_per_mtok)} / ${perMtok(m.output_per_mtok)}`;
const hasTokenPrice = (m: CatalogModel): boolean => m.input_per_mtok !== null || m.output_per_mtok !== null;

/**
 * The price of a model as a row shows it: a language model's `$0.10 / $0.50` (per million tokens in
 * and out, which the list's heading says once), a speech model's `每分鐘 $0.0018` or, for one priced
 * by the token, `$2.00 / $12 每百萬 token`. Else 免費, 價格不固定 (a router) or 價格未知.
 */
export function priceText(model: CatalogModel, role: ModelRole): string {
  if (model.variable_price) return t("picker.varies");
  if (model.free) return t("picker.free");
  if (model.per_minute !== null) return t("pricing.003", { p0: formatPerMinute(model.per_minute) });
  if (!hasTokenPrice(model)) return t("pricing.001");
  return KIND[role] === "llm" ? tokenPair(model) : t("pricing.004", { p0: tokenPair(model) });
}

/** The price in full words, for a screen reader: 「輸入 $0.10 / 輸出 $0.50 每百萬 token」. */
export function priceDescription(model: CatalogModel, role: ModelRole): string {
  if (KIND[role] === "llm" && !model.variable_price && !model.free && hasTokenPrice(model)) {
    return t("pricing.002", { p0: perMtok(model.input_per_mtok), p1: perMtok(model.output_per_mtok) });
  }
  return priceText(model, role);
}

/** A context length in tokens, short: 1050000 -> `1.05M`, 131072 -> `131K`. */
export function formatContext(tokens: number): string {
  if (tokens >= 1_000_000) return `${Number((tokens / 1_000_000).toPrecision(3))}M`;
  if (tokens >= 1000) return `${Math.round(tokens / 1000)}K`;
  return String(tokens);
}

/** The day OpenRouter added a model: 「2026 年 10 月 18 日上架」. */
function addedText(ms: number): string {
  const d = new Date(ms);
  const day = t("format.003", { p0: d.getMonth() + 1, p1: d.getDate() });
  return t("picker.added", { p0: t("format.004", { p0: d.getFullYear(), p1: day }) });
}

/** The plain words for each reason the core gives why a role cannot use a model (brain/catalog.py). */
const REASONS = new Map<string, () => string>([
  ["batch_only", () => t("picker.reasonBatch")],
  ["no_structured_output", () => t("picker.reasonJson")],
  ["reasoning_required", () => t("picker.reasonReasoning")],
  ["no_reasoning_setting", () => t("picker.reasonReasoningSetting")],
  ["no_max_tokens", () => t("picker.reasonMaxTokens")],
]);

/** Why a role cannot use a model, in plain words; a reason this app does not know yet still has some. */
export const reasonText = (reason: string | null | undefined): string =>
  (reason ? REASONS.get(reason)?.() : undefined) ?? t("picker.reasonOther");

/** The heading over the models a role cannot use. */
const UNUSABLE: Record<ModelRole, () => string> = {
  cleanup: () => t("picker.unusableCleanup"),
  answer: () => t("picker.unusableAnswer"),
  stt: () => t("picker.unusableStt"),
};

/** A row of the list: a model, and whether the role can use it. */
export interface PickerRow {
  model: CatalogModel;
  usable: boolean;
}

const squeeze = (text: string): string => text.replace(/[^\p{L}\p{N}]+/gu, "");

/**
 * Whether a model answers a search: each of its words is in the model's name, maker or id, whatever
 * the case, also when the spaces and the punctuation in between are left out ("gpt6" finds GPT-6).
 */
function matcher(query: string): (model: CatalogModel) => boolean {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  return (model) => {
    const text = `${model.name} ${model.provider} ${model.id}`.toLowerCase();
    const packed = squeeze(text);
    return words.every((word) => text.includes(word) || (squeeze(word) !== "" && packed.includes(squeeze(word))));
  };
}

/**
 * What a model sorts by when the order is the price, cheapest first: free and fixed prices, a speech
 * model priced by the minute before one priced by the token (the two cannot be compared), then the
 * routers, whose price varies, and last the models whose price is unknown.
 */
function priceKey(m: CatalogModel): [number, number, number] {
  if (m.variable_price) return [2, 0, 0];
  if (m.free) return [0, 0, 0];
  if (m.per_minute !== null) return [0, m.per_minute, 0];
  if (hasTokenPrice(m)) return [1, m.input_per_mtok ?? Infinity, m.output_per_mtok ?? Infinity];
  return [3, 0, 0];
}

const byName = (a: CatalogModel, b: CatalogModel): number =>
  collator.compare(a.name, b.name) || collator.compare(a.provider, b.provider) || collator.compare(a.id, b.id);

function byPrice(a: CatalogModel, b: CatalogModel): number {
  const x = priceKey(a);
  const y = priceKey(b);
  return x[0] - y[0] || x[1] - y[1] || x[2] - y[2] || byName(a, b);
}

/** The newest first; a model without a date last. */
const byDate = (a: CatalogModel, b: CatalogModel): number =>
  (b.created_at_ms ?? -Infinity) - (a.created_at_ms ?? -Infinity) || byName(a, b);

const ORDERS: Record<PickerSort, (a: CatalogModel, b: CatalogModel) => number> = { price: byPrice, name: byName, newest: byDate };

/** The rows of the list: the models that answer the search and are of the maker, in the order chosen, those the role can use first. */
export function pickerRows(models: CatalogModel[], role: ModelRole, state: PickerState): PickerRow[] {
  const matches = matcher(state.query);
  const rows = models
    .filter((model) => (state.provider === "" || model.provider === state.provider) && matches(model))
    .sort(ORDERS[state.sort])
    .map((model) => ({ model, usable: usableFor(model, role) }));
  return [...rows.filter((row) => row.usable), ...rows.filter((row) => !row.usable)];
}

export interface PickerChoice {
  value: string;
  label: string;
  /** How many models there are of it: a maker's. */
  detail?: string;
}

/** The orders the list can be in. */
export const sortChoices = (): PickerChoice[] => [
  { value: "price", label: t("picker.sortPrice") },
  { value: "name", label: t("picker.sortName") },
  { value: "newest", label: t("picker.sortNewest") },
];

/** The makers of `models` by name, each with how many models it has, after 「所有供應商」 (""). */
export function providerChoices(models: CatalogModel[]): PickerChoice[] {
  const counts = new Map<string, number>();
  for (const model of models) counts.set(model.provider, (counts.get(model.provider) ?? 0) + 1);
  const makers = [...counts.keys()].sort(collator.compare);
  return [
    { value: "", label: t("picker.allProviders"), detail: String(models.length) },
    ...makers.map((maker) => ({ value: maker, label: maker, detail: String(counts.get(maker)) })),
  ];
}

/** Rows that Page Up and Page Down move by. */
const PAGE = 10;

/**
 * Where the highlight goes in a list of `count` rows from `index` (-1: none) for `key`. The arrows go
 * round, Page Up and Page Down go ten rows and stop at the ends, Home and End go to them. Null for
 * any other key, and for an empty list.
 */
export function moveIndex(count: number, index: number, key: string): number | null {
  if (count === 0) return null;
  switch (key) {
    case "ArrowDown":
      return index < 0 ? 0 : (index + 1) % count;
    case "ArrowUp":
      return index < 0 ? count - 1 : (index - 1 + count) % count;
    case "PageDown":
      return Math.min(count - 1, Math.max(index, 0) + PAGE);
    case "PageUp":
      return Math.max(0, index - PAGE);
    case "Home":
      return 0;
    case "End":
      return count - 1;
    default:
      return null;
  }
}

/** A picker's list without its page: the rows, and the highlighted one, as the search, the order, the maker and the keys change them. */
export class PickerList {
  state: PickerState;
  rows: PickerRow[] = [];
  /** The highlighted row, or -1 when there is none. */
  active = -1;

  constructor(
    readonly role: ModelRole,
    private models: CatalogModel[],
    /** The model chosen now: checked, and highlighted when the list opens. */
    public current: string,
    state: Partial<PickerState> = {},
  ) {
    this.state = { ...DEFAULT_PICKER_STATE, ...state };
    this.refresh(current);
  }

  /** Shows these models (a newer catalog), keeping the search and the highlighted model. */
  setModels(models: CatalogModel[]): void {
    this.models = models;
    this.refresh(this.activeId() ?? this.current);
  }

  /** A new search highlights the first row; none at all, the chosen model again. */
  setQuery(query: string): void {
    this.state = { ...this.state, query };
    this.refresh(query.trim() === "" ? this.current : null);
  }

  setSort(sort: PickerSort): void {
    this.state = { ...this.state, sort };
    this.refresh(this.activeId());
  }

  setProvider(provider: string): void {
    this.state = { ...this.state, provider };
    this.refresh(this.activeId());
  }

  /** The makers to narrow the list to. */
  providers(): PickerChoice[] {
    return providerChoices(this.models);
  }

  activeId(): string | null {
    return this.rows[this.active]?.model.id ?? null;
  }

  /** Moves the highlight for `key` (`moveIndex`); whether the key moves it. A row the role cannot use is highlighted too, so that its reason is read out. */
  move(key: string): boolean {
    const next = moveIndex(this.rows.length, this.active, key);
    if (next === null) return false;
    this.active = next;
    return true;
  }

  /** Highlights the row of `id`: the pointer is on it. */
  point(id: string): void {
    const at = this.rows.findIndex((row) => row.model.id === id);
    if (at >= 0) this.active = at;
  }

  /** The model Enter chooses: the highlighted one, if the role can use it. */
  chosen(): string | null {
    const row = this.rows[this.active];
    return row?.usable ? row.model.id : null;
  }

  private refresh(keep: string | null): void {
    this.rows = pickerRows(this.models, this.role, this.state);
    const kept = keep === null ? -1 : this.rows.findIndex((row) => row.model.id === keep);
    this.active = kept >= 0 ? kept : this.rows.length > 0 ? 0 : -1;
  }
}

// ---------- the HTML ----------

/** One row: the name, the price, and a line with the maker, the context or the dictionary, the date when the list is newest first, and why the role cannot use it. */
function optionHTML(row: PickerRow, index: number, list: PickerList): string {
  const { model } = row;
  const chosen = model.id === list.current;
  const details = [model.provider];
  if (model.context_length !== null) details.push(t("picker.context", { p0: formatContext(model.context_length) }));
  if (model.capabilities.dictionary_hints) details.push(t("picker.dictionary"));
  if (list.state.sort === "newest" && model.created_at_ms !== null) details.push(addedText(model.created_at_ms));
  const reason = row.usable ? "" : reasonText(model.roles[list.role]?.reason);
  if (reason) details.push(reason);
  // What a screen reader says of the row, in an order that makes sense when heard.
  const heard = [model.name, model.provider, priceDescription(model, list.role)];
  if (model.recommended) heard.push(t("picker.recommended"));
  if (chosen) heard.push(t("picker.current"));
  if (reason) heard.push(reason);
  return (
    `<button type="button" class="mp-opt" role="option" id="mp-o-${index}" tabindex="-1" data-id="${esc(model.id)}" aria-selected="false"` +
    `${row.usable ? "" : ' aria-disabled="true"'}${chosen ? " data-current" : ""} aria-label="${esc(heard.join(", "))}" title="${esc(model.id)}">` +
    `<span class="ck">${chosen ? CHECK : ""}</span>` +
    `<span class="mp-main"><span class="mp-line"><span class="mp-name">${esc(model.name)}</span>` +
    (model.recommended ? `<span class="mp-badge">${esc(t("picker.recommended"))}</span>` : "") +
    `<span class="mp-price num">${esc(priceText(model, list.role))}</span></span>` +
    `<span class="mp-sub">${esc(details.join(" · "))}</span></span></button>`
  );
}

/** The rows of the list, with a heading over those the role cannot use. */
export function listHTML(list: PickerList): string {
  const first = list.rows.findIndex((row) => !row.usable);
  return list.rows
    .map((row, index) =>
      (index === first ? `<div class="mp-group" aria-hidden="true">${esc(UNUSABLE[list.role]())}</div>` : "") + optionHTML(row, index, list),
    )
    .join("");
}

function dropdownButtonHTML(which: "sort" | "provider", label: string, shown: string): string {
  return (
    `<button type="button" class="mp-dd" data-dd="${which}" aria-haspopup="listbox" aria-expanded="false" aria-label="${esc(`${label}: ${shown}`)}">` +
    `<span>${esc(shown)}</span>${ico("down")}</button>`
  );
}

function dropdownHTML(which: "sort" | "provider", label: string, choices: PickerChoice[], chosen: string): string {
  return (
    `<div class="mp-ddlist" role="listbox" id="mp-dd-${which}" aria-label="${esc(label)}">` +
    choices
      .map(
        (c) =>
          `<button type="button" role="option" tabindex="-1" data-value="${esc(c.value)}" aria-selected="${c.value === chosen}">` +
          `<span class="ck">${c.value === chosen ? CHECK : ""}</span><span class="mt">${esc(c.label)}</span>` +
          (c.detail ? `<span class="md num">${esc(c.detail)}</span>` : "") +
          `</button>`,
      )
      .join("") +
    `</div>`
  );
}

const sortLabel = (sort: PickerSort): string => sortChoices().find((c) => c.value === sort)?.label ?? sort;
const providerLabel = (provider: string): string => provider || t("picker.allProviders");

/** The panel, empty: its list, its line under the list and its small print are filled in apart. */
function panelHTML(list: PickerList, label: string): string {
  const search = t("picker.search");
  return (
    `<div class="mp-head">` +
    `<label class="field mp-search">${ico("search")}<input id="mp-search" type="text" role="combobox" aria-autocomplete="list" aria-expanded="true" ` +
    `aria-controls="mp-list" aria-label="${esc(search)}" placeholder="${esc(search)}" autocomplete="off" spellcheck="false"></label>` +
    `<div class="mp-filters">` +
    dropdownButtonHTML("sort", t("picker.sort"), sortLabel(list.state.sort)) +
    dropdownButtonHTML("provider", t("picker.provider"), providerLabel(list.state.provider)) +
    `</div></div>` +
    `<div class="mp-cols" aria-hidden="true"><span>${esc(t("picker.model"))}</span>` +
    `<span>${esc(KIND[list.role] === "llm" ? t("picker.tokenPrices") : t("picker.prices"))}</span></div>` +
    `<div class="mp-scroll"><div class="mp-list" id="mp-list" role="listbox" aria-label="${esc(label)}"></div>` +
    `<p class="mp-empty" role="status" hidden></p></div>` +
    `<p class="mp-foot"></p>`
  );
}

/** The small print under the list: how many models it shows, and where their prices are from. */
export function footText(catalog: ModelCatalog | null, shown: number, nowMs = Date.now()): string {
  if (!catalog) return "";
  const where = (catalog.source === "builtin" ? `${t("picker.builtin")} ` : "") + pricingNote(catalog, nowMs);
  return `${t("picker.count", { p0: shown })} · ${where}`;
}

/** What to tell the user of each chosen model that OpenRouter no longer lists and that went back to its default (`replaced`). */
export function replacementNotices(catalog: ModelCatalog): string[] {
  const role = { cleanup: "picker.roleCleanup", answer: "picker.roleAnswer", stt: "picker.roleStt" } as const;
  return catalog.replaced.map((r) => {
    const fallback = modelsFor(catalog, r.role).find((m) => m.id === r.default);
    return t("picker.replaced", { p0: r.model, p1: t(role[r.role]), p2: fallback?.name ?? r.default });
  });
}

/** The name of a model the catalog does not have (yet): the last part of its id. */
const shortName = (id: string): string => id.slice(id.lastIndexOf("/") + 1) || id;

// ---------- on the page ----------

export interface ModelPickerOptions {
  /** What the model is for. */
  role: ModelRole;
  /** The id of the model chosen now. */
  current: string;
  /** The catalog (`models.catalog`), or null while it is being read (`loading`) or when it cannot be had. */
  catalog: ModelCatalog | null;
  loading?: boolean;
  /** What the control is called: the title of its row. It names the button and the list for a screen reader. */
  label: string;
  /** The button's `data-k`: a page that draws itself again gives the focus back by it (settings.ts does). */
  key?: string;
  /** No choosing: the brain's settings are not known. */
  disabled?: boolean;
  /** The user chose `id`: never the model chosen now, never one the role cannot use. The page saves it. */
  onChoose: (id: string) => void;
}

export interface ModelPicker {
  /** Shows another model as chosen, a newer catalog, another label or state. An open list follows and keeps its search. */
  update(changes: Partial<Omit<ModelPickerOptions, "role" | "onChoose">>): void;
  /** Opens the list, with `query` in the search if one is given. */
  open(query?: string): void;
  close(): void;
  readonly isOpen: boolean;
  /** The button that opens the list. */
  readonly button: HTMLButtonElement;
  /** Takes the picker off the page (its list closes). */
  destroy(): void;
}

/** The place for a picker in a row of the page; `mountModelPicker` puts the picker in it. */
export const modelPickerSlot = (role: ModelRole): string => `<span class="mp-slot" data-model-picker="${role}"></span>`;

/** Puts a picker into `container`, which it fills. */
export function mountModelPicker(container: HTMLElement, options: ModelPickerOptions): ModelPicker {
  return new MountedPicker(container, options);
}

/** A key that types something: a letter, a digit, a mark (Space is no such key: it presses buttons). */
const typing = (e: KeyboardEvent): boolean => e.key.length === 1 && e.key !== " " && !e.ctrlKey && !e.metaKey && !e.altKey;

/** The picker whose list is open: a page drawn again hands that list to the new button of the role. */
let opened: { picker: MountedPicker; role: ModelRole; button: HTMLElement } | null = null;

interface Dropdown {
  which: "sort" | "provider";
  el: HTMLElement;
  button: HTMLElement;
}

class MountedPicker implements ModelPicker {
  readonly button: HTMLButtonElement;
  private o: ModelPickerOptions;
  private list: PickerList | null = null;
  private panel: HTMLElement | null = null;
  private dropdown: Dropdown | null = null;
  /** The row drawn as highlighted. */
  private lit: HTMLElement | null = null;

  constructor(container: HTMLElement, options: ModelPickerOptions) {
    this.o = { ...options };
    container.innerHTML = `<button type="button" class="mp-trigger" aria-haspopup="dialog" aria-expanded="false"></button>`;
    this.button = container.querySelector<HTMLButtonElement>(".mp-trigger")!;
    if (options.key) this.button.dataset.k = options.key;
    this.paintButton();
    this.button.addEventListener("click", () => (this.isOpen ? this.close() : this.open()));
    this.button.addEventListener("keydown", (e) => this.buttonKey(e));
    this.takeOver();
  }

  get isOpen(): boolean {
    return this.panel !== null && menuIsOpenFor(this.button);
  }

  update(changes: Partial<Omit<ModelPickerOptions, "role" | "onChoose">>): void {
    this.o = { ...this.o, ...changes };
    this.paintButton();
    if (!this.isOpen || !this.list) return;
    if (this.o.disabled) {
      this.close();
      return;
    }
    this.list.current = this.o.current;
    this.list.setModels(this.models());
    this.paintFilters();
    this.paintList(null);
  }

  open(query = ""): void {
    this.show(new PickerList(this.o.role, this.models(), this.o.current, { query }), {});
  }

  close(): void {
    const had = this.panel !== null;
    this.forget();
    if (menuIsOpenFor(this.button)) closeMenu();
    if (had && this.button.isConnected) this.button.focus();
  }

  destroy(): void {
    if (this.isOpen) closeMenu();
    this.forget();
    this.button.remove();
  }

  // ---------- drawing ----------

  private models(): CatalogModel[] {
    return this.o.catalog ? modelsFor(this.o.catalog, this.o.role) : [];
  }

  private paintButton(): void {
    const model = this.models().find((m) => m.id === this.o.current);
    const name = model?.name ?? shortName(this.o.current);
    const price = model ? priceText(model, this.o.role) : this.o.loading ? "…" : "";
    this.button.innerHTML =
      `<span class="mp-tname">${esc(name)}</span>` + (price ? `<span class="mp-tprice num">${esc(price)}</span>` : "") + ico("down");
    this.button.disabled = this.o.disabled === true;
    this.button.title = model ? `${model.provider}: ${model.name}` : this.o.current;
    this.button.setAttribute("aria-label", `${this.o.label}: ${name}${model ? `, ${priceDescription(model, this.o.role)}` : ""}`);
  }

  private search(): HTMLInputElement | null {
    return this.panel?.querySelector<HTMLInputElement>("#mp-search") ?? null;
  }

  private show(list: PickerList, restore: { caret?: number; scroll?: number }): void {
    if (this.button.disabled) return;
    this.list = list;
    const panel = openPopover(this.button, panelHTML(list, this.o.label), {
      className: "menu mp-panel",
      role: "dialog",
      label: this.o.label,
    });
    this.panel = panel;
    opened = { picker: this, role: this.o.role, button: this.button };
    panel.addEventListener("keydown", (e) => this.panelKey(e));
    panel.addEventListener("input", (e) => this.searched(e));
    panel.addEventListener("click", (e) => this.clicked(e));
    // A click on a row leaves the focus in the search, where the keys work.
    panel.addEventListener("mousedown", (e) => {
      if ((e.target as Element).closest(".mp-opt")) e.preventDefault();
    });
    panel.addEventListener("pointermove", (e) => this.pointed(e));
    const input = this.search()!;
    input.value = list.state.query;
    this.paintList("center");
    if (restore.scroll !== undefined) panel.querySelector(".mp-scroll")!.scrollTop = restore.scroll;
    input.focus({ preventScroll: true });
    const caret = Math.min(restore.caret ?? input.value.length, input.value.length);
    input.setSelectionRange(caret, caret);
  }

  /** The page was drawn again while this role's list was open on the button this one replaces: the list opens here, as it was. */
  private takeOver(): void {
    const before = opened;
    if (!before || before.role !== this.o.role || before.button.isConnected || !menuIsOpenFor(before.button)) return;
    const { list, panel } = before.picker;
    const caret = before.picker.search()?.selectionStart ?? undefined;
    const scroll = panel?.querySelector(".mp-scroll")?.scrollTop;
    before.picker.forget();
    this.show(new PickerList(this.o.role, this.models(), this.o.current, list?.state), { caret, scroll });
  }

  /** Lets go of the panel without touching it: the popover closed, or a new button took the list over. */
  private forget(): void {
    this.panel = null;
    this.list = null;
    this.dropdown = null;
    this.lit = null;
    if (opened?.picker === this) opened = null;
  }

  private paintFilters(): void {
    if (!this.panel || !this.list) return;
    const shown = { sort: sortLabel(this.list.state.sort), provider: providerLabel(this.list.state.provider) };
    const labels = { sort: t("picker.sort"), provider: t("picker.provider") };
    for (const which of ["sort", "provider"] as const) {
      const button = this.panel.querySelector<HTMLElement>(`.mp-dd[data-dd="${which}"]`)!;
      button.querySelector("span")!.textContent = shown[which];
      button.setAttribute("aria-label", `${labels[which]}: ${shown[which]}`);
    }
  }

  /** Draws the rows again; `scroll` says where the list goes: to the highlighted row (in the middle, or just into sight), to the top, or nowhere. */
  private paintList(scroll: "center" | "nearest" | "top" | null): void {
    if (!this.panel || !this.list) return;
    const list = this.list;
    this.panel.querySelector(".mp-list")!.innerHTML = listHTML(list);
    const empty = this.panel.querySelector<HTMLElement>(".mp-empty")!;
    const missing = this.o.loading ? t("common.loading") : t("picker.failed");
    empty.textContent = list.rows.length > 0 ? "" : this.o.catalog ? t("picker.empty") : missing;
    empty.hidden = list.rows.length > 0;
    this.panel.querySelector(".mp-foot")!.textContent = footText(this.o.catalog, list.rows.length);
    this.lit = null;
    if (scroll === "top") this.panel.querySelector(".mp-scroll")!.scrollTop = 0;
    this.paintActive(scroll === "center" || scroll === "nearest" ? scroll : null);
  }

  /** Marks the highlighted row for the eye and for the search, which owns the keys (`aria-activedescendant`). */
  private paintActive(block: ScrollLogicalPosition | null): void {
    if (!this.panel || !this.list) return;
    this.lit?.setAttribute("aria-selected", "false");
    const row = this.list.active >= 0 ? this.panel.querySelector<HTMLElement>(`#mp-o-${this.list.active}`) : null;
    row?.setAttribute("aria-selected", "true");
    this.lit = row;
    const input = this.search();
    if (row) input?.setAttribute("aria-activedescendant", row.id);
    else input?.removeAttribute("aria-activedescendant");
    if (row && block) row.scrollIntoView({ block });
  }

  // ---------- what the user does ----------

  private buttonKey(e: KeyboardEvent): void {
    if (this.button.disabled || this.isOpen) return;
    const arrow = e.key === "ArrowDown" || e.key === "ArrowUp";
    if (!arrow && !typing(e)) return; // Enter and Space press the button
    // The page and the menus listen on the document: this key is the picker's alone.
    e.preventDefault();
    e.stopPropagation();
    this.open(arrow ? "" : e.key);
  }

  private searched(e: Event): void {
    const input = e.target as HTMLInputElement;
    if (input.id !== "mp-search" || !this.list) return;
    this.list.setQuery(input.value);
    this.paintList("top");
  }

  private pointed(e: PointerEvent): void {
    const row = (e.target as Element).closest<HTMLElement>(".mp-opt");
    if (!row || !this.list || !this.panel) return;
    delete this.panel.dataset.kbd;
    if (row.dataset.id === this.list.activeId()) return;
    this.list.point(row.dataset.id ?? "");
    this.paintActive(null);
  }

  private clicked(e: MouseEvent): void {
    const target = e.target as Element;
    const row = target.closest<HTMLElement>(".mp-opt");
    if (row) {
      if (row.getAttribute("aria-disabled") !== "true") this.choose(row.dataset.id ?? "");
      return;
    }
    const choice = target.closest<HTMLElement>(".mp-ddlist [data-value]");
    if (choice) {
      this.pick(choice.dataset.value ?? "");
      return;
    }
    const opener = target.closest<HTMLElement>(".mp-dd");
    if (opener) {
      const which = opener.dataset.dd as Dropdown["which"];
      if (this.dropdown?.which === which) this.closeDropdown();
      else this.openDropdown(which, "chosen");
      return;
    }
    if (this.dropdown && !target.closest(".mp-ddlist")) this.closeDropdown(false);
  }

  private panelKey(e: KeyboardEvent): void {
    // Every key stays in the panel: the menus of ui.ts would move the focus between its buttons.
    e.stopPropagation();
    if (e.isComposing || !this.list) return; // an input method is at work in the search
    if (this.dropdown) {
      this.dropdownKey(e, this.dropdown);
      return;
    }
    if (e.key === "Escape") {
      e.preventDefault();
      this.close();
      return;
    }
    if (e.key === "Tab") {
      e.preventDefault();
      this.cycle(e.shiftKey);
      return;
    }
    const target = e.target as HTMLElement;
    if (target.id === "mp-search") {
      if (e.key === "Enter") {
        e.preventDefault();
        const id = this.list.chosen();
        if (id) this.choose(id);
      } else if (["ArrowDown", "ArrowUp", "PageDown", "PageUp"].includes(e.key)) {
        e.preventDefault();
        if (this.list.move(e.key) && this.panel) {
          this.panel.dataset.kbd = "";
          this.paintActive("nearest");
        }
      }
      return; // anything else types in the search; Home and End move in it
    }
    const opener = target.closest<HTMLElement>(".mp-dd");
    if (opener && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
      e.preventDefault();
      this.openDropdown(opener.dataset.dd as Dropdown["which"], e.key === "ArrowUp" ? "last" : "chosen");
    } else if (typing(e)) {
      e.preventDefault();
      this.typeIntoSearch(e.key);
    }
  }

  private dropdownKey(e: KeyboardEvent, dropdown: Dropdown): void {
    const items = Array.from(dropdown.el.querySelectorAll<HTMLElement>("[data-value]"));
    const at = items.indexOf(document.activeElement as HTMLElement);
    if (e.key === "Escape") {
      e.preventDefault();
      this.closeDropdown();
    } else if (e.key === "Tab") {
      e.preventDefault();
      this.closeDropdown(false);
      this.cycle(e.shiftKey, dropdown.button);
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      if (at >= 0) this.pick(items[at].dataset.value ?? "");
    } else {
      const next = moveIndex(items.length, at, e.key);
      if (next !== null) {
        e.preventDefault();
        items[next].focus();
      } else if (typing(e)) {
        // To the next choice that starts with the letter, as a list of the system does.
        e.preventDefault();
        const letter = e.key.toLowerCase();
        const after = [...items.slice(at + 1), ...items.slice(0, at + 1)];
        after.find((item) => item.textContent?.trim().toLowerCase().startsWith(letter))?.focus();
      }
    }
  }

  /** Tab and Shift+Tab go round the search and the two dropdowns, from `from` (the focus, else). */
  private cycle(back: boolean, from: Element | null = document.activeElement): void {
    if (!this.panel) return;
    const stops = [this.search(), ...Array.from(this.panel.querySelectorAll<HTMLElement>(".mp-dd"))].filter((s): s is HTMLElement => s !== null);
    const at = Math.max(0, stops.indexOf(from as HTMLElement));
    stops[(at + (back ? stops.length - 1 : 1)) % stops.length]?.focus();
  }

  /** A key typed on one of the dropdowns goes to the search, which searches. */
  private typeIntoSearch(char: string): void {
    const input = this.search();
    if (!input || !this.list) return;
    input.focus();
    input.value += char;
    input.setSelectionRange(input.value.length, input.value.length);
    this.list.setQuery(input.value);
    this.paintList("top");
  }

  private openDropdown(which: Dropdown["which"], focus: "chosen" | "last"): void {
    if (!this.panel || !this.list) return;
    this.closeDropdown(false);
    const button = this.panel.querySelector<HTMLElement>(`.mp-dd[data-dd="${which}"]`)!;
    const sort = which === "sort";
    const choices = sort ? sortChoices() : this.list.providers();
    const chosen = sort ? this.list.state.sort : this.list.state.provider;
    button.insertAdjacentHTML("afterend", dropdownHTML(which, sort ? t("picker.sort") : t("picker.provider"), choices, chosen));
    const el = button.nextElementSibling as HTMLElement;
    el.style.left = `${button.offsetLeft}px`;
    button.setAttribute("aria-expanded", "true");
    button.setAttribute("aria-controls", el.id);
    this.dropdown = { which, el, button };
    const items = el.querySelectorAll<HTMLElement>("[data-value]");
    const first = focus === "last" ? items[items.length - 1] : (el.querySelector<HTMLElement>('[aria-selected="true"]') ?? items[0]);
    first?.focus();
    first?.scrollIntoView({ block: "nearest" });
  }

  private closeDropdown(focusButton = true): void {
    const dropdown = this.dropdown;
    if (!dropdown) return;
    this.dropdown = null;
    dropdown.el.remove();
    dropdown.button.setAttribute("aria-expanded", "false");
    dropdown.button.removeAttribute("aria-controls");
    if (focusButton) dropdown.button.focus();
  }

  /** A choice in one of the dropdowns: another order, or another maker. */
  private pick(value: string): void {
    const dropdown = this.dropdown;
    if (!dropdown || !this.list) return;
    if (dropdown.which === "sort") this.list.setSort(value as PickerSort);
    else this.list.setProvider(value);
    this.closeDropdown();
    this.paintFilters();
    // The highlighted model stays highlighted where it went, if it is still listed.
    this.paintList("nearest");
  }

  private choose(id: string): void {
    const changed = id !== "" && id !== this.o.current;
    this.close();
    if (!changed) return;
    this.o = { ...this.o, current: id };
    this.paintButton();
    this.o.onChoose(id);
  }
}
