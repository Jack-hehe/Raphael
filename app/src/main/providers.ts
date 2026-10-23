// Settings > Models: provider metadata is public; credentials live only in the input until sent.
import * as api from "./api";
import { t } from "./i18n";
import { providerMark } from "./provider-marks";
import type { MainEvent, ModelProvider, ProviderKind, ProviderField, ProviderPatch, ProviderModel, ProviderTest } from "./types";
import { DISCONNECT_CONFIRM_MS, btn, esc, fieldHTML, ico, iconButton, tg } from "./ui";

export interface ProvidersView {
  providers: ModelProvider[];
  kinds: ProviderKind[];
  state: "loading" | "ready" | "failed";
  busy: boolean;
  open: { kind: string; id: string | null; replacing: boolean } | null;
  error: "error" | "required" | null;
  result: ProviderTest | null;
  models: ProviderModel[] | "loading" | "failed";
  query: string;
  confirming: boolean;
  dirty: boolean;
}
export const emptyProvidersView = (): ProvidersView => ({ providers: [], kinds: [], state: "loading", busy: false, open: null, error: null, result: null, models: [], query: "", confirming: false, dirty: false });

const kindOf = (v: ProvidersView): ProviderKind | undefined => v.kinds.find((k) => k.kind === v.open?.kind);
const providerOf = (v: ProvidersView): ModelProvider | undefined => v.providers.find((p) => p.id === v.open?.id);
const inputId = (v: ProvidersView, field: string): string => `pv-${v.open?.id ?? v.open?.kind}-${field}`;
const secret = (field: string): boolean => field === "key" || field === "key_id";
const FIELD_TITLES = {
  name: () => t("providers.name"), key: () => t("providers.key"), base_url: () => t("providers.base_url"),
  api_version: () => t("providers.api_version"), region: () => t("providers.region"), key_id: () => t("providers.key_id"),
};
const GROUP_TITLES = { official: () => t("providers.official"), gateway: () => t("providers.gateway"), local: () => t("providers.local") };
const errorText = (error: ProvidersView["error"]): string => t(error === "required" ? "providers.required" : "providers.error");

function dialogHTML(v: ProvidersView): string {
  const kind = kindOf(v);
  if (!v.open || !kind) return "";
  const provider = providerOf(v);
  const disabled = v.busy ? " disabled" : "";
  const fields = (["name", ...kind.fields] as (ProviderField | "name")[]).map((field) => {
    const title = field === "key" && kind.kind === "bedrock" ? t("providers.secret") : field === "key" && kind.kind === "openai_compatible" ? t("providers.keyOptional") : FIELD_TITLES[field]();
    const value = field === "name" ? provider?.name ?? kind.name : field === "base_url" ? provider?.base_url ?? kind.default_base_url ?? "" : "";
    const control = secret(field) && provider?.has_key && !v.open!.replacing
      ? `<span class="val">${esc(t("providers.set"))}</span>${btn(esc(t("providers.replace")), "", ` data-pv="replace" data-k="pv-replace-${field}"${disabled}`)}`
      : fieldHTML(title, value, { id: inputId(v, field), type: secret(field) ? "password" : "text", disabled: v.busy });
    return `<div class="pv-field"><label class="pv-label" for="${esc(inputId(v, field))}">${esc(title)}</label><div class="svc-line">${control}</div></div>`;
  }).join("");
  return `<div class="cs-scrim" data-pv="scrim" data-dialog="provider:${esc(v.open.id ?? kind.kind)}"><div class="cs-dialog pv-dialog" id="pv-dialog" role="dialog" aria-modal="true" aria-labelledby="pv-title" tabindex="-1" data-trap>` +
    `<div class="cs-dlg-head"><span class="ic">${providerMark(kind.kind)}</span><h2 id="pv-title">${esc(provider?.name ?? kind.name)}</h2><span class="sp"></span>${iconButton("x", t("providers.close"), ' data-pv="close" data-k="pv-close"')}</div>` +
    fields + (v.error ? `<p class="note" role="alert">${esc(errorText(v.error))}</p>` : "") +
    `<div class="svc-acts">${btn(esc(t("providers.save")), "pri", ` data-pv="save" data-k="pv-save"${disabled}`)}` +
    btn(esc(t("providers.test")), "", ` data-pv="test" data-k="pv-test"${v.busy || !v.open.id || v.dirty ? " disabled" : ""}`) +
    (v.open.id ? btn(esc(t(v.confirming ? "providers.confirmRemove" : "providers.remove")), v.confirming ? "danger" : "", ` data-pv="remove" data-k="pv-remove"${disabled}`) : "") + `</div>` +
    (v.dirty || !v.open.id ? `<p class="note">${esc(t("providers.saveFirst"))}</p>` : "") +
    (v.result ? `<p class="note" role="status">${esc(v.result.ok ? t("providers.count", { p0: v.result.models }) : t(v.result.kind === "auth" ? "providers.auth" : v.result.kind === "offline" ? "providers.offline" : "providers.error"))}</p>` : "") +
    `<section class="pv-models"><h3>${esc(t("providers.models"))}</h3>` +
    (Array.isArray(v.models) && v.models.length > 10 ? fieldHTML(t("providers.search"), v.query, { id: "pv-model-search", icon: "search" }) : "") +
    `<div class="pv-model-list">${modelsHTML(v)}</div></section></div></div>`;
}

