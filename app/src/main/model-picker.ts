// A two-phase model chooser shared by settings, services and member details.
import "./model-picker.css";
import { getLocale, t } from "./i18n";
import {
  type LocalConfirm,
  type LocalView,
  faceTitle,
  localListHTML,
} from "./local-models";
import { formatPerMinute, formatPerMtok } from "./pricing";
import { modelMark, providerMark } from "./provider-marks";
import { CLIS, cliModels } from "./cli-brains";
import type { CatalogModel, CliBrain, CliBrainId, ModelCatalog, ModelRole, ModelProvider } from "./types";
import { CHECK, btn, esc, ico, openMenu, sel } from "./ui";
import { openDialog, type Dialog } from "./member-dialogs";

// ---------- what the list shows ----------

/** How the list is ordered. */
export type PickerSort = "price" | "name" | "newest";

/** What the list is narrowed to and how it is ordered. */
export interface PickerState {
  query: string;
  sort: PickerSort;
  /** A maker, or "" for all of them. */
  provider: string;
  /** A connected provider id, or "" for every enabled connection. */
  connection?: string;
}

export const DEFAULT_PICKER_STATE: PickerState = { query: "", sort: "price", provider: "", connection: "" };

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
const modelRef = (model: CatalogModel): string => model.id.startsWith("cli:") || model.id.startsWith("@") || !model.provider_id || model.provider_id === "openrouter"
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
  if (model.id.startsWith("cli:")) return t("brains.subscription");
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
 * Filter by connection, maker and search. All connections pin the current model when not searching;
 * searches rank exact and prefix names first. Direct connections precede OpenRouter, with favourites,
 * role compatibility and the chosen order retained inside each connection and relevance tier.
 */
