// The model picker's list without a page: what it shows of each model, the search, the orders, the
// maker, the rows a role cannot use, the keys and the chosen model. The page around it (the button
// and the popover) is ui.ts's, and is tried in the app.

import { afterEach, describe, expect, test } from "vitest";
import { setLocale } from "./i18n";
import {
  PickerList,
  acceptsImages,
  fitFor,
  modelPickerHTML,
  usableFor,
  footText,
  formatContext,
  listHTML,
  modelsFor,
  moveIndex,
  pickerRows,
  priceDescription,
  priceText,
  providerChoices,
  reasonText,
  replacementNotices,
  sortChoices,
  withFavorite,
} from "./model-picker";
import type { CatalogModel, ModelCatalog } from "./types";

afterEach(() => setLocale("zh-TW"));

test("the current model is pinned before direct provider groups and OpenRouter comes last", () => {
  const local = llm("llama3.1:8b", { name: "Llama", provider: "Meta", provider_id: "p4", provider_name: "Ollama", input_per_mtok: 0 });
  const direct = llm("gpt-5.1", { name: "GPT", provider: "OpenAI", provider_id: "p3", provider_name: "OpenAI" });
  const catalog = { ...CATALOG, llm: [local, direct, LUNA] };
  const list = new PickerList("answer", modelsFor(catalog, "answer"), "openai/gpt-6-luna");
  expect(list.rows.map((r) => r.model.id)).toEqual(["openai/gpt-6-luna", "@p4:llama3.1:8b", "@p3:gpt-5.1"]);
  const html = listHTML(list);
  expect(html.indexOf("mp-group")).toBeLessThan(html.indexOf("Ollama"));
  expect(html).toContain("mp-provider-mark");
  list.point("@p4:llama3.1:8b");
  expect(list.chosen()).toBe("@p4:llama3.1:8b");
  expect(modelPickerHTML({ role: "answer", current: "@p4:llama3.1:8b", catalog, label: "Model" })).toContain("Llama");
});

test("direct connections stay grouped with favourites inside each group and OpenRouter last", () => {
  const direct = llm("gpt-5", { name: "GPT-5", provider_id: "p3", provider_name: "OpenAI" });
  const local = llm("llama:8b", { name: "Llama", provider_id: "p4", provider_name: "Ollama" });
  const list = new PickerList("answer", [OLD, direct, local, LUNA], "missing", {}, [OLD.id]);
  expect(list.rows.map((r) => r.model.id)).toEqual(["@p4:llama:8b", "@p3:gpt-5", "openai/gpt-5", "openai/gpt-6-luna"]);
});

test("all-provider search ranks exact and prefix names across connections before substring matches", () => {
  const direct = llm("gpt-5", { name: "GPT-5", provider_id: "p3", provider_name: "OpenAI", input_per_mtok: 99 });
  const prefix = llm("gpt-5-mini", { name: "GPT-5 Mini", provider_id: "p3", provider_name: "OpenAI" });
  const tail = llm("other/gpt-5", { name: "Something GPT-5", free: true });
  const list = new PickerList("answer", [tail, prefix, OLD, direct], tail.id, { query: "gpt-5" }, [tail.id]);
  expect(list.rows.map((r) => r.model.id)).toEqual(["@p3:gpt-5", OLD.id, "@p3:gpt-5-mini", tail.id]);
});

const DAY = 86_400_000;
const OCT_18 = Date.UTC(2026, 9, 18, 4);

/** A language model the cleanup and the answers can use; `over` changes what a test needs. */
function llm(id: string, over: Partial<CatalogModel> = {}): CatalogModel {
  return {
    id,
    name: id.split("/")[1],
    provider: id.split("/")[0],
    input_per_mtok: 1,
    output_per_mtok: 2,
    per_minute: null,
    free: false,
    variable_price: false,
    context_length: 128_000,
    created_at_ms: OCT_18,
    capabilities: { structured_output: true, reasoning: true, reasoning_off: true, max_tokens: true },
    roles: { cleanup: { usable: true, reason: null }, answer: { usable: true, reason: null } },
    ...over,
  };
}

const unusable = (reason: string): Partial<CatalogModel> => ({
  roles: { cleanup: { usable: false, reason }, answer: { usable: false, reason } },
});

