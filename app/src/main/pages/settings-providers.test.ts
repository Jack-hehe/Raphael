// @vitest-environment happy-dom
// Exercise provider notifications through the real settings panel with a fake core API.
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { AppContext, MainEvent, ModelCatalog } from "../types";
import { setLocale } from "../i18n";

const api = vi.hoisted(() => ({
  settingsGet: vi.fn(), settingsSet: vi.fn(), secretsStatus: vi.fn(), statusGet: vi.fn(), audioDevices: vi.fn(),
  keyboardHasAltgr: vi.fn(), languagesList: vi.fn(), modelsCatalog: vi.fn(), modelsLocal: vi.fn(), hotkeysPause: vi.fn(), audioMonitor: vi.fn(),
  providersKinds: vi.fn(), providersList: vi.fn(), providersModels: vi.fn(), providersAdd: vi.fn(), servicesStatus: vi.fn(),
}));
vi.mock("../api", () => api);
vi.mock("@tauri-apps/api/app", () => ({ getVersion: async () => "test" }));
import { createSettings } from "./settings";

const empty: ModelCatalog = { llm: [], stt: [], source: "builtin", fetched_at_ms: null, replaced: [] };
const direct: ModelCatalog = { ...empty, llm: [{
  id: "local:8b", name: "Local example", provider: "Meta", provider_id: "p4", provider_name: "Ollama",
  input_per_mtok: null, output_per_mtok: null, per_minute: null, free: false, variable_price: false,
  context_length: 8192, created_at_ms: null, capabilities: {}, roles: { answer: { usable: true, reason: null }, cleanup: { usable: true, reason: null }, stt: { usable: true, reason: null } },
}] };
direct.stt = direct.llm;
const flush = async () => { for (let i = 0; i < 40; i++) await Promise.resolve(); };
const listeners = new Set<(event: MainEvent) => void>();
const send = (event: MainEvent) => { for (const listener of listeners) listener(event); };
let page: ReturnType<typeof createSettings>;
async function mount() {
  page = createSettings({
    device: () => ({ dictation_keys: ["AltRight"], agent_keys: ["AltRight+Space"], ui_language: "en", dictation_mode: "hold" }),
    onEvent: (handler: (event: MainEvent) => void) => { listeners.add(handler); return () => { listeners.delete(handler); }; },
    refreshDevice: async () => {}, undo: { begin: vi.fn() },
  } as unknown as AppContext);
  document.body.append(page.el); page.el.hidden = false;
  await page.show("usage"); await flush();
}
const click = (selector: string) => { const button = document.querySelector<HTMLButtonElement>(selector); expect(button, selector).not.toBeNull(); button!.click(); };

beforeEach(() => {
  vi.resetAllMocks(); listeners.clear(); setLocale("en");
  api.settingsGet.mockResolvedValue({ settings: { stt_engine: "cloud" } });
  api.secretsStatus.mockResolvedValue({ has_key: true });
  api.statusGet.mockResolvedValue({ openrouter: null, spend: null });
  api.audioDevices.mockResolvedValue({ devices: [], default_id: null });
  api.keyboardHasAltgr.mockResolvedValue(false);
  api.languagesList.mockResolvedValue({ groups: [] });
  api.modelsLocal.mockResolvedValue({ models: [] });
  api.modelsCatalog.mockResolvedValue(empty);
  api.settingsSet.mockImplementation(async (key: string, value: string) => ({ settings: { stt_engine: "cloud", [key]: value } }));
});
afterEach(() => { page?.hide(); document.body.innerHTML = ""; setLocale("zh-TW"); });

test("a late catalog response cannot undo a newer provider notification in an open picker", async () => {
  await mount(); click('[data-model-picker="answer"] .mp-trigger');
  let old!: (catalog: ModelCatalog) => void;
  api.modelsCatalog.mockImplementationOnce(() => new Promise((resolve) => { old = resolve; })).mockResolvedValueOnce(direct);
  send({ type: "ProvidersChanged" }); send({ type: "ProvidersChanged" }); await flush();
  expect(document.querySelector("#mp-list")?.textContent).toContain("Local example");
  old(empty); await flush();
  expect(document.querySelector("#mp-list")?.textContent).toContain("Local example");
});

test.each([
  ["cleanup", "cleanup_model"], ["answer", "agent_model"], ["stt", "stt_cloud_model"],
])("the %s purpose stores provider refs and still accepts plain OpenRouter slugs", async (role, setting) => {
  api.modelsCatalog.mockResolvedValue(direct);
  await mount();
  click(`[data-model-picker="${role}"] .mp-trigger`);
  click('[data-id="@p4:local:8b"]'); await flush();
  expect(api.settingsSet).toHaveBeenCalledWith(setting, "@p4:local:8b");
  const legacy = { ...direct, llm: direct.llm.map((model) => ({ ...model, id: "openai/example", provider_id: "openrouter", provider_name: "OpenRouter", name: "OpenRouter example" })) };
  legacy.stt = legacy.llm;
  api.modelsCatalog.mockResolvedValue(legacy);
  send({ type: "ProvidersChanged" }); await flush();
  click(`[data-model-picker="${role}"] .mp-trigger`);
  click('[data-id="openai/example"]'); await flush();
  expect(api.settingsSet).toHaveBeenCalledWith(setting, "openai/example");
});

