// Settings > Models: provider metadata is public; credentials live only in the input until sent.
import * as api from "./api";
import { t } from "./i18n";
import { providerMark } from "./provider-marks";
import type { MainEvent, ModelProvider, ProviderKind, ProviderField, ProviderPatch, ProviderModel, ProviderTest } from "./types";
import { DISCONNECT_CONFIRM_MS, btn, esc, fieldHTML, ico, iconButton, openMenu, sel, tg } from "./ui";

export interface ProvidersView {
  providers: ModelProvider[];
  kinds: ProviderKind[];
  state: "loading" | "ready" | "failed";
  busy: boolean;
  open: { kind: string; id: string | null; replacing: boolean } | null;
  error: "error" | "required" | "account" | null;
  result: ProviderTest | null;
  models: ProviderModel[] | "loading" | "failed";
  query: string;
  kindQuery?: string;
  adding?: boolean;
  accountSet?: boolean;
  fieldValues?: Partial<Record<"region" | "location", string>>;
  confirming: boolean;
  dirty: boolean;
}
export const emptyProvidersView = (): ProvidersView => ({ providers: [], kinds: [], state: "loading", busy: false, open: null, error: null, result: null, models: [], query: "", confirming: false, dirty: false });

const kindOf = (v: ProvidersView): ProviderKind | undefined => v.kinds.find((k) => k.kind === v.open?.kind);
const providerOf = (v: ProvidersView): ModelProvider | undefined => v.providers.find((p) => p.id === v.open?.id);
const inputId = (v: ProvidersView, field: string): string => `pv-${v.open?.id ?? v.open?.kind}-${field}`;
const secret = (field: string): boolean => ["key", "key_id", "secret", "service_account"].includes(field);
const FIELD_TITLES = {
  name: () => t("providers.name"), key: () => t("providers.key"), base_url: () => t("providers.base_url"),
  api_version: () => t("providers.api_version"), region: () => t("providers.region"), key_id: () => t("providers.key_id"),
  secret: () => t("providers.secret"), service_account: () => t("providers.service_account"), project: () => t("providers.project"), location: () => t("providers.location"),
};
const FIELD_CHOICES = {
  region: ["us-east-1", "us-east-2", "us-west-2", "ca-central-1", "eu-west-1", "eu-west-2", "eu-west-3", "eu-central-1", "eu-central-2", "eu-north-1", "ap-northeast-1", "ap-northeast-2", "ap-south-1", "ap-southeast-1", "ap-southeast-2", "sa-east-1"],
  location: ["global", "us-central1", "us-east1", "us-east4", "us-west1", "us-west4", "northamerica-northeast1", "europe-west1", "europe-west2", "europe-west3", "europe-west4", "europe-west9", "asia-east1", "asia-northeast1", "asia-northeast3", "asia-south1", "asia-southeast1", "australia-southeast1"],
};
const choicesFor = (field: "region" | "location") => FIELD_CHOICES[field].map((value) => ({ value, label: value }));
const GROUP_TITLES = { official: () => t("providers.official"), gateway: () => t("providers.gateway"), local: () => t("providers.local") };
const POPULAR = ["openrouter", "litellm", "portkey", "vercel"];
function description(kind: ProviderKind): string {
  if (kind.kind === "openai_compatible") return t("providers.compatibleNote");
  if (kind.group === "official") return t("providers.officialAPI", { p0: kind.name });
  if (kind.group === "local") return t("providers.localServer");
  return t(POPULAR.includes(kind.kind) || kind.kind === "bifrost" ? "providers.modelGateway" : "providers.cloudInference");
}

