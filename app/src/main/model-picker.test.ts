// The model picker's list without a page: what it shows of each model, the search, the orders, the
// maker, the rows a role cannot use, the keys and the chosen model. The page around it (the button
// and the popover) is ui.ts's, and is tried in the app.

import { afterEach, describe, expect, test } from "vitest";
import { setLocale } from "./i18n";
import {
  PickerList,
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
} from "./model-picker";
import type { CatalogModel, ModelCatalog } from "./types";

afterEach(() => setLocale("zh-TW"));

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
    capabilities: { structured_output: true, reasoning: true, reasoning_off: true, max_tokens: true, realtime: true },
    recommended: false,
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
    capabilities: { dictionary_hints: false, realtime: true },
    roles: { stt: { usable: true, reason: null } },
    ...over,
  };
}

const LUNA = llm("openai/gpt-6-luna", { name: "GPT-6 Luna", provider: "OpenAI", input_per_mtok: 0.1, output_per_mtok: 0.5, context_length: 1_050_000, recommended: true });
const HAIKU = llm("anthropic/claude-haiku-5.5", { name: "Claude Haiku 5.5", provider: "Anthropic", input_per_mtok: 0.1, output_per_mtok: 0.5, recommended: true, created_at_ms: OCT_18 + DAY });
const ALIAS = llm("~openai/gpt-luna-latest", { name: "GPT Luna Latest", provider: "OpenAI", input_per_mtok: 0.1, output_per_mtok: 0.5 });
const FREE = llm("nvidia/nemotron:free", { name: "Nemotron 3 Super (free)", provider: "NVIDIA", input_per_mtok: 0, output_per_mtok: 0, free: true, created_at_ms: null });
const ROUTER = llm("typesafe/jev-router", { name: "Jev Router", provider: "TypeSafe", input_per_mtok: null, output_per_mtok: null, variable_price: true });
const DEAR = llm("openai/gpt-10", { name: "GPT-10", provider: "OpenAI", input_per_mtok: 20, output_per_mtok: 80, created_at_ms: OCT_18 + 2 * DAY });
const OLD = llm("openai/gpt-5", { name: "GPT-5", provider: "OpenAI", input_per_mtok: 1.25, output_per_mtok: 10, created_at_ms: OCT_18 - 300 * DAY });
const UNKNOWN = llm("odd/model", { name: "Odd", provider: "Odd", input_per_mtok: null, output_per_mtok: null });
const FLASH = llm("google/gemini-3.8-flash", { name: "Gemini 3.8 Flash", provider: "Google", input_per_mtok: 0.825, output_per_mtok: 4.125, ...unusable("reasoning_required") });
const BATCH = llm("anthropic/claude-haiku-5.5:batch", { name: "Claude Haiku 5.5 (batch)", provider: "Anthropic", input_per_mtok: 0.05, output_per_mtok: 0.25, ...unusable("batch_only") });
const LLMS = [LUNA, HAIKU, ALIAS, FREE, ROUTER, DEAR, OLD, UNKNOWN, FLASH, BATCH];

