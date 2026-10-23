// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { setLocale, t } from "./i18n";

const api = vi.hoisted(() => ({
  providersKinds: vi.fn(), providersList: vi.fn(), providersAdd: vi.fn(), providersUpdate: vi.fn(),
  providersRemove: vi.fn(), providersTest: vi.fn(), providersModels: vi.fn(),
  openLink: vi.fn(),
}));
vi.mock("./api", () => api);

import { createProviders, providersHTML, providersAddHTML } from "./providers";

const connected = { id: "p3", kind: "openai", name: "OpenAI", base_url: "https://api.openai.com/v1", has_key: true, enabled: true, status: "ok", models: 42 };
const kinds = [
  { kind: "openai", name: "OpenAI", group: "official", fields: ["key"], default_base_url: "https://api.openai.com/v1", available: true },
  { kind: "bedrock", name: "Amazon Bedrock", group: "official", fields: ["key", "key_id", "region"], default_base_url: "", available: false },
  { kind: "openai_compatible", name: "Custom", group: "gateway", fields: ["base_url", "key"], default_base_url: "", available: true },
  { kind: "ollama", name: "Ollama", group: "local", fields: ["base_url"], default_base_url: "http://localhost:11434/v1", available: true },
];
let root: HTMLDivElement;
function mount() {
  const panel = createProviders({ root, visible: () => true, redraw: () => { root.innerHTML = providersHTML(panel.view()) + providersAddHTML(panel.view()); } });
  root.addEventListener("click", (e) => panel.click(e.target as Element));
  root.addEventListener("input", (e) => panel.input(e.target as Element));
  root.addEventListener("keydown", (e) => panel.key(e));
  root.addEventListener("change", (e) => void panel.change(e.target as Element));
  return panel;
}
const flush = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };
function click(selector: string) { const el = root.querySelector<HTMLButtonElement>(selector); expect(el, selector).not.toBeNull(); el!.click(); }

function connect(selector: string) {
  if (!root.querySelector(selector)) click('[data-pv="directory"]');
  click(selector);
}

beforeEach(() => {
  vi.resetAllMocks();
  setLocale("en");
  root = document.createElement("div"); document.body.append(root);
  api.providersKinds.mockResolvedValue({ kinds });
  api.providersList.mockResolvedValue({ providers: [connected] });
  api.providersModels.mockResolvedValue({ models: [] });
  api.openLink.mockResolvedValue(undefined);
});

test.each(["zh-TW", "en", "ja"] as const)("%s: the add button is the last provider card and closes back to that card", async (locale) => {
  setLocale(locale);
  const panel = mount(); await panel.load();
  const grid = root.querySelector(".pv > .pv-grid")!;
  const add = root.querySelector<HTMLButtonElement>('button.pv-card[data-pv="directory"]');
  expect(add).not.toBeNull();
  expect(grid.lastElementChild).toBe(add);
  expect(grid.children).toHaveLength(2);
  expect(add?.querySelector(".nm")?.textContent).toBe(t("providers.add"));
  expect(add?.querySelector(".ic .i")).not.toBeNull();
  expect(add?.querySelector(".ds")?.textContent).toContain("OpenAI");
  if (locale === "zh-TW") expect(add?.querySelector(".ds")?.textContent).toBe("OpenAI、Claude、Ollama…等 60 家");
  expect(providersAddHTML(panel.view())).toBe("");
  click('[data-pv="directory"]');
  expect(root.querySelector('[role="dialog"]')).not.toBeNull();
  root.querySelector<HTMLElement>("#pv-kind-search")!.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  expect(document.activeElement).toBe(root.querySelector('button.pv-card[data-pv="directory"]'));
});

test("with no connected providers the grid contains only the add card", async () => {
  api.providersList.mockResolvedValue({ providers: [] });
  const panel = mount(); await panel.load();
  const grid = root.querySelector(".pv > .pv-grid")!;
  expect(grid).not.toBeNull();
  expect(grid.children).toHaveLength(1);
  expect(grid.firstElementChild).toBe(root.querySelector('button[data-pv="directory"]'));
  click('[data-pv="directory"]');
  expect(root.querySelectorAll(".pv-add")).toHaveLength(kinds.length);
});