function directoryHTML(v: ProvidersView): string {
  let count = 0;
  const groups = (["official", "gateway", "local"] as const).map((group) => {
    const kinds = v.kinds.filter((k) => k.group === group && k.name.toLowerCase().includes((v.kindQuery ?? "").trim().toLowerCase()));
    if (group === "gateway") kinds.sort((a, b) => {
      const rank = (id: string) => POPULAR.includes(id) ? POPULAR.indexOf(id) : POPULAR.length;
      return rank(a.kind) - rank(b.kind) || a.name.localeCompare(b.name, "en");
    });
    count += kinds.length;
    return kinds.length ? `<section class="sgroup"><h3>${esc(GROUP_TITLES[group]())}</h3><div class="pv-grid">` + kinds.map((k) => {
      const note = description(k);
      return `<div class="pv-card pv-add"><span class="ic">${providerMark(k.kind)}</span><span class="tx"><span class="nm">${esc(k.name)}</span><span class="ds" title="${esc(note)}">${esc(note)}</span></span>` +
        btn(esc(t("providers.connect")), "", ` data-pv="add" data-id="${esc(k.kind)}" data-k="pv-add:${esc(k.kind)}" aria-label="${esc(t("providers.connect") + " " + k.name)}"${k.available ? "" : ` title="${esc(t("providers.soon"))}"`}${k.available && !v.busy ? "" : " disabled"}`) + `</div>`;
    }).join("") + `</div></section>` : "";
  }).join("");
  return `<div class="cs-scrim" data-pv="scrim" data-dialog="provider:add"><div class="cs-dialog pv-dialog pv-catalog" id="pv-dialog" role="dialog" aria-modal="true" aria-labelledby="pv-title" tabindex="-1" data-trap>` +
    `<div class="cs-dlg-head"><h2 id="pv-title">${esc(t("providers.add"))}</h2><span class="sp"></span>${iconButton("x", t("providers.close"), ' data-pv="close" data-k="pv-close"')}</div>` +
    `<p class="note">${esc(t("providers.choose"))}</p>${fieldHTML(t("providers.searchKinds"), v.kindQuery ?? "", { id: "pv-kind-search", icon: "search" })}` +
    `<div class="pv-directory">${groups || `<p class="note" role="status">${esc(t("providers.noMatches"))}</p>`}</div><p class="pv-results note" role="status">${esc(t("providers.results", { p0: count }))}</p></div></div>`;
}
const errorText = (error: ProvidersView["error"]): string => t(error === "account" ? "providers.invalidAccount" : error === "required" ? "providers.required" : "providers.error");
function validAccount(value: string): boolean {
  try {
    const data: unknown = JSON.parse(value);
    return typeof data === "object" && data !== null && "client_email" in data && "private_key" in data &&
      typeof data.client_email === "string" && !!data.client_email.trim() && typeof data.private_key === "string" && !!data.private_key.trim();
  } catch { return false; }
}