const SCRIBE = stt("elevenlabs/scribe-v2", { name: "Scribe v2", provider: "ElevenLabs", per_minute: 0.00366667, recommended: true });
const WHISPER = stt("openai/whisper-large-v3-turbo", { name: "Whisper Large V3 Turbo", provider: "OpenAI", per_minute: 0.0001998, capabilities: { dictionary_hints: true, realtime: true } });
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
    expect(rows({ query: "anthropic" })).toEqual(["anthropic/claude-haiku-5.5", "anthropic/claude-haiku-5.5:batch"]);
    expect(rows({ query: "luna-latest" })).toEqual(["~openai/gpt-luna-latest"]);
  });

  test("needs every word, and looks past spaces and punctuation", () => {
    expect(rows({ query: "gpt6" })).toEqual(["openai/gpt-6-luna"]);
    expect(rows({ query: "haiku batch" })).toEqual(["anthropic/claude-haiku-5.5:batch"]);
    expect(rows({ query: "  claude   5.5 " })).toHaveLength(2);
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
      "anthropic/claude-haiku-5.5:batch",
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
    expect(rows({ provider: "Anthropic" })).toEqual(["anthropic/claude-haiku-5.5", "anthropic/claude-haiku-5.5:batch"]);
    expect(rows({ provider: "Anthropic", query: "batch" })).toEqual(["anthropic/claude-haiku-5.5:batch"]);
  });

  test("is chosen among the makers by name, each with its count, after all of them", () => {
    expect(providerChoices(LLMS)).toEqual([
      { value: "", label: "所有供應商", detail: "10" },
      { value: "Anthropic", label: "Anthropic", detail: "2" },
      { value: "Google", label: "Google", detail: "1" },
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
    const heading = html.indexOf('class="mp-group"');
    expect(heading).toBeGreaterThan(html.indexOf('data-id="odd/model"'));
    expect(html.slice(heading)).toContain("無法用來整理聽寫");
    expect(html).toMatch(/data-id="google\/gemini-3\.8-flash" aria-selected="false" aria-disabled="true"/);
    expect(html).toContain("一定會先推理，太慢");
    expect(html).toContain("只能批次處理，無法即時回覆");
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
    expect(list.chosen()).toBe("openai/gpt-5");
    expect(list.move("ArrowUp") && list.move("ArrowUp")).toBe(true);
    expect(list.chosen()).toBe("~openai/gpt-luna-latest");
    expect(list.move("x")).toBe(false);
  });

  test("a chosen model the list does not show opens the list on its first row", () => {
    const list = new PickerList("cleanup", LLMS, "gone/model");
    expect(list.activeId()).toBe("nvidia/nemotron:free");
  });

  test("a newer catalog keeps the search and the highlighted model", () => {
    const list = new PickerList("cleanup", LLMS, "openai/gpt-6-luna", { query: "gpt" });
    list.point("openai/gpt-10");
    list.setModels([...LLMS, llm("openai/gpt-11", { name: "GPT-11", provider: "OpenAI" })]);
    expect(list.state.query).toBe("gpt");
    expect(list.activeId()).toBe("openai/gpt-10");
    expect(ids(list.rows)).toContain("openai/gpt-11");
  });
});

describe("the chosen model", () => {
  test("is checked, and only it", () => {
    const html = listHTML(new PickerList("cleanup", LLMS, "anthropic/claude-haiku-5.5"));
    expect(html.match(/data-current/g)).toHaveLength(1);
    expect(html).toMatch(/data-id="anthropic\/claude-haiku-5\.5"[^>]*data-current/);
    const row = html.slice(html.indexOf('data-id="anthropic/claude-haiku-5.5"'));
    expect(row.slice(0, row.indexOf("</button>"))).toContain("<svg");
    expect(row).toContain("Claude Haiku 5.5, Anthropic, 輸入 $0.10 / 輸出 $0.50 每百萬 token, 推薦, 目前使用");
  });

  test("a row shows its maker, its context and its badge", () => {
    const html = listHTML(new PickerList("cleanup", [LUNA], "x/y"));
    expect(html).toContain("GPT-6 Luna");
    expect(html).toContain("OpenAI · 1.05M 上下文");
    expect(html).toContain('<span class="mp-badge">推薦</span>');
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

describe("a choice that is no model", () => {
  // The speech recognition of this computer, which the settings offer with the speech models.
  const LOCAL = { id: "local", name: "本機（離線，聲音不上傳）", price: "免費", detail: "聲音不離開這台電腦" };

  test("comes first, is checked when it is chosen, and can be chosen", () => {
    const list = new PickerList("stt", STTS, "local", {}, [LOCAL]);
    expect(ids(list.rows)[0]).toBe("local");
    expect([list.activeId(), list.chosen()]).toEqual(["local", "local"]);
    const html = listHTML(list);
    expect(html).toMatch(/data-id="local"[^>]*data-current/);
    expect(html).toContain('<span class="mp-price num">免費</span>');
    expect(html).toContain('<span class="mp-sub">聲音不離開這台電腦</span>');
    expect(html).toContain('aria-label="本機（離線，聲音不上傳）, 聲音不離開這台電腦, 免費, 目前使用"');
    expect(list.modelCount()).toBe(STTS.length);
  });

  test("is found by its name, and leaves a search it does not answer and a maker", () => {
    const list = new PickerList("stt", STTS, "elevenlabs/scribe-v2", {}, [LOCAL]);
    list.setQuery("本機");
    expect(ids(list.rows)).toEqual(["local"]);
    list.setQuery("whisper");
    expect(ids(list.rows)).toEqual(["openai/whisper-large-v3-turbo"]);
    list.setQuery("");
    expect(list.activeId()).toBe("elevenlabs/scribe-v2");
    list.setProvider("OpenAI");
    expect(ids(list.rows)).toEqual(["openai/whisper-large-v3-turbo"]);
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