function stt(id: string, over: Partial<CatalogModel> = {}): CatalogModel {
  return {
    ...llm(id),
    input_per_mtok: null,
    output_per_mtok: null,
    per_minute: 0.002,
    context_length: null,
    capabilities: { dictionary_hints: false },
    roles: { stt: { usable: true, reason: null } },
    ...over,
  };
}

const LUNA = llm("openai/gpt-6-luna", { name: "GPT-6 Luna", provider: "OpenAI", input_per_mtok: 0.1, output_per_mtok: 0.5, context_length: 1_050_000 });
const HAIKU = llm("anthropic/claude-haiku-5.5", { name: "Claude Haiku 5.5", provider: "Anthropic", input_per_mtok: 0.1, output_per_mtok: 0.5, created_at_ms: OCT_18 + DAY });
const ALIAS = llm("~openai/gpt-luna-latest", { name: "GPT Luna Latest", provider: "OpenAI", input_per_mtok: 0.1, output_per_mtok: 0.5 });
const FREE = llm("nvidia/nemotron:free", { name: "Nemotron 3 Super (free)", provider: "NVIDIA", input_per_mtok: 0, output_per_mtok: 0, free: true, created_at_ms: null });
const ROUTER = llm("typesafe/jev-router", { name: "Jev Router", provider: "TypeSafe", input_per_mtok: null, output_per_mtok: null, variable_price: true });
const DEAR = llm("openai/gpt-10", { name: "GPT-10", provider: "OpenAI", input_per_mtok: 20, output_per_mtok: 80, created_at_ms: OCT_18 + 2 * DAY });
const OLD = llm("openai/gpt-5", { name: "GPT-5", provider: "OpenAI", input_per_mtok: 1.25, output_per_mtok: 10, created_at_ms: OCT_18 - 300 * DAY });
const UNKNOWN = llm("odd/model", { name: "Odd", provider: "Odd", input_per_mtok: null, output_per_mtok: null });
const FLASH = llm("google/gemini-3.8-flash", { name: "Gemini 3.8 Flash", provider: "Google", input_per_mtok: 0.825, output_per_mtok: 4.125, ...unusable("reasoning_required") });
const MOON = llm("moondream/moondream3-preview", { name: "Moondream 3 Preview", provider: "Moondream", input_per_mtok: 0.3, output_per_mtok: 2.5, ...unusable("no_structured_output") });
const LLMS = [LUNA, HAIKU, ALIAS, FREE, ROUTER, DEAR, OLD, UNKNOWN, FLASH, MOON];

const SCRIBE = stt("elevenlabs/scribe-v2", { name: "Scribe v2", provider: "ElevenLabs", per_minute: 0.00366667 });
const WHISPER = stt("openai/whisper-large-v3-turbo", { name: "Whisper Large V3 Turbo", provider: "OpenAI", per_minute: 0.0001998, capabilities: { dictionary_hints: true } });
const GEMINI = stt("google/gemini-3.5-transcribe", { name: "Gemini 3.5 Transcribe", provider: "Google", per_minute: null, input_per_mtok: 2, output_per_mtok: 12 });
const MAI = stt("microsoft/mai-transcribe-2", { name: "MAI-Transcribe 2", provider: "Microsoft AI", per_minute: 0.00166667 });
const STTS = [SCRIBE, WHISPER, GEMINI, MAI];

const CATALOG: ModelCatalog = { llm: LLMS, stt: STTS, fetched_at_ms: Date.now(), source: "live", replaced: [] };

const ids = (models: { model: CatalogModel }[]): string[] => models.map((row) => row.model.id);
const rows = (state: Partial<{ query: string; sort: "price" | "name" | "newest"; provider: string }> = {}) =>
  ids(pickerRows(LLMS, "cleanup", { query: "", sort: "price", provider: "", ...state }));