test("connecting a kind saves its fields once and clears the password before awaiting the core", async () => {
  const panel = mount(); await panel.load();
  connect('[data-pv="add"][data-id="openai"]');
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

test("the provider directory orders popular gateways first and searches names across three groups", async () => {
  api.providersKinds.mockResolvedValue({ kinds: [
    ...kinds,
    ...["Zulu", "Vercel", "Alpha", "Portkey", "OpenRouter", "LiteLLM"].map((name) => ({ kind: name.toLowerCase(), name, group: "gateway", fields: ["key"], available: true })),
  ] });
  const panel = mount(); await panel.load();
  click('[data-pv="directory"]');
  const names = () => [...root.querySelectorAll('.pv-add .nm')].map((node) => node.textContent);
  expect(names()).toEqual(["OpenAI", "Amazon Bedrock", "OpenRouter", "LiteLLM", "Portkey", "Vercel", "Alpha", "Custom", "Zulu", "Ollama"]);
  const search = root.querySelector<HTMLInputElement>("#pv-kind-search")!;
  expect(search).not.toBeNull();
  search.value = "oPeN"; search.dispatchEvent(new Event("input", { bubbles: true }));
  expect(names()).toEqual(["OpenAI", "OpenRouter"]);
  expect(root.querySelectorAll(".pv-add .ds")).toHaveLength(2);
});

test.each(["zh-TW", "en", "ja"] as const)("%s renders every kind as a marked card with a description and a real connect button", async (locale) => {
  setLocale(locale);
  const panel = mount(); await panel.load(); click('[data-pv="directory"]');
  const dialog = root.querySelector('[role="dialog"]')!;
  expect(dialog.getAttribute("aria-modal")).toBe("true");
  expect(dialog.querySelectorAll(".pv-grid")).toHaveLength(3);
  const cards = [...dialog.querySelectorAll(".pv-card")];
  expect(cards).toHaveLength(kinds.length);
  expect(cards.map((card) => card.querySelector<HTMLElement>('[data-pv="add"]')?.dataset.id).sort()).toEqual(kinds.map((kind) => kind.kind).sort());
  for (const card of cards) {
    expect(card.querySelector('.ic .mono :is(img.logo, svg.i)')).not.toBeNull();
    expect(card.querySelector(".ds")?.textContent?.length).toBeGreaterThan(8);
    expect(card.querySelector('button[type="button"][data-pv="add"]')).not.toBeNull();
  }
  expect(cards[0].textContent).toContain(t("providers.officialAPI", { p0: "OpenAI" }));
  expect(cards[2].textContent).toContain(t("providers.compatibleNote"));
});

test("the add dialog preserves search on back, discards credentials and restores focus on close", async () => {
  const panel = mount(); await panel.load(); click('[data-pv="directory"]');
  expect(document.activeElement?.id).toBe("pv-kind-search");
  const search = root.querySelector<HTMLInputElement>("#pv-kind-search")!;
  search.value = "OpenAI"; search.dispatchEvent(new Event("input", { bubbles: true }));
  click('[data-pv="add"][data-id="openai"]');
  expect(root.querySelectorAll('[role="dialog"]')).toHaveLength(1);
  expect(root.querySelector('[data-dialog="provider:add"] .pv-catalog')).not.toBeNull();
  root.querySelector<HTMLInputElement>('input[type="password"]')!.value = "discard-on-back";
  click('[data-pv="back"]');
  expect(root.querySelector<HTMLInputElement>("#pv-kind-search")?.value).toBe("OpenAI");
  expect(root.querySelectorAll(".pv-add")).toHaveLength(1);
  expect((document.activeElement as HTMLElement).dataset.id).toBe("openai");
  click('[data-pv="add"][data-id="openai"]');
  expect(root.querySelector<HTMLInputElement>('input[type="password"]')?.value).toBe("");
  expect(api.providersAdd).not.toHaveBeenCalled();
  click('[data-pv="close"]');
  expect(root.querySelector('[role="dialog"]')).toBeNull();
  expect((document.activeElement as HTMLElement).dataset.pv).toBe("directory");
});

test("a search with no matches remains editable and Escape closes the add dialog", async () => {
  const panel = mount(); await panel.load(); click('[data-pv="directory"]');
  const search = root.querySelector<HTMLInputElement>("#pv-kind-search")!;
  search.value = "unmatched"; search.dispatchEvent(new Event("input", { bubbles: true }));
  expect(root.querySelectorAll(".pv-add")).toHaveLength(0);
  expect(root.querySelector('[role="dialog"]')?.textContent).toContain("No providers match");
  expect(document.activeElement?.id).toBe("pv-kind-search");
  document.activeElement!.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  expect(root.querySelector('[role="dialog"]')).toBeNull();
  click('[data-pv="directory"]');
  expect(root.querySelector<HTMLInputElement>("#pv-kind-search")?.value).toBe("");
  expect(root.querySelectorAll(".pv-add")).toHaveLength(kinds.length);
});

test("Bedrock credentials are passwords and region is a working dropdown; docs open from metadata", async () => {
  api.providersKinds.mockResolvedValue({ kinds: [{ ...kinds[1], available: true, fields: ["region", "key_id", "secret", "api_version"], docs_url: "https://console.aws.amazon.com/bedrock/" }] });
  const panel = mount(); await panel.load(); connect('[data-pv="add"]');
  expect(root.querySelectorAll('input[type="password"]')).toHaveLength(2);
  click('[data-pv-field="region"]');
  const choice = [...document.querySelectorAll<HTMLButtonElement>('[role="menuitemradio"]')].find((node) => node.textContent?.includes("ap-northeast-1"));
  expect(choice).toBeDefined(); choice!.click();
  root.querySelector<HTMLInputElement>('[id$="-key_id"]')!.value = "access";
  root.querySelector<HTMLInputElement>('[id$="-secret"]')!.value = "private";
  root.querySelector<HTMLInputElement>('[id$="-api_version"]')!.value = "2024-01-01";
  click('[data-pv="docs"]');
  expect(api.openLink).toHaveBeenCalledWith("https://console.aws.amazon.com/bedrock/");
  api.providersAdd.mockRejectedValue(new Error("private"));
  click('[data-pv="save"]'); await flush();
  expect(api.providersAdd).toHaveBeenCalledWith({ kind: "bedrock", name: "Amazon Bedrock", region: "ap-northeast-1", key_id: "access", secret: "private", api_version: "2024-01-01" });
  expect(root.innerHTML).not.toContain("private");
});

test("Vertex accepts pasted or locally picked JSON without rendering credentials and rejects malformed accounts", async () => {
  const vertex = { kind: "vertex", name: "Vertex AI", group: "official", available: true, fields: ["service_account", "project", "location"] };
  api.providersKinds.mockResolvedValue({ kinds: [vertex] });
  const panel = mount(); await panel.load(); connect('[data-pv="add"]');
  const area = root.querySelector<HTMLTextAreaElement>("textarea")!;
  expect(area).not.toBeNull();
  area.value = JSON.stringify({ client_email: "user@example.com", private_key: "private-account-key" });
  area.dispatchEvent(new Event("input", { bubbles: true }));
  expect(area.value).toBe("");
  expect(root.querySelector('[role="dialog"]')?.textContent).toContain("Configured");
  expect(root.innerHTML).not.toContain("private-account-key");
  expect(JSON.stringify(panel.view())).not.toContain("private-account-key");
  root.querySelector<HTMLInputElement>('[id$="-project"]')!.value = "my-project";
  api.providersAdd.mockRejectedValue(new Error("private-account-key"));
  click('[data-pv="save"]'); await flush();
  expect(api.providersAdd).toHaveBeenCalledWith({ kind: "vertex", name: "Vertex AI", service_account: JSON.stringify({ client_email: "user@example.com", private_key: "private-account-key" }), project: "my-project", location: "us-central1" });
  expect(root.querySelector('textarea')).not.toBeNull();
  const fileInput = root.querySelector<HTMLInputElement>('input[type="file"]')!;
  const choose = vi.spyOn(fileInput, "click"); click('[data-pv="file"]'); expect(choose).toHaveBeenCalledOnce();
  Object.defineProperty(fileInput, "files", { configurable: true, value: [new File(['{"client_email":"missing-key"}'], "account.json", { type: "application/json" })] });
  fileInput.dispatchEvent(new Event("change", { bubbles: true })); await flush();
  expect(root.querySelector('[role="alert"]')?.textContent).toContain("client_email and private_key");
  expect(root.innerHTML).not.toContain("missing-key");
  const next = root.querySelector<HTMLInputElement>('input[type="file"]')!;
  Object.defineProperty(next, "files", { configurable: true, value: [new File(['{"client_email":"ok@example.com","private_key":"file-private-key"}'], "account.json")] });
  next.dispatchEvent(new Event("change", { bubbles: true })); await flush();
  expect(root.querySelector('[role="alert"]')).toBeNull();
  expect(root.innerHTML).not.toContain("file-private-key");
  expect(root.querySelector('[role="dialog"]')?.textContent).toContain("Configured");
  click('[data-pv="save"]'); await flush();
  expect(api.providersAdd).toHaveBeenLastCalledWith(expect.objectContaining({ service_account: '{"client_email":"ok@example.com","private_key":"file-private-key"}' }));
});

test("local kinds explain agent context and only flagged models carry a warning chip", async () => {
  api.providersList.mockResolvedValue({ providers: [{ ...connected, kind: "ollama", name: "Ollama" }] });
  api.providersKinds.mockResolvedValue({ kinds: [{ ...kinds[3], local_context_hint: true }] });
  api.providersModels.mockResolvedValue({ models: [
    { id: "small", name: "Small", context: 8192, context_warning: true },
    { id: "large", name: "Large", context: 131072, context_warning: false },
  ] });
  const panel = mount(); await panel.load(); click('[data-pv="open"]'); await flush();
  expect(root.querySelector('[role="dialog"]')?.textContent).toContain("at least 64K");
  expect(root.querySelectorAll('.pv-context-warning')).toHaveLength(1);
  expect(root.querySelector('.pv-context-warning')?.closest('li')?.textContent).toContain("Small");
});

test("editing a saved regional provider preserves unknown public configuration until explicitly changed", async () => {
  api.providersKinds.mockResolvedValue({ kinds: [{ ...kinds[1], available: true, fields: ["region", "key_id", "secret"] }] });
  api.providersList.mockResolvedValue({ providers: [{ ...connected, kind: "bedrock" }] });
  api.providersUpdate.mockRejectedValue(new Error("failed"));
  const panel = mount(); await panel.load(); click('[data-pv="open"]'); await flush();
  click('[data-pv="save"]'); await flush();
  expect(api.providersUpdate).toHaveBeenCalledWith({ id: "p3", name: "OpenAI" });
});

test("connected cards show status, a switch and a dialog button; the add dialog groups all kinds", async () => {
  const panel = mount(); await panel.load();
  expect(root.textContent).toContain("42 models");
  expect(root.querySelector('[role="switch"]')?.getAttribute("aria-checked")).toBe("true");
  expect(root.querySelector('.pv-grid .pv-card [data-pv="open"]')).not.toBeNull();
  expect(root.querySelector('[role="dialog"]')).toBeNull();
  click('[data-pv="directory"]');
  for (const title of ["Providers", "Add a provider", "Official", "Gateways and cloud", "Local"]) expect(root.textContent).toContain(title);
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
  expect(root.querySelectorAll(".pv > .pv-grid > .pv-card")).toHaveLength(1);
  expect(root.querySelector(".pv > .pv-grid > .pv-card")?.getAttribute("data-pv")).toBe("directory");
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
  connect('[data-pv="add"][data-id="openai_compatible"]');
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
  connect('[data-pv="add"][data-id="openai"]');
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