test("fallbacks add ordered provider refs, cap at three, reorder and remove through settings.set", async () => {
  let settings = { stt_engine: "cloud", cleanup_fallbacks: ["@p1:first", "@p2:second"] };
  api.settingsGet.mockImplementation(async () => ({ settings }));
  api.settingsSet.mockImplementation(async (key: string, value: unknown) => { settings = { ...settings, [key]: value }; return { settings }; });
  api.modelsCatalog.mockResolvedValue(direct);
  await mount();
  const section = () => page.el.querySelector('[data-fallback-purpose="cleanup"]')!;
  expect(section()).not.toBeNull();
  expect(section().querySelectorAll('.mp-slot')).toHaveLength(2);
  click('[data-fallback-purpose="cleanup"] [data-fallback-act="add"]');
  expect(api.settingsSet).not.toHaveBeenCalled();
  expect(section().querySelectorAll('.mp-slot')).toHaveLength(3);
  click('[data-fallback-purpose="cleanup"] [data-fallback-index="2"] .mp-trigger');
  click('[data-id="@p4:local:8b"]'); await flush();
  expect(api.settingsSet).toHaveBeenLastCalledWith("cleanup_fallbacks", ["@p1:first", "@p2:second", "@p4:local:8b"]);
  expect(section().querySelector<HTMLButtonElement>('[data-fallback-act="add"]')?.disabled).toBe(true);
  click('[data-fallback-purpose="cleanup"] [data-fallback-index="2"] [data-fallback-act="up"]'); await flush();
  expect(api.settingsSet).toHaveBeenLastCalledWith("cleanup_fallbacks", ["@p1:first", "@p4:local:8b", "@p2:second"]);
  click('[data-fallback-purpose="cleanup"] [data-fallback-index="0"] [data-fallback-act="down"]'); await flush();
  expect(api.settingsSet).toHaveBeenLastCalledWith("cleanup_fallbacks", ["@p4:local:8b", "@p1:first", "@p2:second"]);
  click('[data-fallback-purpose="cleanup"] [data-fallback-index="1"] [data-fallback-act="remove"]'); await flush();
  expect(api.settingsSet).toHaveBeenLastCalledWith("cleanup_fallbacks", ["@p4:local:8b", "@p2:second"]);
});

test.each(["agent", "cleanup", "router", "pdf", "stt", "memory"])("the %s fallback picker stores its own purpose key", async (purpose) => {
  api.modelsCatalog.mockResolvedValue(direct); await mount();
  click(`[data-fallback-purpose="${purpose}"] [data-fallback-act="add"]`);
  click(`[data-fallback-purpose="${purpose}"] .mp-trigger`);
  click('[data-id="@p4:local:8b"]'); await flush();
  expect(api.settingsSet).toHaveBeenCalledWith(`${purpose}_fallbacks`, ["@p4:local:8b"]);
});

test("provider dropdowns and local JSON fields work through settings event delegation", async () => {
  api.providersList.mockResolvedValue({ providers: [] });
  api.providersKinds.mockResolvedValue({ kinds: [{ kind: "vertex", name: "Vertex", fields: ["service_account", "project", "location"], group: "official", available: true }] });
  await mount(); click('[data-pane="models"]'); await flush(); click('[data-pv="add"]');
  click('[data-pv-field="location"]');
  const choice = [...document.querySelectorAll<HTMLButtonElement>('[role="menuitemradio"]')].find((node) => node.textContent?.includes("asia-east1"))!;
  expect(choice).toBeDefined(); choice.click();
  const area = page.el.querySelector<HTMLTextAreaElement>('textarea')!;
  area.value = '{"client_email":"test@example.com","private_key":"secret"}'; area.dispatchEvent(new Event("input", { bubbles: true }));
  page.el.querySelector<HTMLInputElement>('[id$="-project"]')!.value = "project";
  api.providersAdd.mockRejectedValue(new Error("failed")); click('[data-pv="save"]'); await flush();
  expect(api.providersAdd).toHaveBeenCalledWith({ kind: "vertex", name: "Vertex", location: "asia-east1", project: "project", service_account: '{"client_email":"test@example.com","private_key":"secret"}' });
});

test("vision fallback settings use image models in the service dialog", async () => {
  const visionCatalog = { ...direct, llm: direct.llm.map((m) => ({ ...m, capabilities: { input_modalities: ["text", "image"] } })) };
  api.modelsCatalog.mockResolvedValue(visionCatalog);
  api.servicesStatus.mockResolvedValue({
    search: { enabled: true, service: "openrouter", options: [] }, read: { enabled: true, service: "builtin", options: [] },
    pdf: { enabled: true, service: "builtin", options: [] }, voice: { enabled: false, service: "fish", options: [] },
    vision: { enabled: true, model: "", resolved_model: "example", supports_images: true },
  });
  await mount(); click('[data-pane="agent"]'); await flush();
  click('button[data-svc="open"][data-id="vision"]');
  click('button[data-svc="service"][data-service="model"]');
  click('[data-fallback-purpose="vision"] [data-fallback-act="add"]');
  click('[data-fallback-purpose="vision"] .mp-trigger');
  click('[data-id="@p4:local:8b"]'); await flush();
  expect(api.settingsSet).toHaveBeenCalledWith("vision_fallbacks", ["@p4:local:8b"]);
});

test("redrawing an open fallback picker keeps its identity instead of selecting the primary model", async () => {
  api.modelsCatalog.mockResolvedValue(direct);
  api.settingsGet.mockResolvedValue({ settings: { stt_engine: "cloud", cleanup_fallbacks: ["@p1:old"] } });
  await mount(); click('[data-fallback-purpose="cleanup"] .mp-trigger');
  setLocale("ja"); await flush();
  click('[data-id="@p4:local:8b"]'); await flush();
  expect(api.settingsSet).toHaveBeenCalledWith("cleanup_fallbacks", ["@p4:local:8b"]);
  expect(api.settingsSet).not.toHaveBeenCalledWith("cleanup_model", expect.anything());
  expect((document.activeElement as HTMLElement).dataset.k).toBe("fallback-cleanup-0");
});
