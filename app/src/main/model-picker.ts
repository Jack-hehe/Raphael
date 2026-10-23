// The model picker: a button that shows the model chosen for one of Raphael's roles and its price,
// and a panel to choose another from enabled providers (`models.catalog`). The panel
// has a search field, the order and the maker as two small dropdowns, and the models of the role's
// kind: those the role can use first, then the others, greyed, with the reason in plain words.
// The models the user starred (`favorites`, kept by the core for each role) are listed first, under
// 我的最愛, whatever the order, and a search narrows them like the others. Every row has a star
// button beside its option. The stars are out of the tab order but for the star of the highlighted
// row, which is one stop of it: the arrows still move the highlight, and the star is reached with a
// Tab and pressed with Enter or Space.
// The speech-to-text picker has two tabs over it, 雲端 (all of this) and 本機 (local-models.ts: the
// speech models that run on this computer, downloaded only when the user chooses one).
// The picker of 看圖片 (Settings › 拉斐爾 › 服務, the role `vision`) lists the language models too,
// those that read images first (`acceptsImages`), and above them all a row of its own (`lead`):
// 「與回答相同」, the value "", which is the model that answers.
// Keys: the arrows and Enter in the list, typing searches, Tab goes between the tabs, the search, the
// star of the highlighted row and the two dropdowns, Left and Right change the tab, Esc closes. The panel is the app's popover (ui.ts), so
// it closes the way menus do.
//
// The settings page draws its rows as HTML, and draws them again often: a row holds the picker's
// button (`modelPickerHTML`, a dropdown to the page's rules and styles), and the page mounts the
// picker on it after each drawing. A list that was open stays open on the new button, with what
// was being searched.

import "./model-picker.css";
import { t } from "./i18n";
import {
  type LocalConfirm,
  type LocalView,
  faceTitle,
  localListHTML,
  localState,
  stateText,
  tabsHTML,
} from "./local-models";
import { formatPerMinute, formatPerMtok, pricingNote } from "./pricing";
import { providerMark } from "./provider-marks";
import type { CatalogModel, ModelCatalog, ModelRole } from "./types";
import { CHECK, closeMenu, esc, ico, menuIsOpenFor, openPopover, placeMenu } from "./ui";

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

/**
 * What a picker chooses a model for: a role of the core's catalog, or `vision`, the model that looks
 * at images (the `vision_model` setting), which the catalog has no role for: it is any language
 * model that reads images.
 */
export type PickerRole = ModelRole | "vision";

/** The kind of model each role chooses among. */
const KIND: Record<PickerRole, "llm" | "stt"> = { cleanup: "llm", answer: "llm", stt: "stt", vision: "llm" };

/** The models of the catalog that `role` chooses among: those of its kind. */
export const modelsFor = (catalog: ModelCatalog, role: PickerRole): CatalogModel[] => {
  const models = catalog[KIND[role]];
  return models.some((m) => modelRef(m) !== m.id) ? models.map((m) => ({ ...m, id: modelRef(m) })) : models;
};

/** Legacy slugs stay OpenRouter slugs. A colon inside a local model id is kept verbatim. */
const modelRef = (model: CatalogModel): string => model.id.startsWith("@") || !model.provider_id || model.provider_id === "openrouter"
  ? model.id : `@${model.provider_id}:${model.id}`;
const connectionId = (model: CatalogModel): string => model.provider_id ?? (model.id.startsWith("@") ? model.id.slice(1).split(":", 1)[0] : "openrouter");
const connectionName = (model: CatalogModel): string => model.provider_name ?? (connectionId(model) === "openrouter" ? "OpenRouter" : connectionId(model));

/**
 * Whether `model` reads images, as OpenRouter's list says (`input_modalities`). A model of a catalog
 * that says nothing of what its models read (a core from before it sent it) is taken to: the core
 * still refuses an image to a model that cannot take it (`vision_unsupported`).
 */
export const acceptsImages = (model: CatalogModel): boolean => model.input_modalities?.includes("image") ?? true;

/** Whether `role` can use `model`, and if not why: the core's word for a role of its catalog, ours for `vision`. */
export function fitFor(model: CatalogModel, role: PickerRole): { usable: boolean; reason: string | null } {
  if (role === "vision") return acceptsImages(model) ? { usable: true, reason: null } : { usable: false, reason: "no_image_input" };
  const fit = model.roles[role];
  return { usable: fit?.usable === true, reason: fit?.reason ?? null };
}

