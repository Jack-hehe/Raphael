// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { setLocale } from "./i18n";

const api = vi.hoisted(() => ({
  providersKinds: vi.fn(), providersList: vi.fn(), providersAdd: vi.fn(), providersUpdate: vi.fn(),
  providersRemove: vi.fn(), providersTest: vi.fn(), providersModels: vi.fn(),
}));
vi.mock("./api", () => api);

import { createProviders, providersHTML } from "./providers";

const connected = { id: "p3", kind: "openai", name: "OpenAI", base_url: "https://api.openai.com/v1", has_key: true, enabled: true, status: "ok", models: 42 };
const kinds = [
  { kind: "openai", name: "OpenAI", group: "official", fields: ["key"], default_base_url: "https://api.openai.com/v1", available: true },
  { kind: "bedrock", name: "Amazon Bedrock", group: "official", fields: ["key", "key_id", "region"], default_base_url: "", available: false },
  { kind: "openai_compatible", name: "Custom", group: "gateway", fields: ["base_url", "key"], default_base_url: "", available: true },
  { kind: "ollama", name: "Ollama", group: "local", fields: ["base_url"], default_base_url: "http://localhost:11434/v1", available: true },
];
let root: HTMLDivElement;
function mount() {
  const panel = createProviders({ root, visible: () => true, redraw: () => { root.innerHTML = providersHTML(panel.view()); } });
  root.addEventListener("click", (e) => panel.click(e.target as Element));
  root.addEventListener("input", (e) => panel.input(e.target as Element));
  root.addEventListener("keydown", (e) => panel.key(e));
  return panel;
}
const flush = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };
function click(selector: string) { const el = root.querySelector<HTMLButtonElement>(selector); expect(el, selector).not.toBeNull(); el!.click(); }

beforeEach(() => {
  vi.resetAllMocks();
  setLocale("en");
  root = document.createElement("div"); document.body.append(root);
  api.providersKinds.mockResolvedValue({ kinds });
  api.providersList.mockResolvedValue({ providers: [connected] });
  api.providersModels.mockResolvedValue({ models: [] });
});

test("connecting a kind saves its fields once and clears the password before awaiting the core", async () => {
  const panel = mount(); await panel.load();
  click('[data-pv="add"][data-id="openai"]');
  const key = root.querySelector<HTMLInputElement>('input[type="password"]')!;
  expect(key).not.toBeNull();
  key.value = "secret-never-rendered";
  let finish!: (value: unknown) => void;
  api.providersAdd.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  click('[data-pv="save"]');
  expect(key.value).toBe("");
  expect(root.innerHTML).not.toContain("secret-never-rendered");
  expect(api.providersAdd).toHaveBeenCalledWith({ kind: "openai", name: "OpenAI", key: "secret-never-rendered" });
  finish({ provider: connected }); await flush();
  expect(root.querySelector('[role="dialog"]')?.textContent).toContain("Configured");
  expect(root.querySelector('input[type="password"]')).toBeNull();
});
afterEach(() => { root.remove(); setLocale("zh-TW"); vi.useRealTimers(); });

test("connected providers show a status, a switch, and real connect buttons grouped by kind", async () => {
  const panel = mount(); await panel.load();
  expect(root.textContent).toContain("42 models");
  expect(root.querySelector('[role="switch"]')?.getAttribute("aria-checked")).toBe("true");
  for (const title of ["Connected", "Add a provider", "Official", "Gateways and routers", "Local"]) expect(root.textContent).toContain(title);
  expect(root.querySelector<HTMLButtonElement>('[data-pv="add"][data-id="bedrock"]')?.disabled).toBe(true);
  expect(root.querySelector('[data-pv="open"] .ic')?.textContent).toBe("");
  click('[data-pv="toggle"]'); await flush();
  expect(api.providersUpdate).toHaveBeenCalledWith({ id: "p3", enabled: false });
});

test.each([
  [{ ok: true, models: 42 }, "42 models"],
  [{ ok: false, kind: "auth", models: 0 }, "Invalid key"],
  [{ ok: false, kind: "offline", models: 0 }, "Cannot connect"],
])("a saved provider shows its connection test result inline: %j", async (result, text) => {
  const panel = mount(); await panel.load();
  click('[data-pv="open"]'); await flush();
  api.providersTest.mockResolvedValue(result);
  click('[data-pv="test"]'); await flush();
  expect(root.querySelector('[role="dialog"] [role="status"]')?.textContent).toContain(text);
  expect(api.providersTest).toHaveBeenCalledWith("p3");
});

test("removing a provider asks a second time before removing the connection", async () => {
  const panel = mount(); await panel.load();
  click('[data-pv="open"]'); await flush();
  click('[data-pv="remove"]');
  expect(api.providersRemove).not.toHaveBeenCalled();
  expect(root.querySelector('[data-pv="remove"]')?.textContent).toBe("Remove provider?");
  api.providersRemove.mockResolvedValue({ removed: true });
  api.providersList.mockResolvedValue({ providers: [] });
  click('[data-pv="remove"]'); await flush();
  expect(api.providersRemove).toHaveBeenCalledWith("p3");
  expect(root.querySelector('[role="dialog"]')).toBeNull();
  expect(root.textContent).toContain("No providers connected");
});