describe("prices", () => {
  test("a language model shows its prices in and out per million tokens, which the list's heading names", () => {
    expect(priceText(LUNA, "cleanup")).toBe("$0.10 / $0.50");
    expect(priceText(DEAR, "answer")).toBe("$20 / $80");
    expect(priceText(llm("a/b", { input_per_mtok: null }), "cleanup")).toBe("? / $2.00");
    expect(priceDescription(LUNA, "cleanup")).toBe("輸入 $0.10 / 輸出 $0.50 每百萬 token");
  });

  test("a free model is free, a router's price varies, and a model without one says so", () => {
    expect(priceText(FREE, "cleanup")).toBe("免費");
    expect(priceText(ROUTER, "cleanup")).toBe("價格不固定");
    expect(priceText(UNKNOWN, "cleanup")).toBe("價格未知");
    expect(priceDescription(ROUTER, "cleanup")).toBe("價格不固定");
  });

  test("a speech model is priced per minute, or per million tokens when it is priced by the token", () => {
    expect(priceText(SCRIBE, "stt")).toBe("每分鐘 $0.0037");
    expect(priceText(WHISPER, "stt")).toBe("每分鐘 $0.00020");
    expect(priceText(MAI, "stt")).toBe("每分鐘 $0.0017");
    expect(priceText(GEMINI, "stt")).toBe("$2.00 / $12 每百萬 token");
    expect(priceDescription(GEMINI, "stt")).toBe("$2.00 / $12 每百萬 token");
  });

  test("the prices speak the language of the window", () => {
    setLocale("en");
    expect(priceText(SCRIBE, "stt")).toBe("$0.0037 per minute");
    expect(priceText(FREE, "cleanup")).toBe("Free");
    expect(priceText(ROUTER, "cleanup")).toBe("Price varies");
    expect(priceDescription(LUNA, "cleanup")).toBe("Input $0.10 / output $0.50 per million tokens");
  });

  test("context lengths are short", () => {
    expect(formatContext(1_050_000)).toBe("1.05M");
    expect(formatContext(1_048_576)).toBe("1.05M");
    expect(formatContext(1_000_000)).toBe("1M");
    expect(formatContext(2_000_000)).toBe("2M");
    expect(formatContext(131_072)).toBe("131K");
    expect(formatContext(40_960)).toBe("41K");
    expect(formatContext(512)).toBe("512");
  });
});

describe("the search", () => {
  test("finds a model by its name, its maker or its id, whatever the case", () => {
    // The two cost the same, so their names decide, and a space comes before a hyphen.
    expect(rows({ query: "LUNA" })).toEqual(["~openai/gpt-luna-latest", "openai/gpt-6-luna"]);
    expect(rows({ query: "anthropic" })).toEqual(["anthropic/claude-haiku-5.5"]);
    expect(rows({ query: "luna-latest" })).toEqual(["~openai/gpt-luna-latest"]);
  });

  test("needs every word, and looks past spaces and punctuation", () => {
    expect(rows({ query: "gpt6" })).toEqual(["openai/gpt-6-luna"]);
    expect(rows({ query: "moondream preview" })).toEqual(["moondream/moondream3-preview"]);
    expect(rows({ query: "  claude   5.5 " })).toEqual(["anthropic/claude-haiku-5.5"]);
    expect(rows({ query: "no such model" })).toEqual([]);
  });

  test("a new search highlights its first row, and an empty one the chosen model again", () => {
    const list = new PickerList("cleanup", LLMS, "anthropic/claude-haiku-5.5");
    expect(list.activeId()).toBe("anthropic/claude-haiku-5.5");
    list.setQuery("gpt");
    expect(list.active).toBe(0);
    list.setQuery("zzz");
    expect([list.active, list.chosen()]).toEqual([-1, null]);
    list.setQuery("");
    expect(list.activeId()).toBe("anthropic/claude-haiku-5.5");
  });
});

