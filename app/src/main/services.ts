// 服務, in the 拉斐爾 tab of Settings: who does the work of Raphael's tools, one choice for each.
// Four rows, drawn like every other row of the tab, the name on the left and the control on the
// right:
//
// - 搜尋網路: a dropdown of the services that look the web up (OpenRouter, Brave, Tavily, Exa, SearXNG).
// - 讀網頁: a dropdown of those that read a page (內建, Jina, Firecrawl, Tavily).
// - 看圖片: the model picker (model-picker.ts), with the models that read images, under 「與回答相同」
//   (the value "", the model that answers). settings.ts mounts it, as it mounts the model rows.
// - 讀 PDF: a dropdown of 內建 and OpenRouter.
//
// Under a row whose service takes a key, one compact line: with no key stored, a password field and
// a bordered 「儲存」; with one, 「已設定」 in muted words and a bordered 「移除」. Jina's key is
// optional: its field says so. SearXNG has its address on that line instead, saved with
// `settings.set searxng_url`.
//
// The choices and whether each key is stored come from the core (`services.status`), which never
// says a key; choosing is `settings.set`. A key goes once to `services.set_key`: the field is emptied
// the moment it is sent, the key is in no state of this page, and nothing here logs it.
//
// `servicesHTML` draws the section from a snapshot (pure, so its rules can be tested without a
// browser); `createServices` holds the state and does what the buttons and the dropdowns say.

import * as api from "./api";
import { describeError } from "./format";
import { t } from "./i18n";
import type { BrainSettings, ServiceChoice, ServiceOption, ServicesStatus } from "./types";
import { type Choice, btn, esc, fieldHTML, note, row, sel, st, toaster } from "./ui";

/** The three capabilities chosen with a dropdown, with the setting each choice is kept in. */
export const SERVICE_KINDS = [
  { kind: "search", k: "svc-search", setting: "web_search_service", label: () => t("services.search") },
  { kind: "read", k: "svc-read", setting: "web_read_service", label: () => t("services.read") },
  { kind: "pdf", k: "svc-pdf", setting: "pdf_service", label: () => t("services.pdf") },
] as const satisfies readonly { kind: keyof ServicesStatus; k: string; setting: keyof BrainSettings; label: () => string }[];

export type ServiceKind = (typeof SERVICE_KINDS)[number]["kind"];

/** The longest key the core takes (`services.set_key`). */
export const SERVICE_KEY_MAX = 512;

/** The services by their names: brands, the same in every language. `builtin` is said in the window's language. */
const NAMES: Record<string, string> = {
  openrouter: "OpenRouter",
  brave: "Brave",
  tavily: "Tavily",
  exa: "Exa",
  searxng: "SearXNG",
  jina: "Jina",
  firecrawl: "Firecrawl",
};

/** The name of a service: 內建 for this computer's own, its brand for the others, its id for one this app does not know yet. */
export const serviceName = (id: string): string => (id === "builtin" ? t("services.builtin") : (NAMES[id] ?? id));

/** The choices of a capability's dropdown, in the core's order. */
export const serviceChoices = (choice: ServiceChoice): Choice[] => choice.options.map((o) => ({ value: o.id, label: serviceName(o.id) }));

/** Whether the line under the row is drawn for `option`: it takes a key (needed or optional), or it is SearXNG's address. */
export const hasLine = (option: ServiceOption | undefined): boolean =>
  option !== undefined && (option.id === "searxng" || option.needs_key || option.key_optional);

export interface ServicesView {
  status: ServicesStatus | "loading" | "failed";
  /** The service whose key or address is on its way to the core, or being removed. */
  busy: string | null;
}

export const emptyServicesView = (): ServicesView => ({ status: "loading", busy: null });

/** The id of the field of `service`'s key on the page. */
export const keyInputId = (service: string): string => `svc-key-${service}`;
/** The id of SearXNG's address field. */
export const URL_INPUT_ID = "svc-url";

/**
 * The line under a row whose chosen service takes a key or an address. `url` is SearXNG's address
 * as the settings have it (not a secret: it is shown in its field to be changed).
 */