/** Whether `role` can use `model`. */
export const usableFor = (model: CatalogModel, role: PickerRole): boolean => fitFor(model, role).usable;

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
export function priceText(model: CatalogModel, role: PickerRole): string {
  if (model.variable_price) return t("picker.varies");
  if (model.free) return t("picker.free");
  if (model.per_minute !== null) return t("pricing.003", { p0: formatPerMinute(model.per_minute) });
  if (!hasTokenPrice(model)) return t("pricing.001");
  return KIND[role] === "llm" ? tokenPair(model) : t("pricing.004", { p0: tokenPair(model) });
}

/** The price in full words, for a screen reader: 「輸入 $0.10 / 輸出 $0.50 每百萬 token」. */
export function priceDescription(model: CatalogModel, role: PickerRole): string {
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
  ["no_structured_output", () => t("picker.reasonJson")],
  ["reasoning_required", () => t("picker.reasonReasoning")],
  ["no_reasoning_setting", () => t("picker.reasonReasoningSetting")],
  ["no_max_tokens", () => t("picker.reasonMaxTokens")],
  ["no_image_input", () => t("picker.reasonImages")],
]);

/** Why a role cannot use a model, in plain words; a reason this app does not know yet still has some. */
export const reasonText = (reason: string | null | undefined): string =>
  (reason ? REASONS.get(reason)?.() : undefined) ?? t("picker.reasonOther");

/** The heading over the models a role cannot use. */
const UNUSABLE: Record<PickerRole, () => string> = {
  cleanup: () => t("picker.unusableCleanup"),
  answer: () => t("picker.unusableAnswer"),
  stt: () => t("picker.unusableStt"),
  vision: () => t("picker.unusableVision"),
};

/** A row of the list: a model, whether the role can use it, and whether the user starred it. */
export interface PickerRow {
  model: CatalogModel;
  usable: boolean;
  favorite: boolean;
  /** The picker's own first row (`PickerLead`), which is no model of the catalog. */
  lead?: boolean;
}

/**
 * A choice above every model, which is not one of them: 「與回答相同」 of 看圖片, whose value is "".
 * It has no maker, price or star, is listed whatever the order and the maker, and leaves the list
 * only when a search does not match its name.
 */
export interface PickerLead {
  id: string;
  name: string;
}

/** The lead as a row of the list: a model with its id and name and nothing else. */
function leadRow(lead: PickerLead): PickerRow {
  const model: CatalogModel = {
    id: lead.id, name: lead.name, provider: "", input_per_mtok: null, output_per_mtok: null, per_minute: null, free: false,
    variable_price: false, context_length: null, created_at_ms: null, capabilities: {}, roles: {},
  };
  return { model, usable: true, favorite: false, lead: true };
}

/** `favorites` with `id` added (once, at the end) or taken out. */
export const withFavorite = (favorites: readonly string[], id: string, on: boolean): string[] =>
  on ? (favorites.includes(id) ? [...favorites] : [...favorites, id]) : favorites.filter((f) => f !== id);

const squeeze = (text: string): string => text.replace(/[^\p{L}\p{N}]+/gu, "");

/**
 * Whether a text answers a search: each of its words is in it, whatever the case, also when the
 * spaces and the punctuation in between are left out ("gpt6" finds GPT-6).
 */
function matcher(query: string): (text: string) => boolean {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  return (text) => {
    const lower = text.toLowerCase();
    const packed = squeeze(lower);
    return words.every((word) => lower.includes(word) || (squeeze(word) !== "" && packed.includes(squeeze(word))));
  };
}

/** What a search looks in: a model's name, its maker and its id. */
const searched = (model: CatalogModel): string => `${model.name} ${model.provider} ${model.provider_name ?? ""} ${model.id}`;

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

/**
 * The rows of the list: the models that answer the search and are of the maker, in the order chosen.
 * The favourites come first, then the other models; in each, those the role can use before those it
 * cannot. A favourite that the catalog lacks is no row.
 */