describe("the order", () => {
  test("price, low to high: free first, then by the price in and out, then the routers, then the unknown", () => {
    expect(rows()).toEqual([
      "nvidia/nemotron:free",
      "anthropic/claude-haiku-5.5",
      "~openai/gpt-luna-latest",
      "openai/gpt-6-luna",
      "openai/gpt-5",
      "openai/gpt-10",
      "typesafe/jev-router",
      "odd/model",
      // the role cannot use these: after the others, in the same order
      "moondream/moondream3-preview",
      "google/gemini-3.8-flash",
    ]);
  });

  test("a speech model priced by the minute comes before one priced by the token", () => {
    const order = ids(pickerRows(STTS, "stt", { query: "", sort: "price", provider: "" }));
    expect(order).toEqual(["openai/whisper-large-v3-turbo", "microsoft/mai-transcribe-2", "elevenlabs/scribe-v2", "google/gemini-3.5-transcribe"]);
  });

  test("by name, as people read numbers", () => {
    expect(rows({ sort: "name", query: "openai" })).toEqual(["~openai/gpt-luna-latest", "openai/gpt-5", "openai/gpt-6-luna", "openai/gpt-10"]);
  });

  test("newest first, a model without a date last", () => {
    expect(rows({ sort: "newest", provider: "OpenAI" })).toEqual(["openai/gpt-10", "~openai/gpt-luna-latest", "openai/gpt-6-luna", "openai/gpt-5"]);
    const order = rows({ sort: "newest" });
    expect(order.indexOf("nvidia/nemotron:free")).toBe(order.length - 3); // last of the usable ones
  });

  test("the orders are offered in this order", () => {
    expect(sortChoices().map((c) => c.value)).toEqual(["price", "name", "newest"]);
  });

  test("another order keeps the highlighted model highlighted", () => {
    const list = new PickerList("cleanup", LLMS, "openai/gpt-6-luna");
    list.setSort("name");
    expect(list.activeId()).toBe("openai/gpt-6-luna");
  });
});

describe("the maker", () => {
  test("narrows the list to one maker", () => {
    expect(rows({ provider: "OpenAI" })).toEqual(["~openai/gpt-luna-latest", "openai/gpt-6-luna", "openai/gpt-5", "openai/gpt-10"]);
    expect(rows({ provider: "OpenAI", query: "luna" })).toEqual(["~openai/gpt-luna-latest", "openai/gpt-6-luna"]);
  });

  test("is chosen among the makers by name, each with its count, after all of them", () => {
    expect(providerChoices(LLMS)).toEqual([
      { value: "", label: "所有供應商", detail: "10" },
      { value: "Anthropic", label: "Anthropic", detail: "1" },
      { value: "Google", label: "Google", detail: "1" },
      { value: "Moondream", label: "Moondream", detail: "1" },
      { value: "NVIDIA", label: "NVIDIA", detail: "1" },
      { value: "Odd", label: "Odd", detail: "1" },
      { value: "OpenAI", label: "OpenAI", detail: "4" },
      { value: "TypeSafe", label: "TypeSafe", detail: "1" },
    ]);
  });

  test("a maker the highlighted model is not of moves the highlight to the first row", () => {
    const list = new PickerList("cleanup", LLMS, "openai/gpt-6-luna");
    list.setProvider("Anthropic");
    expect(list.activeId()).toBe("anthropic/claude-haiku-5.5");
  });
});

describe("the rows a role cannot use", () => {
  test("come last, under a heading, greyed with the reason in plain words", () => {
    const html = listHTML(new PickerList("cleanup", LLMS, "openai/gpt-6-luna"));
    const heading = html.indexOf("無法用來整理聽寫");
    expect(heading).toBeGreaterThan(html.indexOf('data-id="odd/model"'));
    expect(html.slice(heading)).toContain("無法用來整理聽寫");
    expect(html).toMatch(/data-id="google\/gemini-3\.8-flash" aria-selected="false" aria-disabled="true"/);
    expect(html).toContain("一定會先推理，太慢");
    expect(html).toContain("無法依指定格式（JSON）回覆");
    expect(html.match(/aria-disabled="true"/g)).toHaveLength(2);
  });

  test("can be highlighted, so that their reason is read out, but never chosen", () => {
    const list = new PickerList("cleanup", LLMS, "openai/gpt-6-luna");
    expect(list.move("End")).toBe(true);
    expect(list.activeId()).toBe("google/gemini-3.8-flash");
    expect(list.chosen()).toBeNull();
    list.point("openai/gpt-10");
    expect(list.chosen()).toBe("openai/gpt-10");
  });

  test("every reason has its words, and a reason the app does not know still has some", () => {
    expect(reasonText("no_structured_output")).toBe("無法依指定格式（JSON）回覆");
    expect(reasonText("no_reasoning_setting")).toBe("不接受關閉推理的設定");
    expect(reasonText("no_max_tokens")).toBe("無法限制回覆長度");
    expect(reasonText("something_new")).toBe("這裡無法使用");
    expect(reasonText(null)).toBe("這裡無法使用");
    setLocale("en");
    expect(reasonText("reasoning_required")).toBe("Always reasons first, which is too slow");
  });

  test("a role sees only its kind of model", () => {
    expect(modelsFor(CATALOG, "cleanup")).toBe(LLMS);
    expect(modelsFor(CATALOG, "answer")).toBe(LLMS);
    expect(modelsFor(CATALOG, "stt")).toBe(STTS);
  });
});