function lineHTML(option: ServiceOption, v: ServicesView, url: string): string {
  const off = v.busy === option.id ? " disabled" : "";
  const name = serviceName(option.id);
  if (option.id === "searxng") {
    return (
      `<div class="svc-line" data-svc-line="${esc(option.id)}">` +
      fieldHTML("https://…", url, { id: URL_INPUT_ID, w: 200, label: t("services.url", { p0: name }), disabled: v.busy === option.id }) +
      btn(esc(t("services.save")), "", ` data-svc="url" data-service="${esc(option.id)}"${off}`) +
      `</div>`
    );
  }
  if (option.has_key) {
    return (
      `<div class="svc-line" data-svc-line="${esc(option.id)}">` +
      `<span class="val">${esc(t("services.keySet"))}</span>` +
      btn(esc(t("services.remove")), "", ` data-svc="clear" data-service="${esc(option.id)}"${off}`) +
      `</div>`
    );
  }
  const placeholder = option.needs_key ? t("services.key") : t("services.keyOptional");
  return (
    `<div class="svc-line" data-svc-line="${esc(option.id)}">` +
    fieldHTML(placeholder, "", { id: keyInputId(option.id), w: 200, type: "password", label: t("services.keyLabel", { p0: name }), disabled: v.busy === option.id }) +
    btn(esc(t("services.save")), "", ` data-svc="save" data-service="${esc(option.id)}"${off}`) +
    `</div>`
  );
}

/**
 * The section: its four rows and the line under each that needs one. `visionPicker` is the button of
 * the 看圖片 picker (`modelPickerHTML`), which the settings draw with the catalog they hold. `brain`
 * gives what is chosen while the core has not said (`status` is loading or failed): the dropdowns
 * are shown, closed, until the core gives their choices.
 */
export function servicesHTML(v: ServicesView, visionPicker: string, brain: BrainSettings | null): string {
  const status = typeof v.status === "object" ? v.status : null;
  const rows: string[] = [];
  const current = (kind: ServiceKind, setting: keyof BrainSettings): string => status?.[kind].service ?? String(brain?.[setting] ?? "");
  for (const { kind, k, setting, label } of SERVICE_KINDS) {
    const chosen = current(kind, setting);
    const choices = status ? serviceChoices(status[kind]) : [{ value: chosen, label: serviceName(chosen) }];
    rows.push(row(esc(label()), "", sel(choices, chosen, label(), ` data-k="${k}"${status ? "" : " disabled"}`), { attrs: ` data-row="${k}"` }));
    const option = status?.[kind].options.find((o) => o.id === chosen);
    if (option && hasLine(option)) rows.push(lineHTML(option, v, brain?.searxng_url ?? ""));
    // 看圖片 goes between 讀網頁 and 讀 PDF.
    if (kind === "read") rows.push(row(esc(t("services.vision")), "", visionPicker, { attrs: ' data-row="svc-vision"' }));
  }
  if (v.status === "failed") rows.push(note(st("err", t("services.loadFailed")), ' role="alert"'));
  return `<section class="sgroup svc" data-svc-section><h2>${esc(t("services.title"))}</h2><div class="slist">${rows.join("")}</div></section>`;
}

// ---------- state ----------

export interface ServicesHost {
  visible(): boolean;
  redraw(): void;
  /** Changes a setting of the brain (`settings.set`) the way the rest of the panel does; tells a refusal itself. */
  changeSetting(key: keyof BrainSettings, value: string): Promise<void>;
}

/** What the section's dropdown of `k` offers, what is chosen and what choosing does; the same shape as the panel's other menus. */
export interface ServicesMenu {
  choices: Choice[];
  current: string;
  pick: (value: string) => void;
}

export interface ServicesPanel {
  view(): ServicesView;
  load(): Promise<void>;
  /** The menu of one of the section's dropdowns, by its `data-k`; null for any other. */
  menuFor(k: string): ServicesMenu | null;
  /** A click on the page: whether it was the section's. */
  click(target: Element): boolean;
  key(e: KeyboardEvent): boolean;
  /** The 看圖片 picker chose `id` ("" is 「與回答相同」). */
  chooseVision(id: string): Promise<void>;
  reset(): void;
}