export function pickerRows(models: CatalogModel[], role: PickerRole, state: PickerState, favorites: readonly string[] = []): PickerRow[] {
  const matches = matcher(state.query);
  const starred = new Set(favorites);
  const rows = models
    .filter((model) => (state.provider === "" || model.provider === state.provider) && matches(searched(model)))
    .sort(ORDERS[state.sort])
    .map((model) => ({ model: { ...model, id: modelRef(model) }, usable: usableFor(model, role), favorite: starred.has(modelRef(model)) }));
  const usableFirst = (own: PickerRow[]): PickerRow[] => {
    const groups = new Map<string, PickerRow[]>();
    for (const row of own) {
      const id = connectionId(row.model);
      groups.set(id, [...(groups.get(id) ?? []), row]);
    }
    const ids = [...groups.keys()].sort((a, b) => a === "openrouter" ? -1 : b === "openrouter" ? 1 : collator.compare(connectionName(groups.get(a)![0].model), connectionName(groups.get(b)![0].model)));
    return ids.flatMap((id) => { const group = groups.get(id)!; return [...group.filter((r) => r.usable), ...group.filter((r) => !r.usable)]; });
  };
  return [...usableFirst(rows.filter((row) => row.favorite)), ...usableFirst(rows.filter((row) => !row.favorite))];
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
    readonly role: PickerRole,
    private readonly models: CatalogModel[],
    /** The model chosen now: checked, and highlighted when the list opens. */
    public current: string,
    state: Partial<PickerState> = {},
    /** The ids the user starred, in the order they were added; those the catalog lacks are kept, not listed. */
    public favorites: readonly string[] = [],
    /** The choice above every model, if the picker has one (`PickerLead`). */
    private readonly lead: PickerLead | null = null,
  ) {
    this.state = { ...DEFAULT_PICKER_STATE, ...state };
    this.refresh(current);
  }

  /** The user starred or un-starred a model: the rows move, and the highlight stays on its model. */
  setFavorites(favorites: readonly string[]): void {
    this.favorites = favorites;
    this.refresh(this.activeId());
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

  /** How many models the list shows (the lead is none). */
  modelCount(): number {
    return this.rows.filter((row) => !row.lead).length;
  }

  private refresh(keep: string | null): void {
    const models = pickerRows(this.models, this.role, this.state, this.favorites);
    const lead = this.lead && matcher(this.state.query)(this.lead.name) ? [leadRow(this.lead)] : [];
    this.rows = [...lead, ...models];
    const kept = keep === null ? -1 : this.rows.findIndex((row) => row.model.id === keep);
    this.active = kept >= 0 ? kept : this.rows.length > 0 ? 0 : -1;
  }
}

// ---------- the HTML ----------

/** The star beside a row: a real button that adds the model to 我的最愛 or takes it out. It is not a stop of the Tab key (see `MountedPicker.cycle`). */
function starHTML(model: CatalogModel, on: boolean): string {
  const label = t(on ? "picker.favRemove" : "picker.favAdd", { p0: model.name });
  return (
    `<button type="button" class="mp-star" tabindex="-1" data-star="${esc(model.id)}"${on ? " data-on" : ""} ` +
    `aria-label="${esc(label)}" title="${esc(t(on ? "picker.favRemoveTitle" : "picker.favAddTitle"))}">${ico("star")}</button>`
  );
}

/** One row: the name, the price, and a line with the maker, the context or the dictionary, the date when the list is newest first, and why the role cannot use it; the star is beside it. */
function optionHTML(row: PickerRow, index: number, list: PickerList): string {
  const { model } = row;
  const chosen = model.id === list.current;
  if (row.lead) return leadHTML(model.id, model.name, index, chosen);
  const details = [model.provider];
  if (model.context_length !== null) details.push(t("picker.context", { p0: formatContext(model.context_length) }));
  if (model.capabilities.dictionary_hints) details.push(t("picker.dictionary"));
  if (list.state.sort === "newest" && model.created_at_ms !== null) details.push(addedText(model.created_at_ms));
  const reason = row.usable ? "" : reasonText(fitFor(model, list.role).reason);
  if (reason) details.push(reason);
  const price = priceText(model, list.role);
  // What a screen reader says of the row, in an order that makes sense when heard.
  const heard = [model.name, connectionName(model), model.provider, priceDescription(model, list.role)];
  if (row.favorite) heard.push(t("picker.favorites"));
  if (chosen) heard.push(t("picker.current"));
  if (reason) heard.push(reason);
  return (
    `<div class="mp-row" role="presentation">` +
    `<button type="button" class="mp-opt" role="option" id="mp-o-${index}" tabindex="-1" data-id="${esc(model.id)}" aria-selected="false"` +
    `${row.usable ? "" : ' aria-disabled="true"'}${chosen ? " data-current" : ""} aria-label="${esc(heard.join(", "))}" title="${esc(model.id)}">` +
    `<span class="ck">${chosen ? CHECK : ""}</span>` +
    `<span class="mp-main"><span class="mp-line"><span class="mp-name">${esc(model.name)}</span>` +
    `<span class="mp-price num">${esc(price)}</span></span>` +
    (details.length ? `<span class="mp-sub">${esc(details.join(" · "))}</span>` : "") +
    `</span></button>` +
    starHTML(model, row.favorite) +
    `</div>`
  );
}