describe("the keys", () => {
  test("the arrows go round, Page Up and Page Down go ten rows and stop at the ends", () => {
    expect(moveIndex(5, 4, "ArrowDown")).toBe(0);
    expect(moveIndex(5, 0, "ArrowUp")).toBe(4);
    expect(moveIndex(5, -1, "ArrowDown")).toBe(0);
    expect(moveIndex(5, -1, "ArrowUp")).toBe(4);
    expect(moveIndex(30, 3, "PageDown")).toBe(13);
    expect(moveIndex(30, 25, "PageDown")).toBe(29);
    expect(moveIndex(30, 4, "PageUp")).toBe(0);
    expect([moveIndex(30, 4, "Home"), moveIndex(30, 4, "End")]).toEqual([0, 29]);
    expect(moveIndex(5, 2, "a")).toBeNull();
    expect(moveIndex(0, -1, "ArrowDown")).toBeNull();
  });

  test("the list opens on the chosen model, and Enter chooses the highlighted one", () => {
    const list = new PickerList("cleanup", LLMS, "openai/gpt-6-luna");
    expect(list.activeId()).toBe("openai/gpt-6-luna");
    expect(list.move("ArrowDown")).toBe(true);
    expect(list.chosen()).toBe("nvidia/nemotron:free");
    expect(list.move("ArrowUp") && list.move("ArrowUp")).toBe(true);
    expect(list.chosen()).toBeNull();
    expect(list.move("x")).toBe(false);
  });

  test("a chosen model the list does not show opens the list on its first row", () => {
    const list = new PickerList("cleanup", LLMS, "gone/model");
    expect(list.activeId()).toBe("nvidia/nemotron:free");
  });

  test("a newer catalog keeps the search and the highlighted model", () => {
    // What an open picker does when the catalog changes: a new list in the old one's state, on
    // the new models, with the model that was highlighted highlighted again.
    const before = new PickerList("cleanup", LLMS, "openai/gpt-6-luna", { query: "gpt" });
    before.point("openai/gpt-10");
    const after = new PickerList("cleanup", [...LLMS, llm("openai/gpt-11", { name: "GPT-11", provider: "OpenAI" })], before.current, before.state);
    after.point(before.activeId()!);
    expect(after.state.query).toBe("gpt");
    expect(after.activeId()).toBe("openai/gpt-10");
    expect(ids(after.rows)).toContain("openai/gpt-11");
  });
});

describe("the chosen model", () => {
  test("is checked, and only it", () => {
    const html = listHTML(new PickerList("cleanup", LLMS, "anthropic/claude-haiku-5.5"));
    expect(html.match(/data-current/g)).toHaveLength(1);
    expect(html).toMatch(/data-id="anthropic\/claude-haiku-5\.5"[^>]*data-current/);
    const row = html.slice(html.indexOf('data-id="anthropic/claude-haiku-5.5"'));
    expect(row.slice(0, row.indexOf("</button>"))).toContain("<svg");
    expect(row).toContain("Claude Haiku 5.5, OpenRouter, Anthropic, 輸入 $0.10 / 輸出 $0.50 每百萬 token, 目前使用");
  });

  test("a row shows its maker and its context, and no badge", () => {
    const html = listHTML(new PickerList("cleanup", [LUNA], "x/y"));
    expect(html).toContain("GPT-6 Luna");
    expect(html).toContain("OpenAI · 1.05M 上下文");
    expect(html).not.toContain("mp-badge");
    expect(html).not.toContain("推薦");
    expect(html).toContain('<span class="mp-price num">$0.10 / $0.50</span>');
    expect(html).not.toContain("data-current");
  });

  test("a speech model says when it uses the dictionary, and the newest order shows the dates", () => {
    const html = listHTML(new PickerList("stt", STTS, "elevenlabs/scribe-v2", { sort: "newest" }));
    expect(html).toContain("OpenAI · 會參考詞典 · 2026 年 10 月 18 日上架");
    expect(html).not.toContain("上下文");
  });

  test("text from the list is escaped", () => {
    const html = listHTML(new PickerList("cleanup", [llm("x/<b>", { name: "<img src=x>", provider: '"Q"' })], "x/y"));
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;img src=x&gt;");
    expect(html).toContain("&quot;Q&quot;");
  });
});