function dialogHTML(v: ProvidersView): string {
  const kind = kindOf(v);
  if (!v.open) return v.adding ? directoryHTML(v) : "";
  if (!kind) return "";
  const provider = providerOf(v);
  const disabled = v.busy ? " disabled" : "";
  const fields = (["name", ...kind.fields] as (ProviderField | "name")[]).map((field) => {
    const title = field === "key" && kind.kind === "bedrock" ? t("providers.secret") : field === "key" && kind.kind === "openai_compatible" ? t("providers.keyOptional") : FIELD_TITLES[field]();
    const value = field === "name" ? provider?.name ?? kind.name : field === "base_url" ? provider?.base_url ?? kind.default_base_url ?? "" : field === "region" || field === "location" ? v.fieldValues?.[field] ?? (v.open!.id ? "" : field === "region" ? "us-east-1" : "us-central1") : "";
    const control = secret(field) && (provider?.has_key && !v.open!.replacing || field === "service_account" && v.accountSet)
      ? `<span class="val">${esc(t("providers.set"))}</span>${btn(esc(t("providers.replace")), "", ` data-pv="replace" data-k="pv-replace-${field}"${disabled}`)}`
      : field === "service_account"
        ? `<div class="pv-account">${btn(esc(t("providers.chooseFile")), "", ` data-pv="file" data-k="pv-file"${disabled}`)}<input type="file" class="pv-file" accept=".json,application/json" hidden${disabled}>` +
          `<label class="field"><textarea id="${esc(inputId(v, field))}" rows="3" autocomplete="off" spellcheck="false" aria-label="${esc(t("providers.pasteAccount"))}" placeholder="${esc(t("providers.pasteAccount"))}"${disabled}></textarea></label></div>`
      : field === "region" || field === "location"
        ? sel(value ? choicesFor(field) : [{ value: "", label: t("providers.unchanged") }, ...choicesFor(field)], value, title, ` id="${esc(inputId(v, field))}" data-pv="field" data-pv-field="${field}" data-value="${esc(value)}"${disabled}`)
        : fieldHTML(title, value, { id: inputId(v, field), type: secret(field) ? "password" : "text", disabled: v.busy });
    return `<div class="pv-field"><label class="pv-label" for="${esc(inputId(v, field))}">${esc(title)}</label><div class="svc-line">${control}</div></div>`;
  }).join("");
  return `<div class="cs-scrim" data-pv="scrim" data-dialog="provider:${esc(v.adding ? "add" : v.open.id ?? kind.kind)}"><div class="cs-dialog pv-dialog${v.adding ? " pv-catalog" : ""}" id="pv-dialog" role="dialog" aria-modal="true" aria-labelledby="pv-title" tabindex="-1" data-trap>` +
    `<div class="cs-dlg-head">${v.adding ? iconButton("left", t("providers.back"), ` data-pv="back" data-k="pv-back"${disabled}`) : ""}<span class="ic">${providerMark(kind.kind)}</span><h2 id="pv-title">${esc(provider?.name ?? kind.name)}</h2><span class="sp"></span>${iconButton("x", t("providers.close"), ' data-pv="close" data-k="pv-close"')}</div><div class="pv-setup">` +
    (kind.local_context_hint ? `<p class="note">${esc(t("providers.contextHint"))}</p>` : "") +
    fields + (kind.docs_url ? btn(esc(t("providers.getKey")), "", ` data-pv="docs" data-k="pv-docs"${disabled}`) : "") + (v.error ? `<p class="note" role="alert">${esc(errorText(v.error))}</p>` : "") +
    `<div class="svc-acts">${btn(esc(t("providers.save")), "pri", ` data-pv="save" data-k="pv-save"${disabled}`)}` +
    btn(esc(t("providers.test")), "", ` data-pv="test" data-k="pv-test"${v.busy || !v.open.id || v.dirty ? " disabled" : ""}`) +
    (v.open.id ? btn(esc(t(v.confirming ? "providers.confirmRemove" : "providers.remove")), v.confirming ? "danger" : "", ` data-pv="remove" data-k="pv-remove"${disabled}`) : "") + `</div>` +
    (v.dirty || !v.open.id ? `<p class="note">${esc(t("providers.saveFirst"))}</p>` : "") +
    (v.result ? `<p class="note" role="status">${esc(v.result.ok ? t("providers.count", { p0: v.result.models }) : t(v.result.kind === "auth" ? "providers.auth" : v.result.kind === "offline" ? "providers.offline" : "providers.error"))}</p>` : "") +
    `<section class="pv-models"><h3>${esc(t("providers.models"))}</h3>` +
    (Array.isArray(v.models) && v.models.length > 10 ? fieldHTML(t("providers.search"), v.query, { id: "pv-model-search", icon: "search" }) : "") +
    `<div class="pv-model-list">${modelsHTML(v)}</div></section></div></div></div>`;
}

function modelsHTML(v: ProvidersView): string {
  if (!Array.isArray(v.models)) return `<p class="note">${esc(t(v.models === "loading" ? "providers.loading" : "providers.error"))}</p>`;
  if (!v.models.length) return `<p class="note">${esc(t("providers.noModels"))}</p>`;
  const query = v.query.toLowerCase();
  return `<ul>` + v.models.filter((m) => `${m.name} ${m.id}`.toLowerCase().includes(query)).map((m) =>
    `<li><span class="pv-model-name">${esc(m.name)}</span><span class="pv-model-id">${esc(m.id)}</span>${m.context_warning ? `<span class="pv-context-warning">${esc(t("providers.contextWarning"))}</span>` : ""}</li>`).join("") + `</ul>`;
}

function statusNote(provider: ModelProvider): string {
  if (provider.status === "ok") return t("providers.count", { p0: provider.models });
  return t(provider.status === "auth" ? "providers.auth" : provider.status === "offline" ? "providers.offline" : "providers.unknown");
}