function modelsHTML(v: ProvidersView): string {
  if (!Array.isArray(v.models)) return `<p class="note">${esc(t(v.models === "loading" ? "providers.loading" : "providers.error"))}</p>`;
  if (!v.models.length) return `<p class="note">${esc(t("providers.noModels"))}</p>`;
  const query = v.query.toLowerCase();
  return `<ul>` + v.models.filter((m) => `${m.name} ${m.id}`.toLowerCase().includes(query)).map((m) =>
    `<li><span class="pv-model-name">${esc(m.name)}</span><span class="pv-model-id">${esc(m.id)}</span></li>`).join("") + `</ul>`;
}

function statusNote(provider: ModelProvider): string {
  if (provider.status === "ok") return t("providers.count", { p0: provider.models });
  return t(provider.status === "auth" ? "providers.auth" : provider.status === "offline" ? "providers.offline" : "providers.unknown");
}

export function providersHTML(v: ProvidersView): string {
  const rows = v.providers.map((p) => `<div class="cs-item pv-item" data-pv="open" data-id="${esc(p.id)}">` +
    btn(`<span class="ic">${providerMark(p.kind)}</span><span class="tx"><span class="nm">${esc(p.name)}</span><span class="ds">${esc(statusNote(p))}</span></span>`, "cs-open", ` data-pv="open" data-id="${esc(p.id)}" data-k="pv-open:${esc(p.id)}" aria-haspopup="dialog"`) +
    tg(p.enabled, t("providers.switch", { p0: p.name }), ` data-pv="toggle" data-id="${esc(p.id)}" data-k="pv-toggle:${esc(p.id)}"${v.busy ? " disabled" : ""}`) +
    `<span class="chev" aria-hidden="true">${ico("right")}</span></div>`).join("");
  const groups = (["official", "gateway", "local"] as const).map((group) => `<section class="sgroup"><h2>${esc(GROUP_TITLES[group]())}</h2><div class="cs-list">` +
    v.kinds.filter((k) => k.group === group).map((k) => `<div class="cs-item pv-add"><span class="cs-main"><span class="ic">${providerMark(k.kind)}</span><span class="tx"><span class="nm">${esc(k.name)}</span>${!k.available ? `<span class="ds">${esc(t("providers.soon"))}</span>` : ""}</span></span>` +
      btn(esc(t("providers.connect")), "", ` data-pv="add" data-id="${esc(k.kind)}" data-k="pv-add:${esc(k.kind)}"${k.available && !v.busy ? "" : " disabled"}`) + `</div>`).join("") + `</div></section>`).join("");
  return `<div class="pv"><section class="sgroup"><h2>${esc(t("providers.connected"))}</h2>` +
    (v.state === "ready" ? rows ? `<div class="cs-list">${rows}</div>` : `<p class="note">${esc(t("providers.empty"))}</p>` : `<p class="note" role="status">${esc(t(v.state === "failed" ? "providers.error" : "providers.loading"))}</p>`) +
    `</section>` + (v.error && !v.open ? `<p class="note" role="alert">${esc(errorText(v.error))}</p>` : "") +
    (v.state === "failed" ? btn(esc(t("providers.retry")), "", ' data-pv="retry" data-k="pv-retry"') : "") +
    `<h2 class="pv-add-title">${esc(t("providers.add"))}</h2>${groups}</div>` + dialogHTML(v);
}