describe("favourites", () => {
  const list = (favorites: string[], state = {}, current = "openai/gpt-6-luna") => new PickerList("cleanup", LLMS, current, { connection: "openrouter", ...state }, favorites);

  test("none at all: the list is as it was, with no heading", () => {
    const html = listHTML(list([]));
    expect(html).not.toContain("我的最愛");
    expect(html).not.toContain("所有模型");
    expect(ids(list([]).rows)).toEqual(rows());
  });

  test("come first, in the order of the list, under a heading, and the other models under one of theirs", () => {
    const l = list(["openai/gpt-10", "nvidia/nemotron:free"]);
    expect(ids(l.rows).slice(0, 3)).toEqual(["nvidia/nemotron:free", "openai/gpt-10", "anthropic/claude-haiku-5.5"]);
    expect(l.rows.map((row) => row.favorite).slice(0, 4)).toEqual([true, true, false, false]);
    const html = listHTML(l);
    expect(html.indexOf("我的最愛")).toBeLessThan(html.indexOf('data-id="nvidia/nemotron:free"'));
    expect(html.indexOf("所有模型")).toBeGreaterThan(html.indexOf('data-id="openai/gpt-10"'));
    expect(html.indexOf("所有模型")).toBeLessThan(html.indexOf('data-id="openai/gpt-6-luna"'));
  });

  test("are listed once", () => {
    const l = list(["openai/gpt-10"]);
    expect(ids(l.rows).filter((id) => id === "openai/gpt-10")).toHaveLength(1);
    expect(l.rows).toHaveLength(LLMS.length);
  });

  test("follow the order that was chosen", () => {
    const l = list(["openai/gpt-10", "openai/gpt-5", "openai/gpt-6-luna"], { sort: "name" });
    expect(ids(l.rows).slice(0, 3)).toEqual(["openai/gpt-5", "openai/gpt-6-luna", "openai/gpt-10"]);
    l.setSort("price");
    expect(ids(l.rows).slice(0, 3)).toEqual(["openai/gpt-6-luna", "openai/gpt-5", "openai/gpt-10"]);
  });

  test("that answer a search still come first, and those that do not are gone", () => {
    const l = list(["openai/gpt-10", "nvidia/nemotron:free"]);
    l.setQuery("gpt");
    expect(ids(l.rows).slice(0, 2)).toEqual(["openai/gpt-10", "~openai/gpt-luna-latest"]);
    expect(ids(l.rows)).not.toContain("nvidia/nemotron:free");
    expect(l.rows.map((row) => row.favorite)).toEqual([true, false, false, false]);
    expect(listHTML(l)).toContain("我的最愛");
    l.setQuery("zzz");
    expect(l.rows).toEqual([]);
  });

  test("are narrowed to a maker too", () => {
    const l = list(["openai/gpt-10", "nvidia/nemotron:free"]);
    l.setProvider("NVIDIA");
    expect(ids(l.rows)).toEqual(["nvidia/nemotron:free"]);
  });

  test("whose model the catalog no longer has are not shown, and stay in the list that is saved", () => {
    const l = list(["gone/model", "openai/gpt-10"]);
    expect(ids(l.rows)[0]).toBe("openai/gpt-10");
    expect(ids(l.rows)).not.toContain("gone/model");
    expect(l.rows.filter((row) => row.favorite)).toHaveLength(1);
    expect(l.favorites).toEqual(["gone/model", "openai/gpt-10"]);
    expect(withFavorite(l.favorites, "openai/gpt-5", true)).toEqual(["gone/model", "openai/gpt-10", "openai/gpt-5"]);
  });

  test("a favourite the role cannot use stays among the favourites, greyed with its reason", () => {
    const l = list(["google/gemini-3.8-flash", "openai/gpt-10"]);
    expect(ids(l.rows).slice(0, 2)).toEqual(["openai/gpt-10", "google/gemini-3.8-flash"]);
    const html = listHTML(l);
    expect(html).toMatch(/data-id="google\/gemini-3\.8-flash"[^>]*aria-disabled="true"/);
    expect(html.indexOf("一定會先推理，太慢")).toBeLessThan(html.indexOf("所有模型"));
    // The heading over the other models that cannot be used comes once, after them.
    expect(html.match(/無法用來整理聽寫/g)).toHaveLength(1);
    expect(html.indexOf("無法用來整理聽寫")).toBeGreaterThan(html.indexOf('data-id="odd/model"'));
  });

  test("a favourite is chosen like any model", () => {
    const l = list(["openai/gpt-10"], {}, "openai/gpt-10");
    expect(l.activeId()).toBe("openai/gpt-10");
    expect(l.chosen()).toBe("openai/gpt-10");
  });

  test("each row has a real star button beside its option, never inside it, that says what it does", () => {
    const html = listHTML(list(["openai/gpt-10"]));
    const fav = html.slice(html.indexOf('data-id="openai/gpt-10"'));
    const optionEnd = fav.indexOf("</button>");
    expect(fav.slice(0, optionEnd)).not.toContain("mp-star");
    const star = fav.slice(optionEnd).match(/<button type="button" class="mp-star"[^>]*>/)![0];
    expect(star).toContain('data-star="openai/gpt-10"');
    expect(star).toContain('aria-label="從最愛移除：GPT-10"');
    expect(star).toContain('tabindex="-1"');
    expect(star).toContain("data-on");
    const plain = html.slice(html.indexOf('data-id="openai/gpt-5"'));
    const plainStar = plain.slice(plain.indexOf("</button>")).match(/<button type="button" class="mp-star"[^>]*>/)![0];
    expect(plainStar).toContain('aria-label="加到最愛：GPT-5"');
    expect(plainStar).not.toContain("data-on");
    expect(html.match(/class="mp-star"/g)).toHaveLength(LLMS.length);
  });

  test("the star speaks the language of the window", () => {
    setLocale("en");
    expect(listHTML(list(["openai/gpt-10"]))).toContain('aria-label="Remove from favorites: GPT-10"');
    expect(listHTML(list([]))).toContain('aria-label="Add to favorites: GPT-10"');
  });

  test("a favourite added or removed moves the model and keeps the highlight on it", () => {
    const l = list([]);
    l.point("openai/gpt-10");
    l.setFavorites(withFavorite(l.favorites, "openai/gpt-10", true));
    expect(ids(l.rows)[0]).toBe("openai/gpt-10");
    expect(l.activeId()).toBe("openai/gpt-10");
    l.setFavorites(withFavorite(l.favorites, "openai/gpt-10", false));
    expect(ids(l.rows)).toEqual(rows());
    expect(l.activeId()).toBe("openai/gpt-10");
  });

  test("withFavorite adds once at the end and removes", () => {
    expect(withFavorite(["a"], "b", true)).toEqual(["a", "b"]);
    expect(withFavorite(["a", "b"], "a", true)).toEqual(["a", "b"]);
    expect(withFavorite(["a", "b"], "a", false)).toEqual(["b"]);
    expect(withFavorite(["a"], "z", false)).toEqual(["a"]);
  });
});