test("provider changes refresh the connected rows and discovered models without exposing a typed replacement key", async () => {
  const panel = mount(); await panel.load();
  click('[data-pv="open"]'); await flush();
  click('[data-pv="replace"]');
  root.querySelector<HTMLInputElement>('input[type="password"]')!.value = "draft-stays-private";
  api.providersList.mockResolvedValue({ providers: [{ ...connected, status: "auth" }] });
  api.providersModels.mockResolvedValue({ models: Array.from({ length: 12 }, (_, n) => ({ id: `model-${n}`, name: `Model ${n}`, context: 8192, tools: true, vision: false, audio_in: false, price_in: null, price_out: null })) });
  panel.followEvent({ type: "ProvidersChanged" }); await flush();
  expect(root.querySelector('[data-pv="open"]')?.textContent).toContain("Invalid key");
  expect(root.querySelector<HTMLInputElement>('input[type="password"]')?.value).toBe("draft-stays-private");
  expect(root.innerHTML).not.toContain("draft-stays-private");
  const search = root.querySelector<HTMLInputElement>("#pv-model-search")!;
  search.value = "model-11"; search.dispatchEvent(new Event("input", { bubbles: true }));
  expect(root.querySelectorAll(".pv-model-list li")).toHaveLength(1);
  expect(root.querySelector(".pv-model-list")?.textContent).toContain("Model 11");
  expect(root.querySelector(".pv-model-list button")).toBeNull();
});

test("async model discovery keeps focus in the dialog and Escape returns to its row button", async () => {
  const panel = mount(); await panel.load();
  click('[data-pv="open"]'); await flush();
  expect(document.activeElement).toBe(root.querySelector("#pv-dialog"));
  root.querySelector<HTMLElement>("#pv-dialog")!.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  expect(root.querySelector("#pv-dialog")).toBeNull();
  expect(document.activeElement).toBe(root.querySelector('button[data-pv="open"]'));
});

test("an externally removed provider closes its stale dialog", async () => {
  const panel = mount(); await panel.load();
  click('[data-pv="open"]'); await flush();
  api.providersList.mockResolvedValue({ providers: [] });
  panel.followEvent({ type: "ProvidersChanged" }); await flush();
  expect(root.querySelector('[role="dialog"]')).toBeNull();
});

test("a replacement must be saved before testing so the result describes the new credentials", async () => {
  const panel = mount(); await panel.load();
  click('[data-pv="open"]'); await flush();
  click('[data-pv="replace"]');
  const key = root.querySelector<HTMLInputElement>('input[type="password"]')!;
  key.value = "new-secret"; key.dispatchEvent(new Event("input", { bubbles: true }));
  expect(root.querySelector<HTMLButtonElement>('[data-pv="test"]')?.disabled).toBe(true);
  expect(root.querySelector('[role="dialog"]')?.textContent).toContain("Save changes before testing");
  api.providersUpdate.mockResolvedValue({ provider: connected });
  click('[data-pv="save"]'); await flush();
  expect(api.providersUpdate).toHaveBeenCalledWith({ id: "p3", name: "OpenAI", key: "new-secret" });
  expect(root.querySelector<HTMLButtonElement>('[data-pv="test"]')?.disabled).toBe(false);
});

test("a custom endpoint can connect without a key while invalid addresses stay in the dialog", async () => {
  const panel = mount(); await panel.load();
  click('[data-pv="add"][data-id="openai_compatible"]');
  const address = root.querySelector<HTMLInputElement>('input[aria-label="API address"]')!;
  address.value = "file:///tmp/model";
  click('[data-pv="save"]'); await flush();
  expect(api.providersAdd).not.toHaveBeenCalled();
  expect(root.querySelector('[role="alert"]')?.textContent).toContain("valid http(s) address");
  root.querySelector<HTMLInputElement>('input[aria-label="API address"]')!.value = "http://localhost:8080/v1";
  const custom = { ...connected, id: "p5", kind: "openai_compatible", name: "Custom", base_url: "http://localhost:8080/v1", has_key: false };
  api.providersAdd.mockResolvedValue({ provider: custom });
  api.providersList.mockResolvedValue({ providers: [connected, custom] });
  click('[data-pv="save"]'); await flush();
  expect(api.providersAdd).toHaveBeenCalledWith({ kind: "openai_compatible", name: "Custom", base_url: "http://localhost:8080/v1" });
  expect(root.querySelector('[role="dialog"]')?.textContent).toContain("Custom");
});

test("failed saves show a safe error and preserve public drafts without retaining the secret", async () => {
  const panel = mount(); await panel.load();
  click('[data-pv="add"][data-id="openai"]');
  root.querySelector<HTMLInputElement>('input[aria-label="Name"]')!.value = "My account";
  root.querySelector<HTMLInputElement>('input[type="password"]')!.value = "write-only-secret";
  api.providersAdd.mockRejectedValue(new Error("write-only-secret"));
  click('[data-pv="save"]'); await flush();
  expect(root.querySelector('[role="alert"]')?.textContent).toBe("Could not complete this. Please try again.");
  expect(root.querySelector<HTMLInputElement>('input[aria-label="Name"]')?.value).toBe("My account");
  expect(root.querySelector<HTMLInputElement>('input[type="password"]')?.value).toBe("");
  expect(root.innerHTML).not.toContain("write-only-secret");
});

test("the remove confirmation expires after the existing five-second confirmation window", async () => {
  vi.useFakeTimers();
  const panel = mount(); await panel.load();
  click('[data-pv="open"]'); await flush();
  click('[data-pv="remove"]');
  await vi.advanceTimersByTimeAsync(5001);
  expect(root.querySelector('[data-pv="remove"]')?.textContent).toBe("Remove");
  click('[data-pv="remove"]');
  expect(api.providersRemove).not.toHaveBeenCalled();
  panel.reset();
});