/** Whether `text` may be SearXNG's address: blank (none), or an http(s) URL. */
export function isServiceUrl(text: string): boolean {
  const value = text.trim();
  if (value === "") return true;
  try {
    const url = new URL(value);
    return (url.protocol === "http:" || url.protocol === "https:") && url.hostname !== "";
  } catch {
    return false;
  }
}

export function createServices(host: ServicesHost): ServicesPanel {
  let status: ServicesView["status"] = "loading";
  let busy: string | null = null;
  let loads = 0;

  const redraw = (): void => {
    if (host.visible()) host.redraw();
  };
  const view = (): ServicesView => ({ status, busy });

  async function load(): Promise<void> {
    const mine = ++loads;
    try {
      const next = await api.servicesStatus();
      if (mine !== loads) return;
      status = next;
    } catch {
      if (mine !== loads) return;
      if (typeof status !== "object") status = "failed";
    }
    redraw();
  }

  async function choose(setting: keyof BrainSettings, value: string): Promise<void> {
    await host.changeSetting(setting, value);
    await load();
  }

  function menuFor(k: string): ServicesMenu | null {
    const entry = SERVICE_KINDS.find((s) => s.k === k);
    if (!entry || typeof status !== "object") return null;
    const choice = status[entry.kind];
    return {
      choices: serviceChoices(choice),
      current: choice.service,
      pick: (value) => {
        const fresh = typeof status === "object" ? status[entry.kind] : choice;
        if (value !== fresh.service && fresh.options.some((o) => o.id === value)) void choose(entry.setting, value);
      },
    };
  }

  /**
   * Sends the key typed for `service` to the vault. The field is emptied first, so that the key is
   * on the page no longer than the press that sent it; a key that is refused is typed again.
   */
  async function saveKey(service: string): Promise<void> {
    if (busy !== null || typeof document === "undefined") return;
    const input = document.getElementById(keyInputId(service)) as HTMLInputElement | null;
    if (!input) return;
    const key = input.value.trim();
    input.value = "";
    if (key === "" || key.length > SERVICE_KEY_MAX) return;
    busy = service;
    redraw();
    try {
      await api.servicesSetKey(service, key);
      toaster.info(t("services.keySaved"));
    } catch (err) {
      toaster.info(describeError(err));
    }
    busy = null;
    await load();
  }

  async function clearKey(service: string): Promise<void> {
    if (busy !== null) return;
    busy = service;
    redraw();
    try {
      await api.servicesClearKey(service);
      toaster.info(t("services.keyRemoved", { p0: serviceName(service) }));
    } catch (err) {
      toaster.info(describeError(err));
    }
    busy = null;
    await load();
  }

  async function saveUrl(): Promise<void> {
    if (busy !== null || typeof document === "undefined") return;
    const input = document.getElementById(URL_INPUT_ID) as HTMLInputElement | null;
    if (!input) return;
    const url = input.value.trim();
    if (!isServiceUrl(url)) {
      toaster.info(t("services.urlInvalid"));
      return;
    }
    busy = "searxng";
    redraw();
    await host.changeSetting("searxng_url", url);
    busy = null;
    await load();
    if (url !== "") toaster.info(t("services.urlSaved"));
  }

  function click(target: Element): boolean {
    const button = target.closest<HTMLElement>("[data-svc]");
    if (!button || (button as HTMLButtonElement).disabled) return false;
    const service = button.dataset.service ?? "";
    switch (button.dataset.svc) {
      case "save":
        void saveKey(service);
        return true;
      case "clear":
        void clearKey(service);
        return true;
      case "url":
        void saveUrl();
        return true;
    }
    return false;
  }

  /** Enter in a key's field sends it, as 「儲存」 does; Enter in the address saves it. */
  function key(e: KeyboardEvent): boolean {
    const input = e.target as HTMLInputElement | null;
    if (e.key !== "Enter" || e.isComposing || !input?.id) return false;
    if (input.id === URL_INPUT_ID) {
      e.preventDefault();
      void saveUrl();
      return true;
    }
    if (!input.id.startsWith("svc-key-")) return false;
    e.preventDefault();
    void saveKey(input.id.slice("svc-key-".length));
    return true;
  }

  const chooseVision = (id: string): Promise<void> => choose("vision_model", id);

  function reset(): void {
    loads++;
    busy = null;
  }

  return { view, load, menuFor, click, key, chooseVision, reset };
}