describe("the small print and the notices", () => {
  test("says how many models are shown and where their prices are from", () => {
    expect(footText(CATALOG, 8)).toBe("8 個模型 · 價格來自 OpenRouter，今天更新。實際花費依 OpenRouter 選用的供應商而定。");
    const builtin: ModelCatalog = { ...CATALOG, source: "builtin", fetched_at_ms: null };
    expect(footText(builtin, 3)).toContain("暫時連不上 OpenRouter，只列出拉斐爾原本提供的模型。");
    expect(footText(null, 0)).toBe("");
  });

  test("tells of each chosen model that went back to its default", () => {
    const catalog: ModelCatalog = {
      ...CATALOG,
      replaced: [
        { key: "cleanup_model", role: "cleanup", model: "tencent/hunyuan-a13b-instruct", default: "openai/gpt-6-luna" },
        { key: "stt_cloud_model", role: "stt", model: "deepgram/nova-3", default: "elevenlabs/scribe-v2" },
      ],
    };
    expect(replacementNotices(catalog)).toEqual([
      "OpenRouter 已不再提供「tencent/hunyuan-a13b-instruct」，整理文字已改回預設的「GPT-6 Luna」。",
      "OpenRouter 已不再提供「deepgram/nova-3」，語音轉文字已改回預設的「Scribe v2」。",
    ]);
    setLocale("en");
    expect(replacementNotices(catalog)[0]).toBe(
      "OpenRouter no longer lists tencent/hunyuan-a13b-instruct, so the cleanup model is back to the default, GPT-6 Luna.",
    );
    expect(replacementNotices(CATALOG)).toEqual([]);
  });
});