/** The lead's row: its name alone, checked when it is chosen; no price, no maker, and a gap where a star would be. */
function leadHTML(id: string, name: string, index: number, chosen: boolean): string {
  const heard = chosen ? `${name}, ${t("picker.current")}` : name;
  return (
    `<div class="mp-row" role="presentation">` +
    `<button type="button" class="mp-opt mp-lead" role="option" id="mp-o-${index}" tabindex="-1" data-id="${esc(id)}" aria-selected="false"` +
    `${chosen ? " data-current" : ""} aria-label="${esc(heard)}">` +
    `<span class="ck">${chosen ? CHECK : ""}</span>` +
    `<span class="mp-main"><span class="mp-line"><span class="mp-name">${esc(name)}</span></span></span></button>` +
    `<span class="mp-star-gap" aria-hidden="true"></span>` +
    `</div>`
  );
}

const headingHTML = (text: string): string => `<div class="mp-group" aria-hidden="true">${esc(text)}</div>`;

/**
 * The rows of the list. With favourites, 我的最愛 heads the list and 所有模型 the rest; the models
 * the role cannot use are under a heading of their own, which says why. A lead is above them all,
 * under no heading.
 */
export function listHTML(list: PickerList): string {
  const { rows } = list;
  const starred = rows.findIndex((row) => row.favorite);
  const others = starred < 0 ? -1 : rows.findIndex((row) => !row.favorite && !row.lead);
  const unusable = rows.findIndex((row) => !row.usable && !row.favorite && !row.lead);
  return rows
    .map((row, index) => {
      let heading = "";
      if (index === starred) heading = headingHTML(t("picker.favorites"));
      if (index === unusable) heading = headingHTML(UNUSABLE[list.role]());
      else if (index === others) heading = headingHTML(t("picker.others"));
      const previous = rows[index - 1];
      if (!row.lead && (!previous || previous.lead || previous.favorite !== row.favorite || connectionId(previous.model) !== connectionId(row.model))) {
        heading += `<div class="mp-provider"><span class="mp-provider-mark" aria-hidden="true">${providerMark(connectionName(row.model))}</span><span>${esc(connectionName(row.model))}</span></div>`;
      }
      return heading + optionHTML(row, index, list);
    })
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

/** The panel, empty: its list, its line under the list and its small print are filled in apart. With `tab`, the picker has the local tab too. */
function panelHTML(list: PickerList, label: string, tab: "cloud" | "local" | null): string {
  const search = t("picker.search");
  const cloud =
    `<div class="mp-pane" id="mp-pane-cloud"${tab ? ` role="tabpanel" aria-labelledby="mp-tab-cloud"${tab === "cloud" ? "" : " hidden"}` : ""}>` +
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
    `<p class="mp-foot"></p></div>`;
  if (!tab) return cloud;
  return (
    tabsHTML(tab) +
    cloud +
    `<div class="mp-pane" id="mp-pane-local" role="tabpanel" aria-labelledby="mp-tab-local"${tab === "local" ? "" : " hidden"}>` +
    `<div class="mp-scroll"><div class="lm-list" id="mp-locallist" aria-label="${esc(label)}"></div></div>` +
    `<p class="mp-foot">${esc(t("local.intro"))}</p></div>`
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
  role: PickerRole;
  /** The id of the model chosen now. */
  current: string;
  /** The catalog (`models.catalog`), or null while it is being read (`loading`) or when it cannot be had. */
  catalog: ModelCatalog | null;
  loading?: boolean;
  /** What the control is called: the title of its row. It names the button and the list for a screen reader. */
  label: string;
  /** The button's `data-k`: a page that draws itself again gives the focus back by it (settings.ts does). */
  key?: string;
  /** The ids the user starred for this role (the core keeps them), in the order they were added. */
  favorites?: readonly string[];
  /** Whether the button shows the price of what is chosen: yes, unless the page shows it beside. */
  showPrice?: boolean;
  /** No choosing: the brain's settings are not known. */
  disabled?: boolean;
  /** The user chose `id`: never the model chosen now, never one the role cannot use. The page saves it. */
  onChoose: (id: string) => void;
  /** The speech models of this computer (a second tab) and what is under way: only the speech-to-text picker has it. */
  local?: LocalView;
  /** The user chose the local model `name`, or pressed 下載 on it. The page downloads it if it is not on disk, saves it and hands the progress back with `update`. */
  onUseLocal?: (name: string) => void;
  /** The user cancelled the download in progress. */
  onCancelLocal?: () => void;
  /** The user deleted the local model `name`, after the question. */
  onDeleteLocal?: (name: string) => void;
  /** The user starred (`on`) or un-starred `id`. The picker shows it at once; the page saves it and hands the saved list back with `update`. */
  onFavorite?: (id: string, on: boolean) => void;
  /** A choice above every model (`PickerLead`): 看圖片's 「與回答相同」. `current` may be its id. */
  lead?: PickerLead;
}

/** What a picker's button is drawn from: all but what choosing does. */
export type ModelPickerLook = Omit<ModelPickerOptions, "onChoose">;

export interface ModelPicker {
  /** Shows another model as chosen, a newer catalog, another label or state (not another role). An open list follows and keeps its search. */
  update(changes: Partial<ModelPickerLook>): void;
  /** Opens the list, with `query` in the search if one is given. */
  open(query?: string): void;
  close(): void;
  readonly isOpen: boolean;
  /** The button that opens the list. */
  readonly button: HTMLButtonElement;
}

/** What the button shows: the chosen model (or choice), and its price unless the page shows that. */
function buttonFace(o: ModelPickerLook): { inner: string; label: string; title: string } {
  // The lead chosen: its name alone, with no price.
  if (o.lead && o.current === o.lead.id && !o.local) {
    return { inner: `<span class="mp-tname">${esc(o.lead.name)}</span>${ico("down")}`, label: `${o.label}: ${o.lead.name}`, title: o.lead.name };
  }
  const local = o.local ? faceTitle(o.local) : null;
  const model = local ? undefined : (o.catalog ? modelsFor(o.catalog, o.role) : []).find((m) => m.id === o.current);
  const name = local ?? model?.name ?? shortName(o.current);
  const price = local ? t("settings.sttLocalPrice") : model ? priceText(model, o.role) : o.loading ? "…" : "";
  const said = local ? t("settings.sttLocalPrice") : model ? priceDescription(model, o.role) : "";
  return {
    inner:
      `<span class="mp-tname">${esc(name)}</span>` +
      (price && o.showPrice !== false ? `<span class="mp-tprice num">${esc(price)}</span>` : "") +
      ico("down"),
    label: `${o.label}: ${name}${said ? `, ${said}` : ""}`,
    title: local ?? (model ? `${model.provider}: ${model.name}` : o.current),
  };
}

/** The button alone. It is a dropdown (`sel`): it looks and lines up like the page's others. */
function buttonHTML(o: ModelPickerLook): string {
  const face = buttonFace(o);
  return (
    `<button type="button" class="sel mp-trigger" aria-haspopup="dialog" aria-expanded="false" aria-label="${esc(face.label)}" title="${esc(face.title)}"` +
    `${o.key ? ` data-k="${esc(o.key)}"` : ""}${o.disabled ? " disabled" : ""}>${face.inner}</button>`
  );
}

/** A picker's button in its slot, for a page drawn as HTML; `mountModelPicker` on the slot makes it work. */
export const modelPickerHTML = (o: ModelPickerLook): string =>
  `<span class="mp-slot" data-model-picker="${o.role}">${buttonHTML(o)}</span>`;

/** Makes a picker of `container`: the button drawn in it (`modelPickerHTML`), or a new one in its place. */
export function mountModelPicker(container: HTMLElement, options: ModelPickerOptions): ModelPicker {
  return new MountedPicker(container, options);
}

/** A key that types something: a letter, a digit, a mark (Space is no such key: it presses buttons). */
const typing = (e: KeyboardEvent): boolean => e.key.length === 1 && e.key !== " " && !e.ctrlKey && !e.metaKey && !e.altKey;

/** The picker whose list is open: a page drawn again hands that list to the new button of the role. */
let opened: { picker: MountedPicker; role: PickerRole; button: HTMLElement } | null = null;

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
  /** The tab that is open, when there are two. */
  private tab: "cloud" | "local" = "cloud";
  /** The question the local tab is asking, if it is. */
  private confirm: LocalConfirm | null = null;
  /** What the local tab was drawn from, to tell a new byte count (drawn in place) from a new state (drawn again). */
  private localDrawn = "";

  constructor(container: HTMLElement, options: ModelPickerOptions) {
    this.o = { ...options };
    if (!container.querySelector(".mp-trigger")) container.innerHTML = buttonHTML(this.o);
    this.button = container.querySelector<HTMLButtonElement>(".mp-trigger")!;
    this.paintButton();
    this.button.addEventListener("click", (e) => {
      // The page's own handler of dropdowns (`sel`) would close the list this click opens.
      e.stopPropagation();
      if (this.isOpen) this.close();
      else this.open();
    });
    this.button.addEventListener("keydown", (e) => this.buttonKey(e));
    this.takeOver();
  }

  get isOpen(): boolean {
    return this.panel !== null && menuIsOpenFor(this.button);
  }

  update(changes: Partial<ModelPickerLook>): void {
    this.o = { ...this.o, ...changes, role: this.o.role };
    this.paintButton();
    if (!this.isOpen || !this.list) return;
    if (this.o.disabled) {
      this.close();
      return;
    }
    // The list is made again, as it was searched and highlighted: the choices may have changed too.
    const highlighted = this.list.activeId();
    this.list = this.newList(this.list.state);
    if (highlighted) this.list.point(highlighted);
    this.paintFilters();
    this.paintList(null);
    this.paintLocal();
    placeMenu(); // the small print may have grown
  }

  open(query = ""): void {
    this.tab = this.o.local?.active ? "local" : "cloud";
    this.show(this.newList({ query }), {});
  }

  close(): void {
    const had = this.panel !== null;
    this.forget();
    if (menuIsOpenFor(this.button)) closeMenu();
    if (had && this.button.isConnected) this.button.focus();
  }

  // ---------- drawing ----------

  private models(): CatalogModel[] {
    return this.o.catalog ? modelsFor(this.o.catalog, this.o.role) : [];
  }

  private newList(state: Partial<PickerState>): PickerList {
    return new PickerList(this.o.role, this.models(), this.o.current, state, this.o.favorites ?? [], this.o.lead ?? null);
  }

  private paintButton(): void {
    const face = buttonFace(this.o);
    this.button.innerHTML = face.inner;
    this.button.disabled = this.o.disabled === true;
    this.button.title = face.title;
    this.button.setAttribute("aria-label", face.label);
    if (this.o.key) this.button.dataset.k = this.o.key;
  }

  private search(): HTMLInputElement | null {
    return this.panel?.querySelector<HTMLInputElement>("#mp-search") ?? null;
  }

  private show(list: PickerList, restore: { caret?: number; scroll?: number }): void {
    if (this.button.disabled) return;
    this.list = list;
    if (!this.o.local) this.tab = "cloud";
    this.confirm = null;
    this.localDrawn = "";
    const panel = openPopover(this.button, panelHTML(list, this.o.label, this.o.local ? this.tab : null), {
      className: "menu mp-panel",
      role: "dialog",
      label: this.o.label,
    });
    this.panel = panel;
    opened = { picker: this, role: this.o.role, button: this.button };
    panel.addEventListener("keydown", (e) => this.panelKey(e));
    panel.addEventListener("input", (e) => this.searched(e));
    panel.addEventListener("click", (e) => this.clicked(e));
    // A click on a row or its star leaves the focus in the search, where the keys work.
    panel.addEventListener("mousedown", (e) => {
      if ((e.target as Element).closest(".mp-opt, .mp-star")) e.preventDefault();
    });
    panel.addEventListener("pointermove", (e) => this.pointed(e));
    const input = this.search()!;
    input.value = list.state.query;
    // With favourites the list opens at its top, where they are, and not at the model in use.
    this.paintList(list.rows.some((row) => row.favorite) ? "top" : "center");
    this.paintLocal();
    if (restore.scroll !== undefined) panel.querySelector(".mp-scroll")!.scrollTop = restore.scroll;
    // Placed again now that it is filled in: the popover was placed empty, without its small print,
    // and above its button it would now cover it.
    placeMenu();
    if (this.tab === "local") {
      panel.querySelector<HTMLElement>("#mp-tab-local")?.focus({ preventScroll: true });
      return;
    }
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
    const highlighted = list?.activeId();
    const tab = before.picker.tab;
    before.picker.forget();
    this.tab = tab;
    const again = this.newList(list?.state ?? {});
    if (highlighted) again.point(highlighted);
    this.show(again, { caret, scroll });
  }

  /** Lets go of the panel without touching it: the popover closed, or a new button took the list over. */
  private forget(): void {
    this.panel = null;
    this.list = null;
    this.dropdown = null;
    this.lit = null;
    this.confirm = null;
    this.localDrawn = "";
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
    this.panel.querySelector(".mp-foot")!.textContent = footText(this.o.catalog, list.modelCount());
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

  /**
   * Draws the local tab. A new byte count only moves the bar and the percentage of the row being
   * downloaded, so that the focus stays where it is; anything else (a state changed, a question
   * asked) draws the rows again and gives the focus back to the same button.
   */
  private paintLocal(): void {
    const local = this.o.local;
    const host = this.panel?.querySelector<HTMLElement>("#mp-locallist");
    if (!local || !host) return;
    const models = local.models;
    const shape = JSON.stringify([
      typeof models === "string" ? models : models.map((m) => [m.name, m.ready, localState(m, local).kind]),
      local.active,
      local.download?.name ?? null,
      this.confirm,
    ]);
    if (shape === this.localDrawn && typeof models !== "string") {
      for (const model of models) {
        const row = host.querySelector<HTMLElement>(`.lm-row[data-name="${CSS.escape(model.name)}"]`);
        const state = localState(model, local);
        if (!row || state.kind !== "downloading") continue;
        row.querySelector("[data-state]")!.textContent = stateText(state);
        row.querySelector<HTMLElement>(".lm-bar")!.setAttribute("aria-valuenow", String(state.percent));
        row.querySelector<HTMLElement>(".lm-bar i")!.style.width = `${state.percent}%`;
      }
      return;
    }
    const focused = document.activeElement instanceof HTMLElement && host.contains(document.activeElement) ? document.activeElement : null;
    const back = focused ? { act: focused.dataset.act, name: focused.dataset.name } : null;
    host.innerHTML = localListHTML(local, this.confirm);
    this.localDrawn = shape;
    if (back) {
      const find = (act: string | undefined): HTMLElement | null =>
        host.querySelector<HTMLElement>(`[data-act="${act}"][data-name="${CSS.escape(back.name ?? "")}"]`);
      (find(back.act) ?? find("use") ?? host.querySelector<HTMLElement>("button:not(:disabled)"))?.focus();
    }
    placeMenu();
  }

  private setTab(tab: "cloud" | "local", focus: boolean): void {
    if (!this.panel || !this.o.local || tab === this.tab) return;
    this.tab = tab;
    this.confirm = null;
    this.closeDropdown(false);
    for (const which of ["cloud", "local"] as const) {
      const button = this.panel.querySelector<HTMLElement>(`#mp-tab-${which}`)!;
      button.setAttribute("aria-selected", String(which === tab));
      button.tabIndex = which === tab ? 0 : -1;
      this.panel.querySelector<HTMLElement>(`#mp-pane-${which}`)!.hidden = which !== tab;
    }
    this.paintLocal();
    placeMenu();
    if (focus) this.panel.querySelector<HTMLElement>(`#mp-tab-${tab}`)!.focus();
  }

  /** The arrows between the rows of the local tab. */
  private localKey(e: KeyboardEvent): void {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    const rows = Array.from(this.panel?.querySelectorAll<HTMLElement>("#mp-locallist .lm-main:not(:disabled)") ?? []);
    const at = rows.indexOf(document.activeElement as HTMLElement);
    const next = moveIndex(rows.length, at, e.key);
    if (next === null) return;
    e.preventDefault();
    rows[next].focus();
  }

  /** Starts downloading the local model `name` at once: its row then shows the progress and 取消. */
  private download(name: string): void {
    this.confirm = null;
    this.o.onUseLocal?.(name);
    this.paintLocal();
  }

  /** A button of the local tab. */
  private localAct(button: HTMLElement): void {
    const local = this.o.local;
    const name = button.dataset.name ?? "";
    if (!local) return;
    switch (button.dataset.act) {
      case "use": {
        if (name === local.active) {
          this.close();
          return;
        }
        const model = typeof local.models === "string" ? undefined : local.models.find((m) => m.name === name);
        if (!model) return;
        if (model.ready) {
          this.close();
          this.o.onUseLocal?.(name);
        } else {
          this.download(name);
        }
        return;
      }
      case "download":
        this.download(name);
        return;
      case "delete":
        this.confirm = { kind: "delete", name };
        this.paintLocal();
        this.panel?.querySelector<HTMLElement>('[data-act="confirm"]')?.focus();
        return;
      case "confirm": {
        const asked = this.confirm;
        this.confirm = null;
        if (asked?.kind === "delete") this.o.onDeleteLocal?.(name);
        this.paintLocal();
        return;
      }
      case "dismiss":
        this.confirm = null;
        this.paintLocal();
        return;
      case "cancel":
        this.o.onCancelLocal?.();
        return;
    }
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
    const row = (e.target as Element).closest<HTMLElement>(".mp-row")?.querySelector<HTMLElement>(".mp-opt");
    if (!row || !this.list || !this.panel) return;
    delete this.panel.dataset.kbd;
    if (row.dataset.id === this.list.activeId()) return;
    this.list.point(row.dataset.id ?? "");
    this.paintActive(null);
  }

  private clicked(e: MouseEvent): void {
    const target = e.target as Element;
    const tab = target.closest<HTMLElement>(".mp-panel .tab");
    if (tab) {
      this.setTab(tab.dataset.tab === "local" ? "local" : "cloud", false);
      return;
    }
    const act = target.closest<HTMLElement>("#mp-locallist [data-act]");
    if (act) {
      this.localAct(act);
      return;
    }
    const star = target.closest<HTMLElement>(".mp-star");
    if (star) {
      this.star(star.dataset.star ?? "", document.activeElement === star ? "nearest" : null);
      return;
    }
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
    if (target.closest(".mp-panel .tab") && ["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) {
      e.preventDefault();
      this.setTab(e.key === "ArrowLeft" || e.key === "Home" ? "cloud" : "local", true);
      return;
    }
    if (this.tab === "local") {
      this.localKey(e);
      return;
    }
    if (target.id === "mp-search") {
      if (e.key === "Enter") {
        e.preventDefault();
        const id = this.list.chosen();
        if (id !== null) this.choose(id);
      } else if (["ArrowDown", "ArrowUp", "PageDown", "PageUp"].includes(e.key)) {
        e.preventDefault();
        if (this.list.move(e.key) && this.panel) {
          this.panel.dataset.kbd = "";
          this.paintActive("nearest");
        }
      }
      return; // anything else types in the search; Home and End move in it
    }
    if (target.closest(".mp-star") && moveIndex(this.list.rows.length, this.list.active, e.key) !== null) {
      // On a star the arrows still move the highlight, and the focus goes with it to its star.
      e.preventDefault();
      this.list.move(e.key);
      this.panel?.setAttribute("data-kbd", "");
      this.paintActive("nearest");
      this.activeStar()?.focus();
      return;
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

  /** The star of the highlighted row. */
  private activeStar(): HTMLElement | null {
    const row = this.list && this.list.active >= 0 ? this.panel?.querySelector<HTMLElement>(`#mp-o-${this.list.active}`) : null;
    return row?.closest(".mp-row")?.querySelector<HTMLElement>(".mp-star") ?? null;
  }

  /**
   * Tab and Shift+Tab go round the tab that is open, the search, the star of the highlighted row and
   * the two dropdowns (in the local tab: its buttons), from `from` (the focus, else). The other stars are not stops: a list of hundreds would be no
   * way to Tab through, and the arrows reach every row's star.
   */
  private cycle(back: boolean, from: Element | null = document.activeElement): void {
    if (!this.panel) return;
    const open = this.panel.querySelector<HTMLElement>(`#mp-tab-${this.tab}`);
    const inside =
      this.tab === "local"
        ? Array.from(this.panel.querySelectorAll<HTMLElement>("#mp-pane-local button:not(:disabled)"))
        : [this.search(), this.activeStar(), ...Array.from(this.panel.querySelectorAll<HTMLElement>(".mp-dd"))];
    const stops = [open, ...inside].filter((s): s is HTMLElement => s !== null);
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

  /** Stars the model of `id`, or takes it out of the favourites if it is one. `scroll` is where the list goes: with the focus, when it is on a star, else nowhere. */
  private star(id: string, scroll: "nearest" | null): void {
    if (!this.list || id === "") return;
    const on = !this.list.favorites.includes(id);
    const favorites = withFavorite(this.list.favorites, id, on);
    this.o = { ...this.o, favorites };
    this.list.point(id);
    this.list.setFavorites(favorites);
    this.paintList(scroll);
    if (scroll !== null) this.activeStar()?.focus();
    this.o.onFavorite?.(id, on);
  }

  private choose(id: string): void {
    // "" is no model, but it is the lead's value when the picker has one.
    const changed = (id !== "" || this.o.lead?.id === "") && id !== this.o.current;
    this.close();
    if (!changed) return;
    this.o = { ...this.o, current: id };
    this.paintButton();
    this.o.onChoose(id);
  }
}