export function providersHTML(v: ProvidersView): string {
  const rows = v.providers.map((p) => `<div class="pv-card pv-item">` +
    btn(`<span class="ic">${providerMark(p.kind)}</span><span class="tx"><span class="nm">${esc(p.name)}</span><span class="ds">${esc(statusNote(p))}</span></span><span class="chev" aria-hidden="true">${ico("right")}</span>`, "cs-open", ` data-pv="open" data-id="${esc(p.id)}" data-k="pv-open:${esc(p.id)}" aria-haspopup="dialog"`) +
    tg(p.enabled, t("providers.switch", { p0: p.name }), ` data-pv="toggle" data-id="${esc(p.id)}" data-k="pv-toggle:${esc(p.id)}"${v.busy ? " disabled" : ""}`) +
    `</div>`).join("");
  const add = btn(`<span class="ic">${ico("plus")}</span><span class="tx"><span class="nm">${esc(t("providers.add"))}</span><span class="ds">${esc(t("providers.addNote"))}</span></span>`, "pv-card pv-create", ` data-pv="directory" data-k="pv-directory" aria-haspopup="dialog"${v.state === "ready" && !v.busy ? "" : " disabled"}`);
  return `<section class="sgroup pv"><h2>${esc(t("models.providers"))}</h2>` +
    (v.state === "ready" ? "" : `<p class="note" role="status">${esc(t(v.state === "failed" ? "providers.error" : "providers.loading"))}</p>`) +
    `<div class="pv-grid">${v.state === "ready" ? rows : ""}${add}</div>` +
    (v.error && !v.open && !v.adding ? `<p class="note" role="alert">${esc(errorText(v.error))}</p>` : "") +
    (v.state === "failed" ? btn(esc(t("providers.retry")), "", ' data-pv="retry" data-k="pv-retry"') : "") +
    `</section>`;
}

export function providersAddHTML(v: ProvidersView): string {
  return dialogHTML(v);
}