export interface ProvidersHost { root: HTMLElement; visible(): boolean; redraw(): void; }
export function createProviders(host: ProvidersHost) {
  let state = emptyProvidersView();
  let loads = 0;
  let modelLoads = 0;
  let confirmTimer: ReturnType<typeof setTimeout> | null = null;
  const redraw = () => {
    if (!host.visible()) return;
    // Carry drafts between DOM drawings, never into page state or HTML attributes.
    const inputs = [...host.root.querySelectorAll<HTMLInputElement>(".pv-field input")];
    const drafts = new Map(inputs.map((input) => [input.id, input.value]));
    const active = document.activeElement as HTMLElement | null;
    const selection = active instanceof HTMLInputElement ? [active.selectionStart, active.selectionEnd] : null;
    host.redraw();
    for (const input of host.root.querySelectorAll<HTMLInputElement>(".pv-field input")) {
      if (drafts.has(input.id)) input.value = drafts.get(input.id)!;
      if (active?.id === input.id) { input.focus(); if (selection) input.setSelectionRange(selection[0], selection[1]); }
    }
    if (active && host.root.querySelector("#pv-dialog")) {
      const fresh = [...host.root.querySelectorAll<HTMLElement>("[id], [data-k]")].find((el) => active.id ? el.id === active.id : active.dataset.k && el.dataset.k === active.dataset.k);
      (fresh && !(fresh instanceof HTMLButtonElement && fresh.disabled) ? fresh : host.root.querySelector<HTMLElement>("#pv-dialog"))?.focus();
    }
  };
  async function load() {
    const mine = ++loads;
    try {
      const [providers, kinds] = await Promise.all([api.providersList(), api.providersKinds()]);
      if (mine !== loads) return;
      state = { ...state, providers: providers.providers, kinds: kinds.kinds, state: "ready" };
      if (state.open?.id && !state.providers.some((p) => p.id === state.open?.id)) close();
    } catch { if (mine !== loads) return; state = { ...state, state: "failed" }; }
    redraw();
  }
  function show(kind: string, id: string | null) {
    clearConfirmation();
    modelLoads++;
    state = { ...state, open: { kind, id, replacing: false }, error: null, result: null, models: [], query: "", dirty: false };
    redraw(); host.root.querySelector<HTMLElement>("#pv-dialog")?.focus();
    if (id) void loadModels(id);
  }
  function clearConfirmation() {
    if (confirmTimer !== null) clearTimeout(confirmTimer);
    confirmTimer = null; state.confirming = false;
  }
  function close() {
    const from = state.open;
    clearConfirmation(); modelLoads++; state.open = null; redraw();
    const action = from?.id ? "open" : "add";
    const id = from?.id ?? from?.kind;
    [...host.root.querySelectorAll<HTMLElement>(`button[data-pv="${action}"]`)].find((el) => el.dataset.id === id)?.focus();
  }
  async function remove() {
    const open = state.open;
    const id = open?.id;
    if (!id || state.busy) return;
    if (!state.confirming) {
      state.confirming = true;
      confirmTimer = setTimeout(() => { clearConfirmation(); redraw(); }, DISCONNECT_CONFIRM_MS);
      redraw(); return;
    }
    clearConfirmation(); state.busy = true; state.error = null; redraw();
    try {
      const result = await api.providersRemove(id);
      if (!result.removed) throw new Error("not_removed");
      if (state.open === open) close();
      await load();
    } catch { if (state.open === open) state.error = "error"; }
    finally { state.busy = false; redraw(); }
  }
  async function loadModels(id: string) {
    const mine = ++modelLoads;
    state.models = "loading"; redraw();
    try {
      const result = await api.providersModels(id);
      if (mine !== modelLoads || state.open?.id !== id) return;
      state.models = result.models;
    } catch { if (mine !== modelLoads || state.open?.id !== id) return; state.models = "failed"; }
    redraw();
  }
  async function testConnection() {
    const open = state.open;
    if (!open?.id || state.busy || state.dirty) return;
    state.busy = true; state.result = null; state.error = null; redraw();
    try {
      const result = await api.providersTest(open.id);
      if (state.open === open) state.result = result;
      await load();
      if (state.open === open) await loadModels(open.id);
    } catch { if (state.open === open) state.error = "error"; }
    finally { state.busy = false; redraw(); }
  }
  async function save() {
    const open = state.open;
    const kind = kindOf(state);
    if (!open || !kind || state.busy) return;
    const patch: ProviderPatch = {};
    let valid = true;
    for (const field of ["name", ...kind.fields] as (ProviderField | "name")[]) {
      const input = [...host.root.querySelectorAll<HTMLInputElement>(".pv-field input")].find((el) => el.id === inputId(state, field));
      if (!input) continue;
      const value = input.value.trim();
      if (secret(field)) input.value = "";
      if (field === "base_url" && value) {
        try { const url = new URL(value); if (!["http:", "https:"].includes(url.protocol)) valid = false; }
        catch { valid = false; }
      }
      const optionalKey = field === "key" && kind.kind === "openai_compatible";
      if (!value && !optionalKey && (!open.id || field === "name" || field === "base_url" || open.replacing && secret(field))) valid = false;
      if (value || field === "base_url") patch[field] = value;
    }
    if (!valid) { state.error = "required"; redraw(); return; }
    state.busy = true; state.error = null; redraw();
    try {
      const result = open.id ? await api.providersUpdate({ id: open.id, ...patch }) : await api.providersAdd({ kind: kind.kind, ...patch });
      if (state.open === open) { state.open = { ...open, id: result.provider.id, replacing: false }; state.dirty = false; state.result = null; }
      state.providers = [...state.providers.filter((p) => p.id !== result.provider.id), result.provider];
      await load();
      if (state.open?.id === result.provider.id) await loadModels(result.provider.id);
    } catch { if (state.open === open) state.error = "error"; }
    finally { state.busy = false; redraw(); }
  }
  async function toggle(id: string) {
    const provider = state.providers.find((p) => p.id === id);
    if (!provider || state.busy) return;
    state.busy = true; state.error = null; redraw();
    try { await api.providersUpdate({ id, enabled: !provider.enabled }); await load(); }
    catch { state.error = "error"; }
    finally { state.busy = false; redraw(); }
  }
  return {
    view: () => state, load,
    click(target: Element): boolean {
      const button = target.closest<HTMLElement>("[data-pv]");
      if (!button) return false;
      if (button instanceof HTMLButtonElement && button.disabled) return true;
      if (button.dataset.pv !== "remove") clearConfirmation();
      if (button.dataset.pv === "toggle") void toggle(button.dataset.id!);
      if (button.dataset.pv === "add" && state.kinds.some((k) => k.kind === button.dataset.id && k.available)) show(button.dataset.id!, null);
      if (button.dataset.pv === "open") {
        const provider = state.providers.find((p) => p.id === button.dataset.id);
        if (provider) show(provider.kind, provider.id);
      }
      if (button.dataset.pv === "replace" && state.open) { state.open.replacing = true; state.dirty = true; state.result = null; redraw(); }
      if (button.dataset.pv === "save") void save();
      if (button.dataset.pv === "test") void testConnection();
      if (button.dataset.pv === "remove") void remove();
      if (button.dataset.pv === "retry") void load();
      if (button.dataset.pv === "close" || button.dataset.pv === "scrim" && target === button) close();
      return true;
    },
    input(target: Element): void {
      if (target.closest(".pv-field") && state.open) {
        if (!state.dirty) { state.dirty = true; state.result = null; redraw(); }
        return;
      }
      if (!(target instanceof HTMLInputElement) || target.id !== "pv-model-search") return;
      state.query = target.value;
      const list = host.root.querySelector(".pv-model-list");
      if (list) list.innerHTML = modelsHTML(state);
    },
    followEvent(event: MainEvent): void {
      if (event.type !== "ProvidersChanged" || !host.visible()) return;
      void load().then(() => { if (state.open?.id) void loadModels(state.open.id); });
    },
    key(event: KeyboardEvent): boolean {
      if (!state.open) return false;
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close(); return true; }
      if (event.key === "Enter" && (event.target as HTMLElement | null)?.closest(".pv-field")) {
        event.preventDefault(); void save(); return true;
      }
      return false;
    },
    reset(): void {
      loads++; modelLoads++; clearConfirmation(); state.open = null; state.error = null; state.result = null;
      for (const input of host.root.querySelectorAll<HTMLInputElement>('.pv-field input[type="password"]')) input.value = "";
    },
  };
}