describe("the picker of 看圖片 (the role vision)", () => {
  const LEAD = { id: "", name: "與回答相同" };
  const seeing = llm("google/gemini-4-flash", { input_modalities: ["text", "image"] });
  const blind = llm("openai/gpt-6-luna", { input_modalities: ["text"] });
  const unsaid = llm("acme/old-model");

  test("chooses among the language models; those that read images can be used, the others say why", () => {
    expect(modelsFor(CATALOG, "vision")).toBe(CATALOG.llm);
    expect(usableFor(seeing, "vision")).toBe(true);
    expect(usableFor(blind, "vision")).toBe(false);
    expect(fitFor(blind, "vision")).toEqual({ usable: false, reason: "no_image_input" });
    expect(reasonText("no_image_input")).toBe("無法讀圖片");
    // a catalog that says nothing of what a model reads: the core still refuses an image to one that cannot take it
    expect(acceptsImages(unsaid)).toBe(true);
  });

  test("lists 「與回答相同」 above every model, under no heading, without a star or a price", () => {
    const list = new PickerList("vision", [blind, seeing], "", {}, [], LEAD);
    expect(list.rows.map((row) => row.model.id)).toEqual(["", "google/gemini-4-flash", "openai/gpt-6-luna"]);
    expect(list.rows[0].lead).toBe(true);
    expect(list.modelCount()).toBe(2);
    expect(list.chosen()).toBe(""); // highlighted, it is the one in use
    const html = listHTML(list);
    const lead = html.slice(0, html.indexOf("</div>", html.indexOf("mp-lead")) + 6);
    expect(lead).toMatch(/^<div class="mp-row" role="presentation"><button type="button" class="mp-opt mp-lead"/);
    expect(lead).toContain("data-current");
    expect(lead).not.toContain('class="mp-star"');
    expect(lead).not.toContain("mp-price");
    expect(html).toContain("無法用來看圖片");
    expect(html.indexOf("無法用來看圖片")).toBeGreaterThan(html.indexOf("gemini-4-flash"));
  });

  test("the lead goes with a search that does not match it, and stays whatever the maker or the order", () => {
    const list = new PickerList("vision", [blind, seeing], "", {}, [], LEAD);
    list.setQuery("gemini");
    expect(list.rows.map((row) => row.model.id)).toEqual(["google/gemini-4-flash"]);
    list.setQuery("回答");
    expect(list.rows.map((row) => row.model.id)).toEqual([""]);
    list.setQuery("");
    list.setProvider("google");
    list.setSort("name");
    expect(list.rows.map((row) => row.model.id)).toEqual(["", "google/gemini-4-flash"]);
  });

  test("with favourites, the lead stays first and 我的最愛 comes after it", () => {
    const list = new PickerList("vision", [blind, seeing], "google/gemini-4-flash", {}, ["google/gemini-4-flash"], LEAD);
    expect(list.rows.map((row) => row.model.id)).toEqual(["", "google/gemini-4-flash", "openai/gpt-6-luna"]);
    const html = listHTML(list);
    expect(html.indexOf("mp-lead")).toBeLessThan(html.indexOf("我的最愛"));
  });

  test("the button names the lead when it is chosen, and a model otherwise", () => {
    const look = { role: "vision" as const, label: "看圖片", catalog: { ...CATALOG, llm: [seeing] }, lead: LEAD, showPrice: false };
    expect(modelPickerHTML({ ...look, current: "" })).toContain('<span class="mp-tname">與回答相同</span>');
    expect(modelPickerHTML({ ...look, current: "" })).toContain('aria-label="看圖片: 與回答相同"');
    expect(modelPickerHTML({ ...look, current: "google/gemini-4-flash" })).toContain('<span class="mp-tname">gemini-4-flash</span>');
  });
});