export interface ProvidersHost { root: HTMLElement; visible(): boolean; redraw(): void; }
export function createProviders(host: ProvidersHost) {
  let state = emptyProvidersView();
  let loads = 0;
  let modelLoads = 0;
  let accountDraft: string | null = null;
  let accountReads = 0;
  let confirmTimer: ReturnType<typeof setTimeout> | null = null;
  const redraw = () => {
    if (!host.visible()) return;
    // Carry input drafts between DOM drawings, never into page state or HTML attributes.
    const inputs = [...host.root.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>('.pv-field input:not([type="file"]), .pv-field textarea')];
    const drafts = new Map(inputs.map((input) => [input.id, input.value]));
    const active = document.activeElement as HTMLElement | null;
    const selection = active instanceof HTMLInputElement || active instanceof HTMLTextAreaElement ? [active.selectionStart, active.selectionEnd] : null;
    host.redraw();
    for (const input of host.root.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>('.pv-field input:not([type="file"]), .pv-field textarea')) {
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
    accountDraft = null; accountReads++;
    modelLoads++;
    state = { ...state, adding: id === null, open: { kind, id, replacing: false }, error: null, result: null, models: [], query: "", dirty: false, accountSet: false, fieldValues: {} };
    redraw(); host.root.querySelector<HTMLElement>("#pv-dialog")?.focus();
    if (id) void loadModels(id);
  }
  function clearConfirmation() {
    if (confirmTimer !== null) clearTimeout(confirmTimer);
    confirmTimer = null; state.confirming = false;
  }
  function close() {
    const from = state.open;
    const adding = state.adding;
    accountDraft = null; accountReads++; state.accountSet = false;
    clearConfirmation(); modelLoads++; state.open = null; state.adding = false; state.kindQuery = ""; redraw();
    if (adding) host.root.querySelector<HTMLElement>('[data-pv="directory"]')?.focus();
    else [...host.root.querySelectorAll<HTMLElement>('button[data-pv="open"]')].find((el) => el.dataset.id === from?.id)?.focus();
  }
  function directory() {
    const from = state.open?.kind;
    accountDraft = null; accountReads++; modelLoads++; clearConfirmation();
    state = { ...state, adding: true, open: null, error: null, result: null, models: [], accountSet: false, fieldValues: {}, dirty: false };
    redraw();
    const previous = [...host.root.querySelectorAll<HTMLElement>('[data-pv="add"]')].find((el) => el.dataset.id === from);
    (previous ?? host.root.querySelector<HTMLElement>("#pv-kind-search"))?.focus();
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
      if (field === "service_account") {
        const area = host.root.querySelector<HTMLTextAreaElement>(".pv-account textarea");
        const value = accountDraft ?? area?.value.trim() ?? "";
        if (area) area.value = "";
        accountDraft = null; state.accountSet = false; accountReads++;
        if (value && validAccount(value)) patch.service_account = value;
        else if (value || !open.id || open.replacing) { state.error = "account"; redraw(); return; }
        continue;
      }
      const input = [...host.root.querySelectorAll<HTMLInputElement | HTMLButtonElement>(".pv-field input, .pv-field [data-pv-field]")].find((el) => el.id === inputId(state, field));
      if (!input) continue;
      const value = (input.dataset.pvField ? input.dataset.value ?? "" : input.value).trim();
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
      if (button.dataset.pv === "directory" || button.dataset.pv === "back") directory();
      if (button.dataset.pv === "toggle") void toggle(button.dataset.id!);
      if (button.dataset.pv === "add" && state.kinds.some((k) => k.kind === button.dataset.id && k.available)) show(button.dataset.id!, null);
      if (button.dataset.pv === "open") {
        const provider = state.providers.find((p) => p.id === button.dataset.id);
        if (provider) show(provider.kind, provider.id);
      }
      if (button.dataset.pv === "replace" && state.open) { accountDraft = null; accountReads++; state.accountSet = false; state.open.replacing = true; state.dirty = true; state.result = null; redraw(); }
      if (button.dataset.pv === "file") host.root.querySelector<HTMLInputElement>(".pv-file")?.click();
      if (button.dataset.pv === "save") void save();
      if (button.dataset.pv === "test") void testConnection();
      if (button.dataset.pv === "remove") void remove();
      if (button.dataset.pv === "retry") void load();
      if (button.dataset.pv === "docs") {
        const url = kindOf(state)?.docs_url;
        const open = state.open;
        if (url) void api.openLink(url).catch(() => { if (state.open === open) { state.error = "error"; redraw(); } });
      }
      if (button.dataset.pv === "field") {
        const field = button.dataset.pvField as "region" | "location";
        openMenu(button, choicesFor(field), button.dataset.value ?? "", (value) => {
          state.fieldValues = { ...state.fieldValues, [field]: value };
          state.dirty = true; state.result = null; redraw();
        });
      }
      if (button.dataset.pv === "close" || button.dataset.pv === "scrim" && target === button) close();
      return true;
    },
    input(target: Element): void {
      if (target instanceof HTMLInputElement && target.id === "pv-kind-search") {
        state.kindQuery = target.value;
        const selection = [target.selectionStart, target.selectionEnd] as const;
        redraw();
        const fresh = host.root.querySelector<HTMLInputElement>("#pv-kind-search");
        fresh?.focus(); fresh?.setSelectionRange(...selection);
        return;
      }
      if (target.closest(".pv-field") && state.open) {
        if (target instanceof HTMLTextAreaElement && validAccount(target.value)) {
          accountDraft = target.value.trim(); accountReads++; target.value = "";
          state.accountSet = true; state.dirty = true; state.error = null; state.result = null; redraw(); return;
        }
        if (!state.dirty) { state.dirty = true; state.result = null; redraw(); }
        return;
      }
      if (!(target instanceof HTMLInputElement) || target.id !== "pv-model-search") return;
      state.query = target.value;
      const list = host.root.querySelector(".pv-model-list");
      if (list) list.innerHTML = modelsHTML(state);
    },
    async change(target: Element): Promise<void> {
      if (!(target instanceof HTMLInputElement) || !target.matches(".pv-file") || state.busy) return;
      const file = target.files?.[0]; target.value = "";
      if (!file || !state.open) return;
      const open = state.open, mine = ++accountReads;
      try {
        const value = await file.text();
        if (state.open !== open || mine !== accountReads) return;
        if (!validAccount(value)) throw new Error("invalid_account");
        accountDraft = value.trim(); state.accountSet = true; state.dirty = true; state.result = null; state.error = null;
      } catch {
        if (state.open !== open || mine !== accountReads) return;
        accountDraft = null; state.accountSet = false; state.error = "account";
      }
      redraw();
    },
    followEvent(event: MainEvent): void {
      if (event.type !== "ProvidersChanged" || !host.visible()) return;
      void load().then(() => { if (state.open?.id) void loadModels(state.open.id); });
    },
    key(event: KeyboardEvent): boolean {
      if (!state.open && !state.adding) return false;
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close(); return true; }
      if (event.key === "Enter" && event.target instanceof HTMLInputElement && event.target.closest(".pv-field")) {
        event.preventDefault(); void save(); return true;
      }
      return false;
    },
    reset(): void {
      accountDraft = null; accountReads++; state.accountSet = false;
      loads++; modelLoads++; clearConfirmation(); state.open = null; state.adding = false; state.kindQuery = ""; state.error = null; state.result = null;
      for (const input of host.root.querySelectorAll<HTMLInputElement>('.pv-field input[type="password"]')) input.value = "";
      for (const area of host.root.querySelectorAll<HTMLTextAreaElement>(".pv-field textarea")) area.value = "";
    },
  };
}