export function pickerRows(models: CatalogModel[], role: PickerRole, state: PickerState, favorites: readonly string[] = []): PickerRow[] {
  const matches = matcher(state.query);
  const starred = new Set(favorites);
  const subscription = models.length > 0 && models.every((model) => connectionId(model).startsWith("cli:"));
  const rows = models
    .filter((model) => (!state.connection || connectionId(model) === state.connection) && (state.provider === "" || model.provider === state.provider) && matches(searched(model)))
    // a subscription lists its models in the CLI's own order (strongest first), not by name
    .sort(subscription ? () => 0 : ORDERS[state.sort])
    .map((model) => ({ model: { ...model, id: modelRef(model) }, usable: usableFor(model, role), favorite: starred.has(modelRef(model)) }));
  const query = squeeze(state.query.trim().toLowerCase());
  const searching = state.query.trim() !== "";
  const rank = (model: CatalogModel): number => {
    const name = squeeze(model.name.toLowerCase());
    return name === query ? 0 : name.startsWith(query) ? 1 : 2;
  };
  return rows.sort((a, b) => {
    if (!state.connection && searching && query) {
      const relevance = rank(a.model) - rank(b.model);
      if (relevance) return relevance;
    }
    const ac = connectionId(a.model), bc = connectionId(b.model);
    if (ac !== bc) return ac === "openrouter" ? 1 : bc === "openrouter" ? -1 :
      collator.compare(connectionName(a.model), connectionName(b.model)) || collator.compare(ac, bc);
    return Number(b.favorite) - Number(a.favorite) || Number(b.usable) - Number(a.usable);
  });
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

  setConnection(connection: string): void {
    this.state = { ...this.state, connection, provider: "" };
    this.refresh(this.current);
  }

  /** The makers to narrow the list to. */
  providers(): PickerChoice[] {
    return providerChoices(this.models.filter((model) => !this.state.connection || connectionId(model) === this.state.connection));
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


export function replacementNotices(catalog: ModelCatalog): string[] {
  const role = { cleanup: "picker.roleCleanup", answer: "picker.roleAnswer", stt: "picker.roleStt" } as const;
  return catalog.replaced.map((r) => {
    const fallback = modelsFor(catalog, r.role).find((m) => m.id === r.default);
    return t("picker.replaced", { p0: r.model, p1: t(role[r.role]), p2: fallback?.name ?? r.default });
  });
}


export interface ModelPickerOptions {
  /** What the model is for. */
  role: PickerRole;
  /** The id of the model chosen now. */
  current: string;
  /** The catalog (`models.catalog`), or null while it is being read (`loading`) or when it cannot be had. */
  catalog: ModelCatalog | null;
  /** Public connection metadata includes enabled connections with no models for this role. */
  providers?: readonly ModelProvider[];
  clis?: readonly CliBrain[];
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
  onSources?: () => void;
  onCliAction?: (id: CliBrainId) => void;
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
  /** The model the inheritance lead stands for: shown on the card, and its effort row is the list's when the lead is chosen. */
  inheritFrom?: string;
  /** The effort a newly chosen model starts at when it has it, instead of the model's own default (快速提問: low, to stay quick). */
  defaultEffort?: string;
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

export interface ChooserSource {
  id: string;
  name: string;
  kind: string;
  group: "subscriptions" | "api" | "local";
  models: CatalogModel[];
  cli?: CliBrain;
}

const baseRef = (ref: string): string => ref.split("?effort=")[0];
const effortOf = (ref: string, models: readonly CatalogModel[]): string => ref.split("?effort=")[1] ??
  models.flatMap((model) => Object.entries(model.effort_models ?? {})).find(([, value]) => value === ref)?.[0] ?? "";

/** A chosen ref as a model and an effort, for a page that saves them apart (快速提問): an effort's own model id gives its model. */
export function splitEffort(ref: string, models: readonly CatalogModel[]): { model: string; effort: string } {
  const base = baseRef(ref);
  return { model: models.find((m) => Object.values(m.effort_models ?? {}).includes(base))?.id ?? base, effort: effortOf(ref, models) };
}
const groupKeys = { subscriptions: "chooser.subscriptions", api: "chooser.api", local: "chooser.local" } as const;
const isLocal = (kind: string): boolean => ["ollama", "lmstudio", "lm_studio"].includes(kind);
const shortName = (id: string): string => {
  const raw = baseRef(id).replace(/^@[^:]+:/, "").replace(/^cli:[^:]+:/, "");
  return raw.slice(raw.lastIndexOf("/") + 1) || id;
};

function pickerModels(o: ModelPickerLook): CatalogModel[] {
  const models = o.catalog ? modelsFor(o.catalog, o.role).filter((m) =>
    !m.id.startsWith("cli:") && o.providers?.find((p) => p.id === connectionId(m))?.enabled !== false) : [];
  return [...models, ...(o.role === "answer" ? cliModels(o.clis ?? []) : [])];
}

export function chooserSources(o: ModelPickerLook): ChooserSource[] {
  const models = pickerModels(o);
  const sources: ChooserSource[] = o.role === "answer" ? CLIS.map((known) => ({
    id: `cli:${known.id}`, name: known.name, kind: known.mark, group: "subscriptions" as const,
    cli: o.clis?.find((c) => c.id === known.id), models: models.filter((m) => connectionId(m) === `cli:${known.id}`),
  })) : [];
  for (const provider of o.providers ?? []) {
    if (provider.enabled) sources.push({ id: provider.id, name: provider.name, kind: provider.kind,
      group: isLocal(provider.kind) ? "local" : "api", models: models.filter((m) => connectionId(m) === provider.id) });
  }
  for (const model of models) {
    const id = connectionId(model);
    if (!sources.some((source) => source.id === id)) {
      const name = connectionName(model), kind = id === "openrouter" ? "openrouter" : name.toLowerCase();
      sources.push({ id, name, kind, group: isLocal(kind) ? "local" : "api", models: models.filter((m) => connectionId(m) === id) });
    }
  }
  if (o.local) sources.push({ id: "local-speech", name: t("chooser.localSpeech"), kind: "", group: "local", models: [] });
  return sources;
}

/** A fallback is allowed only for an exact maker and model identifier, never a similar name. */
export function chooserPrice(model: CatalogModel, models: readonly CatalogModel[], role: PickerRole): string {
  if (model.id.startsWith("cli:")) return "";
  if (model.free || model.variable_price || model.per_minute !== null || hasTokenPrice(model)) return priceText(model, role);
  const raw = model.id.replace(/^@[^:]+:/, "");
  const maker = model.provider.toLowerCase();
  const priced = models.find((other) => connectionId(other) === "openrouter" &&
    other.provider.toLowerCase() === maker &&
    (other.id === raw || other.id === `${maker}/${raw}`) &&
    (hasTokenPrice(other) || other.per_minute !== null || other.free));
  return priced ? priceText(priced, role) : "";
}

function concreteCurrent(o: ModelPickerLook, models = pickerModels(o)): string {
  const ref = baseRef(o.current);
  return /^cli:[^:]+$/.test(ref) ? models.find((m) => connectionId(m) === ref && m.is_default)?.id ?? ref :
    models.find((m) => Object.values(m.effort_models ?? {}).includes(ref))?.id ?? ref;
}

/** The same model name and connection source for picker buttons and read-only cards. */
export function modelPickerSelection(o: ModelPickerLook): { name: string; source: string; model?: CatalogModel } {
  // Public cached names remain useful after logout; only pickerModels can supply runnable rows.
  const models = [...pickerModels(o).filter((m) => !m.id.startsWith("cli:")), ...cliModels(o.clis ?? [], false)];
  const inherited = baseRef(o.current) === o.lead?.id && o.inheritFrom;
  const ref = concreteCurrent(inherited ? { ...o, current: inherited } : o, models), model = models.find((m) => m.id === ref);
  const local = o.local?.active ? faceTitle(o.local) : null;
  const name = local ? local.split(" · ").slice(1).join(" · ") : o.lead?.id === ref ? o.lead.name : model?.name ?? shortName(ref);
  const provider = local ? t("picker.tabLocal") : model ? connectionName(model) : ref.startsWith("cli:") ?
    `${CLIS.find((c) => ref.startsWith(`cli:${c.id}`))?.name ?? ""} ${t("brains.subscription")}` : o.lead?.id === ref ? t("chooser.same") : "";
  const source = inherited ? `${provider} · ${t("services.sameAsAnswer")}` : provider;
  return { name, source, model };
}

function buttonFace(o: ModelPickerLook): { inner: string; label: string } {
  const { name, source, model } = modelPickerSelection(o);
  return { inner: `${model ? modelMark(model) : ""}<span class="mp-ttext"><strong class="mp-tname">${esc(name)}</strong><span class="mp-source">${esc(source)}</span></span>${ico("down")}`,
    label: `${o.label}: ${name}${source ? ` · ${source}` : ""}` };
}

function buttonHTML(o: ModelPickerLook): string {
  const face = buttonFace(o);
  return `<button type="button" class="sel mp-trigger" aria-haspopup="dialog" aria-expanded="false" aria-label="${esc(face.label)}"` +
    `${o.key ? ` data-k="${esc(o.key)}"` : ""}${o.disabled ? " disabled" : ""}>${face.inner}</button>`;
}

export const modelPickerHTML = (o: ModelPickerLook): string => `<span class="mp-slot" data-model-picker="${o.role}">${buttonHTML(o)}</span>`;
const mounted = new WeakMap<HTMLElement, MountedPicker>();
let opened: MountedPicker | null = null;
export function mountModelPicker(container: HTMLElement, options: ModelPickerOptions): ModelPicker {
  const button = container.querySelector<HTMLElement>(".mp-trigger");
  const previous = button ? mounted.get(button) : undefined;
  if (previous) { previous.update(options); return previous; }
  return new MountedPicker(container, options);
}

const effortOrder: readonly string[] = ["none", "minimal", "low", "medium", "high", "xhigh", "max"];
/** Display known efforts from shallow to deep, retaining unknown efforts in catalog order. */
const orderedEfforts = (efforts: readonly string[]): string[] => {
  const rank = (effort: string): number => {
    const index = effortOrder.indexOf(effort);
    return index < 0 ? effortOrder.length : index;
  };
  return [...efforts].sort((a, b) => rank(a) - rank(b));
};

const effortText = (effort: string): string => {
  const keys = { none: "chooser.none", minimal: "chooser.minimal", low: "chooser.low", medium: "chooser.medium", high: "chooser.high", xhigh: "chooser.xhigh", max: "chooser.max" } as const;
  return effort in keys ? t(keys[effort as keyof typeof keys]) : effort;
};

/** Radio rows are shared by the dialog and the catalog's pure rendering tests. */
/** A source's mark: its brand's, or for the models on this PC (and a custom connection) a plain one, so every row lines up. */
const sourceMark = (source: ChooserSource): string =>
  providerMark(source.kind, true) || `<span class="mono md" aria-hidden="true">${ico(source.id === "local-speech" ? "mic" : "plug")}</span>`;

export function listHTML(list: PickerList, selected = list.current, models: readonly CatalogModel[] = [], stars = false): string {
  return list.rows.map(({ model, usable, lead }) => {
    const chosen = model.id === selected;
    const descriptions = { "cli:claude:sonnet": "chooser.sonnet", "cli:claude:opus": "chooser.opus", "cli:claude:haiku": "chooser.haiku" } as const;
    const key = descriptions[model.id as keyof typeof descriptions];
    const description = key ? t(key) : getLocale() === "en" ? model.description : "";
    const sub = [description || [model.provider, model.context_length ? formatContext(model.context_length) : ""].filter(Boolean).join(" · "),
      !usable ? reasonText(fitFor(model, list.role).reason) : ""].filter(Boolean).join(" · ");
    const price = lead ? "" : chooserPrice(model, models, list.role);
    return `<div class="mc-item"><button type="button" role="radio" class="mc-row mp-opt${lead ? " mp-lead" : ""}" aria-checked="${chosen}" data-model="${esc(model.id)}" data-id="${esc(model.id)}"${chosen ? ' data-current="true"' : ""}${!usable ? ' disabled aria-disabled="true"' : ""}>` +
      `<span class="mc-radio" aria-hidden="true"></span>${lead ? "" : modelMark(model)}<span class="mc-model"><span class="mc-model-line"><strong>${esc(model.name)}</strong></span>` +
      `<span class="mc-description">${esc(sub)}</span></span>${price ? `<span class="mc-price num">${esc(price)}</span>` : ""}</button>` +
      (stars && !lead ? `<button type="button" class="mp-star" data-star="${esc(model.id)}" aria-pressed="${list.favorites.includes(model.id)}" aria-label="${esc(t(list.favorites.includes(model.id) ? "picker.favRemove" : "picker.favAdd", { p0: model.name }))}">${ico("star")}</button>` : "") + `</div>`;
  }).join("");
}

class MountedPicker implements ModelPicker {
  readonly button: HTMLButtonElement;
  private o: ModelPickerOptions;
  private dialog: Dialog | null = null;
  private source = "";
  private draft = "";
  private effort = "";
  private query = "";
  private sort: PickerSort = "name";
  private maker = "";
  private localDraft: string | null = null;
  private confirm: LocalConfirm | null = null;

  constructor(container: HTMLElement, options: ModelPickerOptions) {
    this.o = { ...options };
    if (!container.querySelector(".mp-trigger")) container.innerHTML = buttonHTML(options);
    this.button = container.querySelector<HTMLButtonElement>(".mp-trigger")!;
    mounted.set(this.button, this);
    this.paintButton();
    this.button.addEventListener("click", (event) => { event.stopPropagation(); this.open(); });
    this.button.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") { event.preventDefault(); this.open(); }
    });
    if (opened && !opened.button.isConnected && opened.o.role === options.role && opened.o.key === options.key) {
      const previous = opened;
      this.source = previous.source; this.draft = previous.draft; this.effort = previous.effort;
      this.query = previous.query; this.sort = previous.sort; this.maker = previous.maker; this.localDraft = previous.localDraft;
      previous.close(); this.show();
    }
  }

  get isOpen(): boolean { return this.dialog !== null; }
  update(changes: Partial<ModelPickerLook>): void {
    this.o = { ...this.o, ...changes, role: this.o.role }; this.paintButton();
    if (this.o.disabled) this.close(); else if (this.dialog) this.paint();
  }
  open(query = ""): void {
    if (this.o.disabled || this.dialog) return;
    const models = pickerModels(this.o);
    this.draft = concreteCurrent(this.o, models); this.effort = effortOf(this.o.current, models);
    this.localDraft = this.o.local?.active ?? null;
    // the list opens where the chosen model is; under an inheritance lead, where the model it stands for is
    const shown = this.inherits() ? this.effortModel()?.id ?? this.draft : this.draft;
    this.source = this.localDraft ? "local-speech" : models.find((m) => m.id === shown)?.provider_id ??
      (shown.startsWith("cli:") ? shown.split(":").slice(0, 2).join(":") : shown.startsWith("@") ? shown.slice(1).split(":")[0] : "openrouter");
    this.query = query; this.maker = ""; this.sort = "name";
    this.show();
  }
  close(): void { this.dialog?.close(); }
  private paintButton(): void {
    const face = buttonFace(this.o); this.button.innerHTML = face.inner;
    this.button.setAttribute("aria-label", face.label); this.button.disabled = !!this.o.disabled;
    this.button.setAttribute("aria-expanded", String(this.isOpen));
  }
  private show(): void {
    opened?.close();
    this.dialog = openDialog({ title: t("chooser.title"), cls: "model-chooser", body: "",
      actions: { secondary: `<span class="mc-selection"></span>${btn(esc(t("common.cancel")), "", ' data-dlg="close"')}`,
        primary: { label: esc(t("chooser.use")), attrs: ' data-dlg="use"' } },
      onAct: (act) => {
        if (act === "use") this.use();
        if (act === "sources") { this.close(); this.o.onSources?.(); }
        if (act === "login") {
          const id = this.source.slice(4) as CliBrainId;
          if (this.o.onCliAction) this.o.onCliAction(id);
        }
      },
      onClose: () => { this.dialog = null; if (opened === this) opened = null; this.paintButton(); if (this.button.isConnected) this.button.focus({ preventScroll: true }); },
    });
    opened = this;
    this.dialog.el.querySelector(".mb-dlg-head h2")?.insertAdjacentHTML("afterend", `<span class="mc-slot">${esc(t("chooser.slot", { name: this.o.label }))}</span>`);
    this.dialog.el.addEventListener("click", (event) => this.click(event));
    this.dialog.el.addEventListener("input", (event) => {
      const input = event.target as HTMLInputElement;
      if (input.matches(".mc-search")) { this.query = input.value; this.paintRows(); }
    });
    this.dialog.el.addEventListener("keydown", (event) => {
      const row = (event.target as HTMLElement).closest<HTMLButtonElement>(".mc-row, .lm-main");
      if (!row || !["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const rows = [...this.dialog!.el.querySelectorAll<HTMLButtonElement>(".mc-row:not(:disabled), .lm-main:not(:disabled)")];
      const at = rows.indexOf(row), next = moveIndex(rows.length, at, event.key);
      if (next !== null) { rows[next]?.click(); this.dialog?.el.querySelector<HTMLButtonElement>('.mc-row[aria-checked="true"], .lm-main[aria-checked="true"]')?.focus(); }
    });
    this.paint(); this.paintButton();
    const selected = this.dialog.el.querySelector<HTMLElement>('.mc-row[aria-checked="true"], .lm-main[aria-checked="true"]');
    // the list keeps its order: it opens where the chosen model is, not with the model moved to the top
    selected?.scrollIntoView?.({ block: "center" }); selected?.focus({ preventScroll: true });
  }
  private sources(): ChooserSource[] { return chooserSources(this.o); }
  private selectedSource(): ChooserSource | undefined { return this.sources().find((source) => source.id === this.source); }
  private paint(): void {
    if (!this.dialog) return;
    if (/^cli:[^:]+$/.test(this.draft)) this.draft = concreteCurrent({ ...this.o, current: this.draft });
    const sources = this.sources();
    if (!sources.some((source) => source.id === this.source)) this.source = sources[0]?.id ?? "";
    const saved = concreteCurrent(this.o), savedModel = pickerModels(this.o).find((m) => m.id === saved);
    const savedSource = savedModel ? connectionId(savedModel) : saved.startsWith("cli:") ? saved.split(":").slice(0, 2).join(":") : "";
    const rail = (["subscriptions", "api", "local"] as const).map((group) => {
      const items = sources.filter((source) => source.group === group);
      return !items.length ? "" : `<section><h3>${esc(t(groupKeys[group]))}</h3>` + items.map((source) => {
        const status = source.id.startsWith("cli:") ? !this.o.clis ? t("common.loading") : t(source.cli?.disconnected ? "brains.disconnected" : !source.cli?.installed ? "brains.missing" : source.cli.available ? "brains.ready" : "brains.signedOut") :
          t(source.group === "local" ? "chooser.localCount" : "chooser.modelCount", { n: source.id === "local-speech" && Array.isArray(this.o.local?.models) ? this.o.local.models.length : source.models.length });
        return `<button type="button" class="btn mc-source" data-source="${esc(source.id)}" aria-pressed="${this.source === source.id}">${sourceMark(source)}<span><strong>${esc(source.name)}</strong><small>${esc(status)}</small></span>${source.id === (this.o.local?.active ? "local-speech" : savedSource) ? '<i class="mc-current" aria-hidden="true"></i>' : ""}</button>`;
      }).join("") + `</section>`;
    }).join("");
    const inherit = this.o.lead ? `<button type="button" class="btn mc-inherit" data-model="${esc(this.o.lead.id)}" aria-pressed="${this.draft === this.o.lead.id}">${esc(this.o.lead.name)}</button>` : "";
    this.dialog.el.querySelector(".mb-dlg-body")!.innerHTML = `<div class="mc-layout"><aside class="mc-rail">${inherit}<div class="mc-source-list">${rail}</div>${btn(`${ico("plus")}${esc(t("chooser.sources"))}`, "", ' data-dlg="sources"')}</aside><div class="mc-pane"></div></div>`;
    this.paintPane();
    this.dialog.el.querySelector<HTMLElement>('.mc-source[aria-pressed="true"]')?.scrollIntoView?.({ block: "nearest" });
  }
  private paintPane(): void {
    const source = this.selectedSource(), pane = this.dialog?.el.querySelector(".mc-pane");
    if (!pane) return;
    if (!source) { pane.innerHTML = `<p class="mc-empty">${esc(t("chooser.empty"))}</p>`; this.paintFooter(); return; }
    const note = source.group === "subscriptions" ? "chooser.subscriptionNote" : source.group === "local" ? "chooser.localNote" : "chooser.apiNote";
    pane.innerHTML = `<header class="mc-pane-head">${sourceMark(source)}<span><h3>${esc(source.name)}</h3><p>${esc(t(note))}</p></span></header>`;
    if (source.group === "subscriptions" && !source.cli?.available) {
      pane.innerHTML += `<div class="mc-empty"><p>${esc(t(source.cli?.disconnected ? "brains.disconnected" : source.cli?.installed ? "chooser.loginNote" : "chooser.installNote"))}</p>${this.o.clis ? btn(esc(t(source.cli?.installed ? "brains.connect" : "brains.install")), "", ' data-dlg="login"') : esc(t("common.loading"))}</div>`;
      this.paintFooter(); return;
    }
    if (source.models.length > 12) {
      const makers = providerChoices(source.models);
      // the app's own dropdowns (ui.ts `sel` and its menu), as everywhere else, never the system's list
      pane.innerHTML += `<div class="mc-tools"><input type="search" class="mc-search" aria-label="${esc(t("chooser.search"))}" placeholder="${esc(t("chooser.search"))}" value="${esc(this.query)}">` +
        sel(sortChoices(), this.sort, t("chooser.sort"), " data-sort") + (makers.length > 2 ? sel(makers, this.maker, t("chooser.maker"), " data-maker") : "") + `</div>`;
    }
    pane.innerHTML += `<div class="mc-scroll"><div class="mc-list" role="radiogroup" aria-label="${esc(t("chooser.title"))}"></div></div><div class="mc-effort"></div>`;
    this.paintRows();
  }
  private paintRows(): void {
    const source = this.selectedSource(), listEl = this.dialog?.el.querySelector(".mc-list");
    if (!listEl || !source) return;
    if (source.id === "local-speech" && this.o.local) {
      listEl.innerHTML = localListHTML(this.o.local, this.confirm, true);
      listEl.querySelectorAll<HTMLElement>('[data-act="use"]').forEach((row) => {
        const selected = row.dataset.name === this.localDraft;
        row.setAttribute("aria-current", String(selected)); row.setAttribute("aria-checked", String(selected));
        const check = row.querySelector(".ck"); if (check) check.innerHTML = selected ? CHECK : "";
      });
    } else {
      const list = new PickerList(this.o.role, source.models, this.draft, { query: this.query, sort: this.sort, provider: this.maker }, this.o.favorites ?? []);
      // the favourites always come first, under their own heading; the rest keep the chosen order
      const favorites = list.rows.filter((row) => row.favorite && !row.lead);
      if (favorites.length && favorites.length < list.rows.length && !this.query) {
        const all = list.rows; list.rows = favorites;
        const first = listHTML(list, this.draft, pickerModels(this.o), !!this.o.onFavorite);
        list.rows = all.filter((row) => !favorites.includes(row));
        listEl.innerHTML = `<h4>${esc(t("chooser.favorites"))}</h4>${first}<h4>${esc(t("chooser.all"))}</h4>${listHTML(list, this.draft, pickerModels(this.o), !!this.o.onFavorite)}`;
      } else listEl.innerHTML = listHTML(list, this.draft, pickerModels(this.o), !!this.o.onFavorite);
      if (!list.rows.length) listEl.innerHTML = `<p class="mc-empty">${esc(t(this.o.loading ? "common.loading" : "chooser.empty"))}</p>`;
    }
    const model = this.effortModel();
    const supported = model?.reasoning_efforts ?? [];
    const levels = this.inherits() || source.models.some((m) => m.id === model?.id) ? orderedEfforts(supported) : [];
    if (!supported.includes(this.effort)) this.effort = (this.o.defaultEffort && supported.includes(this.o.defaultEffort) ? this.o.defaultEffort : model?.default_effort) ?? supported[0] ?? "";
    const efforts = this.dialog!.el.querySelector(".mc-effort")!;
    efforts.innerHTML = levels.length ? `<span>${esc(t("chooser.effort"))}</span><div class="mc-segments" role="group" aria-label="${esc(t("chooser.effort"))}">${levels.map((level) => `<button type="button" class="btn sm" data-effort="${esc(level)}" aria-pressed="${this.effort === level}">${esc(effortText(level))}</button>`).join("")}</div>` : "";
    this.paintFooter();
  }
  private paintFooter(): void {
    if (!this.dialog) return;
    const source = this.selectedSource(), model = pickerModels(this.o).find((m) => m.id === this.draft);
    const local = source?.id === "local-speech";
    const localModel = Array.isArray(this.o.local?.models) ? this.o.local.models.find((m) => m.name === this.localDraft) : null;
    const lead = this.o.lead?.id === this.draft;
    const name = local ? localModel?.title : lead ? this.o.lead?.name : model?.name;
    const modelSource = local ? t("picker.tabLocal") : model ? connectionName(model) : "";
    const efforts = local ? [] : this.effortModel()?.reasoning_efforts ?? [];
    this.dialog.el.querySelector(".mc-selection")!.textContent = name ? `${t("chooser.selection")}${name}${modelSource ? ` · ${modelSource}` : ""}${efforts.includes(this.effort) ? ` · ${t("chooser.effort")} ${effortText(this.effort)}` : ""}` : "";
    const available = model && usableFor(model, this.o.role);
    const use = this.dialog.el.querySelector<HTMLButtonElement>('[data-dlg="use"]')!;
    use.textContent = t(local && localModel && !localModel.ready ? "chooser.downloadUse" : "chooser.use");
    use.disabled = local ? !localModel || !!this.o.local?.download : !lead && !available;
  }
  private click(event: MouseEvent): void {
    const dropdown = (event.target as Element).closest<HTMLButtonElement>(".mc-tools .sel");
    if (dropdown) {
      const sorting = dropdown.matches("[data-sort]");
      const source = this.selectedSource();
      const choices = sorting ? sortChoices() : providerChoices(source?.models ?? []);
      openMenu(dropdown, choices, sorting ? this.sort : this.maker, (value) => {
        if (sorting) this.sort = value as PickerSort; else this.maker = value;
        const label = choices.find((choice) => choice.value === value)?.label ?? value;
        dropdown.querySelector("span")!.textContent = label;
        dropdown.title = label;
        this.paintRows();
      });
      return;
    }
    const target = (event.target as Element).closest<HTMLElement>("[data-source], [data-model], [data-effort], [data-act], [data-star]");
    if (!target || (target as HTMLButtonElement).disabled) return;
    if (target.dataset.source !== undefined) {
      this.source = target.dataset.source; this.query = ""; this.maker = ""; this.confirm = null; this.paint();
      // the source holding the chosen model opens where that model is
      this.dialog?.el.querySelector<HTMLElement>('.mc-list .mc-row[aria-checked="true"]')?.scrollIntoView?.({ block: "center" });
    }
    if (target.dataset.model !== undefined) {
      this.draft = target.dataset.model; this.effort = "";
      this.dialog?.el.querySelector(".mc-inherit")?.setAttribute("aria-pressed", String(this.draft === this.o.lead?.id));
      this.paintRows(); this.paintFooter();
    }
    if (target.dataset.effort) { this.effort = target.dataset.effort; this.paintRows(); }
    if (target.dataset.star) {
      const id = target.dataset.star, on = !this.o.favorites?.includes(id);
      this.o.favorites = withFavorite(this.o.favorites ?? [], id, on);
      this.paintRows(); this.o.onFavorite?.(id, on);
    }
    const act = target.dataset.act, name = target.dataset.name;
    if (act === "use" && name) { this.localDraft = name; this.paintRows(); }
    if (act === "cancel") this.o.onCancelLocal?.();
    if (act === "delete" && name) { this.confirm = { kind: "delete", name }; this.paintRows(); }
    if (act === "dismiss") { this.confirm = null; this.paintRows(); }
    if (act === "confirm" && name) { this.confirm = null; this.o.onDeleteLocal?.(name); }
  }
  private use(): void {
    if (this.dialog?.el.querySelector<HTMLButtonElement>('[data-dlg="use"]')?.disabled) return;
    if (this.source === "local-speech" && this.localDraft) {
      const model = Array.isArray(this.o.local?.models) ? this.o.local.models.find((m) => m.name === this.localDraft) : null;
      if (model?.ready) this.close();
      this.o.onUseLocal?.(this.localDraft); return;
    }
    const model = pickerModels(this.o).find((m) => m.id === this.draft);
    const effort = this.effortModel()?.reasoning_efforts?.includes(this.effort) ? this.effort : "";
    const ref = model?.effort_models?.[effort] ?? (effort ? `${this.draft}?effort=${effort}` : this.draft);
    this.close(); if (ref !== this.o.current || this.o.local?.active) this.o.onChoose(ref);
  }
  /** Whether the inheritance lead is chosen, for a picker whose lead stands for another model (快速提問's 「與回答相同」). */
  private inherits(): boolean { return !!this.o.inheritFrom && this.draft === this.o.lead?.id; }
  /** The model whose effort row the list shows: the one chosen, or the one the chosen lead stands for. */
  private effortModel(): CatalogModel | undefined {
    // Cleanup uses the catalog policy in complete_json, never a chosen effort or ref suffix.
    if (this.o.role === "cleanup") return undefined;
    const id = this.inherits() ? concreteCurrent({ ...this.o, current: this.o.inheritFrom! }) : this.draft;
    return pickerModels(this.o).find((m) => m.id === id);
  }
}
