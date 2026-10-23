// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { mountModelPicker, type ModelPicker } from "./model-picker";
import { setLocale } from "./i18n";
import type { CatalogModel, ModelCatalog, ModelProvider } from "./types";

const model = (id: string, provider_id: string, provider_name: string): CatalogModel => ({
  id, provider_id, provider_name, name: id, provider: "OpenAI", input_per_mtok: null, output_per_mtok: null,
  per_minute: null, free: false, variable_price: false, context_length: null, created_at_ms: null,
  capabilities: {}, roles: { answer: { usable: true, reason: null }, cleanup: { usable: true, reason: null } },
});
const catalog: ModelCatalog = { llm: [model("gpt-5", "p3", "OpenAI"), model("gpt-5-mini", "p3", "OpenAI"), model("openai/gpt-5", "openrouter", "OpenRouter")], stt: [], source: "live", fetched_at_ms: null, replaced: [] };
let pickers: ModelPicker[];
function mount(role: "answer" | "cleanup" = "answer", source = catalog, providers?: ModelProvider[]) {
  const slot = document.createElement("div"); document.body.append(slot);
  const picker = mountModelPicker(slot, { role, current: "@p3:gpt-5", catalog: source, providers, label: "Model", onChoose: vi.fn() });
  pickers.push(picker); picker.open(); return picker;
}
const chip = (id: string) => document.querySelector<HTMLButtonElement>(`[data-connection="${id}"]`)!;
const ids = () => [...document.querySelectorAll<HTMLElement>(".mp-opt")].map((row) => row.dataset.id);
beforeEach(() => { pickers = []; localStorage.clear(); setLocale("en"); vi.spyOn(HTMLElement.prototype, "scrollIntoView").mockImplementation(() => {}); });
afterEach(() => { pickers.forEach((p) => p.close()); document.body.innerHTML = ""; vi.restoreAllMocks(); setLocale("zh-TW"); });

test("provider buttons show counts, filter search, and remember the connection independently for each role", () => {
  const picker = mount();
  expect(chip("p3")?.textContent).toBe("OpenAI2");
  chip("p3").click();
  expect(ids()).toEqual(["@p3:gpt-5", "@p3:gpt-5-mini"]);
  const search = document.querySelector<HTMLInputElement>("#mp-search")!;
  search.value = "mini"; search.dispatchEvent(new Event("input", { bubbles: true }));
  expect(ids()).toEqual(["@p3:gpt-5-mini"]);
  picker.close(); picker.open();
  expect(chip("p3").getAttribute("aria-pressed")).toBe("true");
  picker.close(); mount("cleanup");
  expect(chip("").getAttribute("aria-pressed")).toBe("true");
});

test("Tab reaches chips and arrows select and focus the next provider without changing list keys", () => {
  mount();
  const search = document.querySelector<HTMLInputElement>("#mp-search")!;
  search.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", shiftKey: true, bubbles: true }));
  expect(document.activeElement).toBe(chip("openrouter"));
  chip("").focus();
  chip("").dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
  expect(document.activeElement).toBe(chip("p3"));
  expect(ids()).toEqual(["@p3:gpt-5", "@p3:gpt-5-mini"]);
  search.focus(); search.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true }));
  expect(document.querySelector('.mp-opt[aria-selected="true"]')?.getAttribute("data-id")).toBe("@p3:gpt-5-mini");
});

test("a removed connection and unavailable storage fall back to All", () => {
  const picker = mount(); chip("p3").click(); picker.close();
  picker.update({ catalog: { ...catalog, llm: [catalog.llm[2]] } }); picker.open();
  expect(chip("").getAttribute("aria-pressed")).toBe("true"); picker.close();
  vi.spyOn(localStorage, "getItem").mockImplementation(() => { throw new Error("blocked"); });
  vi.spyOn(localStorage, "setItem").mockImplementation(() => { throw new Error("blocked"); });
  mount(); expect(chip("").getAttribute("aria-pressed")).toBe("true"); chip("p3").click();
  expect(ids()).toHaveLength(2);
  expect(document.querySelectorAll(".mp-opt .mp-row-mark")).toHaveLength(2);
});

test("enabled empty connections remain selectable and disabled connections are omitted", () => {
  const provider: ModelProvider = { id: "p9", kind: "ollama", name: "My server", base_url: "http://localhost:11434", enabled: true, has_key: false, models: 0, status: "unknown" };
  mount("answer", catalog, [provider, { ...provider, id: "disabled", enabled: false }]);
  expect(chip("p9").textContent).toBe("My server0");
  expect(chip("disabled")).toBeNull();
  expect(chip("p9").querySelector(".mp-row-mark")).not.toBeNull();
  chip("p9").click();
  expect(ids()).toEqual([]);
  expect(document.querySelector<HTMLElement>(".mp-empty")?.hidden).toBe(false);
});

test("catalog updates preserve a chosen connection and fall back to All when it disappears", () => {
  const picker = mount(); chip("p3").click();
  picker.update({ catalog: { ...catalog, llm: [...catalog.llm, model("gpt-6", "p3", "OpenAI")] } });
  expect(chip("p3").textContent).toBe("OpenAI3");
  expect(ids()).toHaveLength(3);
  picker.update({ catalog: { ...catalog, llm: [catalog.llm[2]] } });
  expect(chip("").getAttribute("aria-pressed")).toBe("true");
  expect(ids()).toEqual(["openai/gpt-5"]);
});
