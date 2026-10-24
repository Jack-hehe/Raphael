// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { chooserPrice, chooserSources, mountModelPicker, type ModelPicker } from "./model-picker";
import type { CatalogModel, ModelCatalog, ModelProvider } from "./types";
const model = (id: string, provider_id = "openrouter", provider = "OpenAI"): CatalogModel => ({
  id, provider_id, provider_name: provider_id === "openrouter" ? "OpenRouter" : provider_id, name: id, provider,
  input_per_mtok: null, output_per_mtok: null, per_minute: null, free: false, variable_price: false,
  context_length: 128000, created_at_ms: null, capabilities: {}, roles: { answer: { usable: true, reason: null } },
});
const catalog = (llm: CatalogModel[]): ModelCatalog => ({ llm, stt: [], source: "live", fetched_at_ms: null, replaced: [] });
let picker: ModelPicker;
afterEach(() => { picker?.close(); document.body.innerHTML = ""; localStorage.clear(); });
function mount(models: CatalogModel[], providers?: ModelProvider[]) {
  const slot = document.createElement("div"); document.body.append(slot);
  picker = mountModelPicker(slot, { role: "answer", current: models[0]?.id ?? "", catalog: catalog(models), providers, label: "Answer", onChoose: vi.fn() });
  picker.open(); return picker;
}
test("only sources above twelve models show search and sort; maker dropdown requires multiple makers", () => {
  mount(Array.from({ length: 12 }, (_, i) => model(`openai/m${i}`)));
  expect(document.querySelector(".mc-tools")).toBeNull();
  picker.update({ catalog: catalog(Array.from({ length: 13 }, (_, i) => model(`openai/m${i}`))) });
  expect(document.querySelector(".mc-search")).not.toBeNull(); expect(document.querySelector("[data-sort]")).not.toBeNull();
  expect(document.querySelector("[data-maker]")).toBeNull();
  picker.update({ catalog: catalog([...Array.from({ length: 12 }, (_, i) => model(`openai/m${i}`)), model("anthropic/sonnet", "openrouter", "Anthropic")]) });
  expect(document.querySelector("[data-maker]")).not.toBeNull();
  const search = document.querySelector<HTMLInputElement>(".mc-search")!;
  search.value = "sonnet"; search.dispatchEvent(new Event("input", { bubbles: true }));
  expect(document.querySelectorAll(".mc-row")).toHaveLength(1);
});
test("enabled empty providers remain in the proper group and disabled providers disappear", () => {
  const provider: ModelProvider = { id: "local", name: "Ollama", kind: "ollama", base_url: "http://localhost:11434", enabled: true, has_key: false, models: 0, status: "unknown" };
  const sources = chooserSources({ role: "cleanup", catalog: catalog([]), providers: [provider, { ...provider, id: "off", enabled: false }], label: "Cleanup", current: "" });
  expect(sources.map((s) => [s.id, s.group])).toEqual([["local", "local"]]);
  mount([], [provider]); document.querySelector<HTMLButtonElement>('[data-source="local"]')!.click();
  expect(document.querySelectorAll(".mc-row")).toHaveLength(0); expect(document.querySelector(".mc-empty")).not.toBeNull();
});
test("price fallback requires exact maker plus model identity, otherwise it is blank", () => {
  const direct = model("gpt-5", "p3"), router = { ...model("openai/gpt-5"), input_per_mtok: 1.25, output_per_mtok: 10 };
  expect(chooserPrice(direct, [router], "answer")).toBe("$1.25 / $10");
  expect(chooserPrice({ ...direct, id: "gpt-5-mini" }, [router], "answer")).toBe("");
  expect(chooserPrice({ ...direct, provider: "Other" }, [router], "answer")).toBe("");
  expect(chooserPrice(direct, [], "answer")).toBe("");
});
test("API rows show maker logos and escaped names; a saved recent model leads without duplication", () => {
  localStorage.setItem("raphael.chooser.recent.answer", JSON.stringify(["openai/recent"]));
  mount([model("openai/other"), { ...model("openai/recent"), name: "<img src=x>" }]);
  const rows = [...document.querySelectorAll<HTMLElement>(".mc-row")];
  expect(rows.map((row) => row.dataset.model)).toEqual(["openai/recent", "openai/other"]);
  expect(rows[0].querySelector("img[src=x]")).toBeNull(); expect(rows[0].textContent).toContain("<img src=x>");
  expect(rows[0].querySelector(".mono")).not.toBeNull();
});
