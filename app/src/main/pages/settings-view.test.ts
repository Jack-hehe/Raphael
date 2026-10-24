/// <reference types="node" />
import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, describe, expect, test } from "vitest";
import { emptyConnectionsView } from "../connections";
import { emptyMessagingView } from "../messaging";
import { emptyMemoryView } from "../memory-view";
import { emptyRulesView } from "../safety-rules";
import { emptyServicesView } from "../services";
import { emptyProvidersView } from "../providers";
import { setLocale, t } from "../i18n";
import type { LanguageGroup } from "../languages";
import type { BrainSettings, CatalogModel, DeviceSettings, LocalModel, ModelCatalog, SettingsPane, Status } from "../types";
import {
  APPEARANCES,
  PANES,
  canonicalPane,
  RETENTIONS,
  SEARCH_INDEX,
  computerApps,
  type SettingsView,
  loadingHTML,
  settingsMicrophones,
  modelPickerLook,
  paneHTML,
  paneListHTML,
  priceTexts,
  searchResultsHTML,
  searchSettings,
  WORKER_COST_CAPS,
} from "./settings-view";

afterEach(() => setLocale("zh-TW"));

test("Models has its own settings navigation entry and searchable provider page", () => {
  expect(paneListHTML()).toContain('data-pane="models"');
  expect(searchSettings("OpenAI").some((hit) => hit.pane === "models")).toBe(true);
  expect(tab("models", { providers: { ...emptyProvidersView(), state: "ready" } })).toContain("新增供應商");
});

const NOW = Date.UTC(2026, 9, 20, 6, 0);
const REALTEK = "麥克風排列 (Realtek(R) Audio)";

const device: DeviceSettings = {
  dictation_keys: ["AltRight"],
  dictation_mode: "toggle",
  paste_last_keys: ["AltRight+KeyV"],
  agent_keys: ["AltRight+Space"],
  microphone: null,
  emblem_size: 150,
  subtitles: "on",
  appearance: "system",
  ui_language: "zh-TW",
  autostart: false,
  onboarded: true,
  computer_apps: [],
};
const brain: BrainSettings = {
  monthly_limit_usd: 10,
  cleanup_model: "openai/gpt-6-luna",
  languages: ["zh-TW", "en-US"],
  history_retention_days: 30,
  stt_engine: "cloud",
  stt_cloud_model: "elevenlabs/scribe-v2",
  agent_model: "openai/gpt-6-luna",
  agent_city: "台北",
};
/** A model of the catalog that every role of its kind can use. */
const model = (id: string, name: string, provider: string, price: Partial<CatalogModel>, stt = false): CatalogModel => ({
  id, name, provider, input_per_mtok: null, output_per_mtok: null, per_minute: null, free: false, variable_price: false,
  context_length: stt ? null : 1_000_000, created_at_ms: NOW, capabilities: {},
  roles: stt ? { stt: { usable: true, reason: null } } : { cleanup: { usable: true, reason: null }, answer: { usable: true, reason: null } },
  ...price,
});
const catalog: ModelCatalog = {
  llm: [
    model("openai/gpt-6-luna", "GPT-6 Luna", "OpenAI", { input_per_mtok: 0.1, output_per_mtok: 0.4 }),
    model("anthropic/claude-haiku-5.5", "Claude Haiku 5.5", "Anthropic", { input_per_mtok: 1, output_per_mtok: 5 }),
  ],
  stt: [model("elevenlabs/scribe-v2", "Scribe v2", "ElevenLabs", { per_minute: 0.0067 }, true)],
  fetched_at_ms: NOW,
  source: "live",
  replaced: [],
};
const openrouter: Status["openrouter"] = {
  state: "ok", latency_ms: 120, checked_at_ms: NOW, error: null, key: null,
  usage: { daily: 0.5, weekly: 1, monthly: 3.21, total: 9, limit: null, limit_remaining: null },
};
const spend: Status["spend"] = { month_usd: 1.2, today_usd: 0.1, limit_usd: 10, forecast_usd: 2.5, daily: [] };
const GROUPS: LanguageGroup[] = [
  { id: "zh", name: "中文", variants: [{ id: "zh-TW", name: "繁體中文（台灣）" }, { id: "zh-HK", name: "繁體中文（香港）" }] },
  { id: "en", name: "English", variants: [{ id: "en-US", name: "English (United States)" }, { id: "en-GB", name: "English (United Kingdom)" }] },
  { id: "ja", name: "日本語", variants: [{ id: "ja-JP", name: "日本語" }] },
];

const LOCAL_MODELS: LocalModel[] = [
  { name: "qwen3-asr", title: "Qwen3-ASR", languages: ["zh", "en"], note: "dictionary", size: 878_702_423, ready: true },
  { name: "sensevoice", title: "SenseVoice", languages: ["zh", "en"], note: "fast", size: 163_002_883, ready: true },
];
const view = (over: Partial<SettingsView> = {}): SettingsView => ({
  pane: "general", device, brain, hasKey: true, openrouter, spend, catalog, checkingOpenrouter: false,
  devices: { devices: [{ index: 0, name: REALTEK, is_default: true }], selected: null, automatic_device: REALTEK },
  altgr: false, version: "0.1.0", replacingKey: false, confirmingKeyRemoval: false, keyBusy: false, keyError: null, limitDraft: null, limitError: null,
  cityDraft: null, cityError: null, languageGroups: GROUPS, now: NOW, connections: emptyConnectionsView(), messaging: emptyMessagingView(),
  confirmingClear: null, localModels: LOCAL_MODELS, localDownload: null, ...over,
});

test("every skin offers a system, light or dark override", () => {
  expect(paneHTML(view({ pane: "general" }))).not.toContain('data-k="appearance"');
  for (const pane of ["appearance"] as const) {
    expect(paneHTML(view({ pane, brain: { ...brain, skin: "default" } }))).toContain('data-k="appearance"');
    for (const skin of ["paper", "graphite", "lagoon", "dusk", "midnight", "meadow", "ink", "catppuccin"]) {
      expect(paneHTML(view({ pane, brain: { ...brain, skin } }))).toContain('data-k="appearance"');
    }
  }
});
test("Telegram is in the 訊息平台 tab, and only there", () => {
  expect(tab("messaging")).toContain("data-msg-section");
  for (const id of ["general", "dictation", "agent", "connections", "usage", "data", "about"] as const) expect(tab(id)).not.toContain("data-msg");
});

test("the memory is in the 拉斐爾 tab, after its settings, and only there", () => {
  const memory = { ...emptyMemoryView(), status: { memories: [], summary: null } };
  const agent = tab("agent", { memory });
  expect(agent).toContain("data-mem-section");
  expect(agent.indexOf("data-mem-section")).toBeGreaterThan(agent.indexOf('data-k="worker-cap"'));
  for (const id of ["general", "dictation", "connections", "messaging", "usage", "data", "about"] as const) expect(tab(id, { memory })).not.toContain("data-mem");
});

test("the safety rules are in the 拉斐爾 tab, after the memory and before 電腦操作, with no switch", () => {
  const memory = { ...emptyMemoryView(), status: { memories: [], summary: null } };
  const rules = { ...emptyRulesView(), status: [{ id: 1, action: "付款結帳", verdict: "ask" as const, position: 1 }] };
  const agent = tab("agent", { memory, rules });
  expect(agent).toContain("<h2>安全規則</h2>");
  expect(agent.indexOf("data-rules-section")).toBeGreaterThan(agent.indexOf("data-mem-section"));
  const computer = agent.indexOf("<h2>電腦操作</h2>");
  expect(agent.indexOf("data-rules-section")).toBeLessThan(computer);
  expect(agent.slice(agent.indexOf("data-rules-section"), computer)).not.toContain('role="switch"');
  for (const id of ["general", "dictation", "connections", "messaging", "usage", "data", "about"] as const) expect(tab(id, { rules })).not.toContain("data-rules");
});

test("the memory is two sections: 共同記憶 first, then Raphael's own, each with its notes' move", () => {
  const notes = { memories: [{ name: "user-city", description: "主人住台北", type: "user", origin: "told", updated: "", expires: null }], summary: null };
  const memoryShared = { ...emptyMemoryView(), status: notes, expanded: true, space: "shared" as const, title: "共同記憶", moveTo: "移到 拉斐爾 的記憶" };
  const memory = { ...emptyMemoryView(), status: notes, expanded: true, space: "own" as const, title: "拉斐爾 的記憶", moveTo: "移到共同記憶" };
  const agent = tab("agent", { memory, memoryShared });
  const shared = agent.indexOf('data-mem-space="shared"');
  const own = agent.indexOf('data-mem-space="own"');
  expect(shared).toBeGreaterThan(0);
  expect(own).toBeGreaterThan(shared);
  expect(agent).toContain('aria-label="共同記憶"');
  expect(agent).toContain('aria-label="拉斐爾 的記憶"');
  // each note has its menu, a borderless more; the two lists' open notes have fields of their own
  expect(agent.match(/data-mem="more"/g)).toHaveLength(2);
  expect(agent).toContain('class="btn icon" aria-label="「主人住台北」的選項"');
});

test("服務 is in the 拉斐爾 tab, after its settings and before the memory, as a clean list of five rows", () => {
  const memory = { ...emptyMemoryView(), status: { memories: [], summary: null } };
  const agent = tab("agent", { memory, services: emptyServicesView() });
  expect(agent).toContain("<h2>服務</h2>");
  expect(agent.indexOf("data-svc-section")).toBeGreaterThan(agent.indexOf('data-k="worker-cap"'));
  expect(agent.indexOf("data-svc-section")).toBeLessThan(agent.indexOf("data-mem-section"));
  expect(agent.match(/data-k="svc-open:/g)).toHaveLength(5);
  // nothing to choose or type at the tab's level: the picker of 看圖片 is in its dialog, not here
  expect(agent).not.toContain("data-model-picker");
  expect(agent).not.toContain('data-k="svc-search"');
  // 看圖片 is the model picker, of the models that read images, in the dialog's page for 「指定模型」
  const picker = tab("agent", { services: { ...emptyServicesView(), open: { kind: "vision", service: "model" } }, brain: { ...brain, vision_model: "anthropic/claude-haiku-5.5" } });
  expect(picker).toMatch(/<span class="mp-slot" data-model-picker="vision"><button type="button" class="sel mp-trigger"[^>]*data-k="svc-vision">/);
  expect(picker).toContain('<strong class="mp-tname">Claude Haiku 5.5</strong>');
  expect(tab("agent", { services: { ...emptyServicesView(), open: { kind: "vision", service: "model" } } })).toContain('<strong class="mp-tname">與回答相同</strong>');
  for (const id of ["general", "dictation", "connections", "messaging", "usage", "data", "about"] as const) {
    expect(tab(id, { services: emptyServicesView() })).not.toContain("data-svc");
  }
});

const tab = (pane: SettingsPane, over: Partial<SettingsView> = {}): string => paneHTML(view({ pane, ...over }));

test.each([catalog, "loading", "failed"] as const)("a CLI answer is priced as subscription regardless of API catalog availability", (source) => {
  expect(priceTexts(view({ catalog: source, brain: { ...brain, agent_model: "cli:codex" } }))["agent-model"]).toBe("訂閱");
});
const IDS = PANES.map((p) => p.id);
const LOCALES = ["zh-TW", "en", "ja"] as const;

/** The opening tag of every button. */
const buttons = (html: string): string[] => html.match(/<button\b[^>]*>/g) ?? [];
/** The names of the groups, and of the rows, in order. */
const groups = (html: string): string[] => [...html.matchAll(/<h2>(.*?)<\/h2>/g)].map((m) => m[1]);
const names = (html: string): string[] => [...html.matchAll(/<div class="t">(.*?)<\/div>/g)].map((m) => m[1]);
/** The row called `name`, from its start to the start of the next one. */
function rowOf(html: string, name: string): string {
  const rows = html.split(/(?=<div class="row[ "])/);
  const found = rows.find((r) => r.includes(`<div class="t">${name}</div>`));
  if (!found) throw new Error(`no row called ${name}`);
  return found;
}
/** The rows that have a line under their name. */
const described = (html: string): string[] =>
  html.split(/(?=<div class="row[ "])/).filter((r) => r.includes('<div class="d">')).map((r) => /<div class="t">(.*?)<\/div>/.exec(r)![1]);

describe("the tabs", () => {
  test("are General, Dictation, Raphael; Connections, Messaging; Models & usage, Data and About, in each language", () => {
    expect(IDS).toEqual(["general", "appearance", "dictation", "agent", "connections", "messaging", "models", "data", "about"]);
    const titles = {
      "zh-TW": ["一般", "外觀", "聽寫", "拉斐爾", "連接", "訊息平台", "模型與用量", "資料", "關於"],
      en: ["General", "Appearance", "Dictation", "Raphael", "Connections", "Messaging", "Models & usage", "Data", "About"],
      ja: ["一般", "外観", "音声入力", "ラファエル", "接続", "メッセージ", "モデルと使用量", "データ", "アプリ情報"],
    };
    for (const locale of LOCALES) {
      setLocale(locale);
      expect(PANES.map((p) => p.title())).toEqual(titles[locale]);
    }
  });

  test("sit in three groups: the basics with no name, then 整合 and 帳戶 in small grey words", () => {
    const list = paneListHTML();
    const order = [...list.matchAll(/class="nav-group">([^<]+)<|data-pane="(\w+)"/g)].map((m) => m[1] ?? m[2]);
    expect(order).toEqual(["general", "appearance", "dictation", "agent", "整合", "connections", "messaging", "帳戶", "models", "data", "about"]);
    setLocale("en");
    expect(paneListHTML()).toContain('<div class="nav-group">Integrations</div>');
    expect(paneListHTML()).toContain('<div class="nav-group">Account</div>');
    setLocale("ja");
    expect(paneListHTML()).toContain('<div class="nav-group">連携</div>');
  });

  test("every tab is a real button; 拉斐爾's has Raphael's emblem, in a version for each theme", () => {
    const list = paneListHTML();
    expect(list.match(/<button type="button" class="nav" data-pane="\w+" aria-current="false">/g)).toHaveLength(9);
    const agent = list.slice(list.indexOf('data-pane="agent"'), list.indexOf('data-pane="connections"'));
    expect(agent).toContain('src="/brand/app-emblem-light-64.webp"');
    expect(agent).toContain('src="/brand/app-emblem-dark-64.webp"');
    expect(agent).not.toContain("<svg");
  });

  test("each opens with its name and one short line on what it is for", () => {
    for (const locale of LOCALES) {
      setLocale(locale);
      for (const id of IDS) {
        const html = tab(id);
        const title = PANES.find((p) => p.id === id)!.title();
        const head = `<h1 id="set-title" tabindex="-1">${title.replace("&", "&amp;")}</h1>`;
        // 連接 has its 「新增」 on the line of its name
        if (id === "connections") expect(html).toMatch(new RegExp(`^<div class="set-head">${head}<button[^>]*data-k="conn-add"[^>]*>.*?</button></div><p class="purpose">`));
        else expect(html.startsWith(`${head}<p class="purpose">`)).toBe(true);
        expect(html.match(/class="purpose"/g)).toHaveLength(1);
        const purpose = /<p class="purpose">(.*?)<\/p>/.exec(html)![1];
        expect(purpose.length).toBeGreaterThan(3);
        expect(purpose.length).toBeLessThan(60);
      }
    }
  });

  test("say in a line what they are for", () => {
    const purpose = (id: SettingsPane, over: Partial<SettingsView> = {}): string => /<p class="purpose">(.*?)<\/p>/.exec(tab(id, over))![1];
    expect(purpose("general")).toBe("個人資料、介面語言與桌面設定");
    expect(purpose("dictation")).toBe("按快捷鍵說話，拉斐爾把你說的打出來");
    expect(purpose("agent")).toBe("按 右 Alt + Space 問拉斐爾");
    expect(purpose("usage")).toBe(t("providers.about"));
  });

  test("keep the name and the purpose while the settings are being read", () => {
    const html = loadingHTML(view({ pane: "usage" }));
    expect(html).toContain('<h1 id="set-title" tabindex="-1">模型與用量</h1>');
    expect(html).toContain('<p class="purpose">');
    expect(html).toContain("settings-skeleton");
  });
});

describe("the rules for every tab, in every language", () => {
  test("an action is a button with a shape and a choice is a dropdown or a switch: nothing else can be clicked", () => {
    for (const locale of LOCALES) {
      setLocale(locale);
      for (const id of IDS) {
        for (const over of [{}, { hasKey: false }, { brain: null }, { altgr: true }]) {
          const html = tab(id, over);
          for (const tag of buttons(html)) expect(tag).toMatch(/class="(btn[ "]|sel[ "]|tg|tab"|ca-item[ "])/);
          expect(html).not.toMatch(/quiet|class="link"|<a\b/);
        }
      }
    }
  });

  test("no tab shows the options of a setting in a row: no segmented controls, radio rows or chips", () => {
    for (const id of IDS) {
      const html = tab(id);
      if (id === "appearance") {
        expect(html.match(/data-skin-choice=/g)).toHaveLength(9);
        expect(html.match(/aria-pressed="true"/g)).toHaveLength(1);
        continue;
      }
      expect(html).not.toMatch(/role="radio|aria-pressed|class="tabs"|class="chip|lchip/);
    }
  });

  test("a line under a row's name only where the setting would otherwise be set wrong", () => {
    expect(described(tab("general"))).toEqual(["字幕"]);
    expect(described(tab("dictation"))).toEqual(["你說的語言"]);
    // the key's status, the month's forecast, what the limit does, and what each model costs
    expect(described(tab("models"))).toEqual(["拉斐爾的回答", "整理文字", "語音轉文字"]);
    expect(described(tab("usage"))).toEqual(["金鑰"]);
  });
});

describe("一般", () => {
  test("has the interface language, the theme and the emblem's size as dropdowns, the subtitles and the start as switches", () => {
    const html = tab("general");
    expect(groups(html)).toEqual(["個人資料", "介面與桌面", "啟動"]);
    expect(names(html)).toEqual(["介面語言", "桌面圖案大小", "字幕", "開機時自動啟動", "重新導覽"]);
    for (const k of ["ui-language", "size"]) expect(html).toMatch(new RegExp(`<button type="button" class="sel"[^>]*data-k="${k}"`));
    for (const k of ["subtitles", "autostart"]) expect(html).toMatch(new RegExp(`class="tg" role="switch"[^>]*data-k="${k}"`));
  });

  test("starts the guide again with a real bordered button, not a switch or a link", () => {
    const html = tab("general");
    expect(html).toMatch(/<button type="button" class="btn"[^>]*data-act="rerun-guide"/);
    expect(html.match(/data-act="rerun-guide"/g)).toHaveLength(1);
  });

  test("offers light, dark, or as Windows is, in that order", () => {
    expect(APPEARANCES.map((c) => c.label)).toEqual(["淺色", "深色", "跟隨 Windows"]);
    setLocale("en");
    expect(APPEARANCES.map((c) => c.label)).toEqual(["Light", "Dark", "Follow Windows"]);
  });
});

describe("聽寫", () => {
  test("is how a dictation starts, then how Raphael listens", () => {
    const html = tab("dictation");
    expect(groups(html)).toEqual(["開始方式", "聆聽方式"]);
    expect(names(html)).toEqual(["聽寫", "貼上上一次", "取消", "聽寫方式", "你說的語言", "麥克風"]);
  });

  test("shows each shortcut as key caps, with a 變更 button on the one that can be changed here", () => {
    const html = tab("dictation");
    const dictate = rowOf(html, "聽寫");
    expect(dictate).toContain('data-row="hotkey"');
    expect(dictate).toContain("<kbd>右 Alt</kbd>");
    expect(dictate).toContain('<button type="button" class="btn" data-act="rec" data-k="rec">變更</button>');
    expect(rowOf(html, "貼上上一次")).toContain("<kbd>V</kbd>");
    expect(rowOf(html, "取消")).toContain("<kbd>Esc</kbd>");
    for (const name of ["貼上上一次", "取消"]) expect(buttons(rowOf(html, name))).toEqual([]);
    expect(rowOf(html, "聽寫方式")).toMatch(/class="sel"[^>]*data-k="mode"/);
  });

  test("warns about Right Alt only when the keyboard uses it as AltGr", () => {
    expect(tab("dictation", { altgr: true })).toContain("AltGr");
    expect(tab("dictation")).not.toContain("AltGr");
    expect(tab("dictation", { altgr: true, device: { ...device, dictation_keys: ["F9"] } })).not.toContain("AltGr");
  });

  test("has one row for the languages, a dropdown that opens the picker of languages and their variants", () => {
    const row = rowOf(tab("dictation"), "你說的語言");
    expect(buttons(row)).toHaveLength(1);
    expect(row).toContain('class="sel lang-sel" aria-haspopup="dialog"');
    expect(row).toContain("<span>繁體中文（台灣）、English (United States)</span>");
  });

  test("shows the microphone by a short name, with the whole name in the list and the tooltip, and a meter under it", () => {
    const row = rowOf(tab("dictation"), "麥克風");
    expect(row).toContain('<span class="mic-ctl"><button type="button" class="sel"');
    expect(row).toContain("<span>自動（麥克風排列）</span>");
    expect(row).toContain(`title="自動偵測（目前：${REALTEK}）"`);
    // the meter of small bars, under the dropdown and inside its box, which is as wide as the dropdown
    expect(row).toContain(`<span class="meter" aria-hidden="true">${"<i></i>".repeat(46)}</span></span>`);
    expect(buttons(row)).toHaveLength(1); // the search for microphones is an item of its menu
    const choices = settingsMicrophones(view());
    expect(choices.map((c) => c.label)).toEqual(["自動偵測（目前：麥克風排列）", REALTEK]);
    expect(choices.map((c) => c.short)).toEqual(["自動（麥克風排列）", "麥克風排列"]);
    expect(choices[0].title).toBe(`自動偵測（目前：${REALTEK}）`);
  });

  test("fits a long microphone name to the dropdown and the menu, the whole name in the tooltip", () => {
    const cut = "麥克風 (5- HyperX Cloud II Core Wi"; // as MME gives it: 31 characters at most
    const devices = { devices: [{ index: 0, name: cut, is_default: true }, { index: 1, name: REALTEK, is_default: false }], selected: null, automatic_device: cut };
    const row = rowOf(tab("dictation", { devices }), "麥克風");
    expect(row).toContain("<span>自動（HyperX Cloud II…）</span>");
    expect(row).toContain(`title="自動偵測（目前：${cut}）"`);
    const choices = settingsMicrophones(view({ devices }));
    expect(choices.map((c) => c.label)).toEqual(["自動偵測（目前：HyperX Cloud II Core Wi…）", cut, REALTEK]);
    expect(choices.map((c) => c.short)).toEqual(["自動（HyperX Cloud II…）", "HyperX Cloud II Core Wi…", "麥克風排列"]);
    const chosen = rowOf(tab("dictation", { devices, device: { ...device, microphone: cut } }), "麥克風");
    expect(chosen).toContain("<span>HyperX Cloud II Core Wi…</span>");
    expect(chosen).toContain(`title="${cut}"`);
  });

  test("keeps a chosen microphone that is not plugged in, said so", () => {
    const choices = settingsMicrophones(view({ device: { ...device, microphone: "USB Mic (C-Media)" } }));
    expect(choices[choices.length - 1]).toEqual({ value: "USB Mic (C-Media)", label: "USB Mic (C-Media)（未連接）", short: "USB Mic（未連接）" });
  });

  test("has no switch for pausing the media or for learning: both are always on", () => {
    const html = tab("dictation");
    expect(html).not.toContain('class="tg"');
    expect(html).not.toMatch(/pause-media|data-k="learn"|字典/);
  });
});

describe("拉斐爾", () => {
  test("has his picture, the key that asks him and the weather city, with no line of explanation, and no choice of router", () => {
    for (const locale of LOCALES) {
      setLocale(locale);
      const html = tab("agent");
      expect(html).not.toMatch(/agent-router|Jev|Luna/);
      expect(html).not.toContain('data-k="agent-model"'); // with the other models
    }
    setLocale("zh-TW");
    const html = tab("agent");
    expect(names(html).slice(0, 2)).toEqual(["問拉斐爾", "天氣的城市"]);
    expect(described(html)).toEqual([]);
    // Raphael's picture is changed in his details panel on the 拉斐爾 page, no longer here
    expect(names(html)).not.toContain("頭像");
    expect(html).not.toMatch(/avatar|app-emblem/);
    expect(rowOf(html, "問拉斐爾")).toContain('<button type="button" class="btn" data-act="rec-agent" data-k="rec-agent">變更</button>');
    expect(rowOf(html, "天氣的城市")).toContain('id="agent-city" type="text" value="台北"');
  });

  test("hides the retired background cost cap", () => {
    const html = tab("agent");
    expect(html).not.toContain('data-k="worker-cap"');
    expect(tab("agent", { brain: { ...brain, worker_cost_cap_usd: 0.5 } })).not.toContain("US$0.50");
    expect(WORKER_COST_CAPS.map((c) => c.value)).toEqual(["0.1", "0.2", "0.5", "1"]);
  });

  test("has no Telegram: it is in 訊息平台", () => {
    expect(tab("agent")).not.toContain("Telegram");
  });

  test("still says how to ask her when no key is set", () => {
    const html = tab("agent", { device: { ...device, agent_keys: [] } });
    expect(html).toContain('<p class="purpose">設定快捷鍵後，按下就能問拉斐爾</p>');
    expect(rowOf(html, "問拉斐爾")).toContain("尚未設定");
  });

  test("warns about Right Alt under her key only when the keyboard uses it as AltGr", () => {
    const html = tab("agent", { altgr: true });
    expect(html).toContain("AltGr");
    expect(html.indexOf("AltGr")).toBeGreaterThan(html.indexOf('data-row="agent-hotkey"'));
    expect(tab("agent")).not.toContain("AltGr");
    expect(tab("agent", { altgr: true, device: { ...device, agent_keys: ["ControlLeft+Space"] } })).not.toContain("AltGr");
  });

  test("an empty city is said to be wrong", () => {
    const html = tab("agent", { cityDraft: " ", cityError: () => "請輸入城市名稱" });
    expect(html).toContain('role="alert"');
    expect(html).toContain('aria-invalid="true"');
  });
});

describe("Models", () => {
  test("puts providers and model choices on Models, existing usage controls and analytics slot on Usage", () => {
    const html = tab("models");
    expect(groups(html)).toEqual(["使用的模型", "訂閱帳號", "供應商"]);
    expect(names(html)).toEqual(["拉斐爾的回答", "整理文字", "語音轉文字"]);
    expect(html.indexOf('data-pv="directory"')).toBeGreaterThanOrEqual(0);
    expect(html.indexOf('data-pv="directory"')).toBeGreaterThan(html.indexOf('data-row="stt-model"'));
    expect(html).not.toContain('data-row="raphael-spend"');
    expect(html).not.toContain("data-usage-host");
    expect(html).not.toContain("data-fallback");
    const usage = tab("usage");
    expect(names(usage)).toEqual(["金鑰", "Raphael 本月花費", "OpenRouter 帳號本月用量"]);
    expect(usage).toContain("data-usage-host");
    expect(usage).not.toContain("data-model-picker");
    expect(usage).not.toContain("<h2>用量</h2>");
    expect(usage).not.toContain('data-pv="directory"');
  });

  test("shows how OpenRouter took the key, with buttons to check again and to change it", () => {
    const key = rowOf(tab("usage"), "金鑰");
    expect(key).toContain("已連線");
    expect(key).toContain("延遲 120 ms");
    expect(buttons(key)).toEqual(['<button type="button" class="btn danger" data-act="key-remove">', '<button type="button" class="btn" data-act="or-check">', '<button type="button" class="btn" data-act="key-replace" data-k="key">']);
    expect(key).toContain(">變更</button>");
    const bad = rowOf(tab("usage", { openrouter: { ...openrouter, state: "error", error: "auth" } }), "金鑰");
    expect(bad).toContain("金鑰無效，請更換");
  });

  test("has a bordered button to remove the key, only while a key is set, that asks once more", () => {
    const key = rowOf(tab("usage"), "金鑰");
    expect(buttons(key)).toContain('<button type="button" class="btn danger" data-act="key-remove">');
    expect(key).toContain(">移除金鑰</button>");
    expect(rowOf(tab("usage", { confirmingKeyRemoval: true }), "金鑰")).toContain(">確定移除？</button>");
    for (const over of [{ hasKey: false }, { replacingKey: true }]) {
      expect(rowOf(tab("usage", over), "金鑰")).not.toContain("key-remove");
    }
  });

  test("asks for a key when there is none, or while it is being changed", () => {
    for (const over of [{ hasKey: false }, { replacingKey: true }]) {
      const key = rowOf(tab("usage", over), "金鑰");
      expect(key).toContain('id="key-input" type="password"');
      expect(key).toContain('class="btn pri" data-act="key-paste"');
      expect(key).not.toContain('data-act="key-replace"');
    }
    expect(rowOf(tab("usage", { replacingKey: true }), "金鑰")).toContain('data-act="key-cancel"');
    const replacing = rowOf(tab("usage", { replacingKey: true }), "金鑰");
    expect(replacing.indexOf('data-act="key-cancel"')).toBeLessThan(replacing.indexOf('data-act="key-paste"'));
  });

  test("puts the price of what is chosen under each model's name, its picker beside, one self-contained row each", () => {
    const html = tab("models");
    for (const [name, k, role, price, chosen] of [
      ["語音轉文字", "stt-model", "stt", "每分鐘 $0.0067", "Scribe v2"],
      ["整理文字", "model", "cleanup", "輸入 $0.10 / 輸出 $0.40 每百萬 token", "GPT-6 Luna"],
      ["拉斐爾的回答", "agent-model", "answer", "輸入 $0.10 / 輸出 $0.40 每百萬 token", "GPT-6 Luna"],
    ]) {
      const row = rowOf(html, name);
      expect(row).toContain(`data-row="${k}"`);
      expect(row).toContain(
        `<span class="price num" data-price="${k}">${price}</span></div></div>` +
          `<div class="ctl"><span class="mp-slot" data-model-picker="${role}"><button type="button" class="sel mp-trigger" aria-haspopup="dialog"`,
      );
      // The button names what is chosen and leaves its price to the line under the row's name.
      expect(row).toMatch(new RegExp(`data-k="${k}">.*<strong class="mp-tname">${chosen}</strong>.*<svg`));
      expect(row).not.toContain("mp-tprice");
      expect(buttons(row).filter((tag) => tag.includes('class="sel mp-trigger"'))).toHaveLength(1);
      expect(row).toContain('class="models-purpose-note"');
      expect(row).not.toContain('data-fallback');
    }
    expect(priceTexts(view()).note).toBe("價格來自 OpenRouter，今天更新。實際花費依 OpenRouter 選用的供應商而定。");
  });

  test("gives each picker the catalog, what is chosen and the row's name", () => {
    const look = modelPickerLook(view(), "answer");
    expect(look).toMatchObject({ role: "answer", key: "agent-model", label: "拉斐爾的回答", current: "openai/gpt-6-luna", catalog, loading: false, showPrice: false, disabled: false });
    expect(modelPickerLook(view({ brain: { ...brain, stt_cloud_model: "openai/gpt-transcribe" } }), "stt").current).toBe("openai/gpt-transcribe");
  });

  test("gives the speech-to-text picker the local models, and the others none", () => {
    expect(modelPickerLook(view(), "stt").local).toEqual({ models: LOCAL_MODELS, active: null, fallback: null, download: null });
    expect(modelPickerLook(view(), "cleanup").local).toBeUndefined();
    expect(modelPickerLook(view(), "answer").local).toBeUndefined();
  });

  test("the local model is in use when the engine is local, and is qwen3-asr until another is chosen", () => {
    const local = { ...brain, stt_engine: "local" };
    expect(modelPickerLook(view({ brain: local }), "stt").local?.active).toBe("qwen3-asr");
    expect(modelPickerLook(view({ brain: { ...local, stt_local_model: "sensevoice" } }), "stt").local?.active).toBe("sensevoice");
    // With the cloud in use, the local model chosen is only what the fallback is.
    expect(modelPickerLook(view({ brain: { ...brain, stt_local_model: "sensevoice" } }), "stt").local?.active).toBeNull();
    expect(modelPickerLook(view({ brain: { ...brain, stt_local_model: "sensevoice" } }), "stt").local?.fallback).toBe("sensevoice");
    expect(modelPickerLook(view({ brain: { ...brain, stt_local_model: null } }), "stt").local?.fallback).toBeNull();
    expect(modelPickerLook(view({ brain: { ...local, stt_local_model: "sensevoice" } }), "stt").local?.fallback).toBeNull();
  });

  test("a download under way, and the catalogue while it is read, reach the picker", () => {
    const download = { name: "sensevoice", done: 5, total: 10 };
    expect(modelPickerLook(view({ localDownload: download }), "stt").local?.download).toEqual(download);
    expect(modelPickerLook(view({ localModels: "loading" }), "stt").local?.models).toBe("loading");
  });

  test("the cloud model stays the picker's choice, also while the local engine is in use", () => {
    const local = view({ brain: { ...brain, stt_engine: "local", stt_local_model: "sensevoice" } });
    expect(modelPickerLook(local, "stt").current).toBe("elevenlabs/scribe-v2");
    const row = rowOf(tab("models", { brain: { ...brain, stt_engine: "local", stt_local_model: "sensevoice" } }), "語音轉文字");
    expect(row).toContain('<strong class="mp-tname">SenseVoice</strong><span class="mp-source">本機</span>');
    expect(row).toContain('data-price="stt-model">免費</span>');
  });

  test("gives each picker the favourites of its own role, none when the core has not said", () => {
    const starred = { ...brain, model_favorites: { cleanup: ["a/b"], answer: ["c/d", "e/f"] } };
    expect(modelPickerLook(view({ brain: starred }), "cleanup").favorites).toEqual(["a/b"]);
    expect(modelPickerLook(view({ brain: starred }), "answer").favorites).toEqual(["c/d", "e/f"]);
    expect(modelPickerLook(view({ brain: starred }), "stt").favorites).toEqual([]);
    expect(modelPickerLook(view(), "cleanup").favorites).toEqual([]);
    expect(modelPickerLook(view({ brain: null }), "cleanup").favorites).toEqual([]);
  });

  test("says the prices are coming, and shows none it could not get", () => {
    expect(rowOf(tab("models", { catalog: "loading" }), "整理文字")).toContain(">價格載入中…</span>");
    expect(modelPickerLook(view({ catalog: "loading" }), "cleanup")).toMatchObject({ catalog: null, loading: true });
    expect(modelPickerLook(view({ catalog: "failed" }), "cleanup")).toMatchObject({ catalog: null, loading: false });
    expect(priceTexts(view({ catalog: "failed" }))).toEqual({ model: "", "agent-model": "", "stt-model": "", note: "目前無法取得模型價格。" });
  });

  test("shows a saved model the catalog does not have by its id, with an unknown price", () => {
    const row = rowOf(tab("models", { brain: { ...brain, cleanup_model: "x/old" } }), "整理文字");
    expect(row).toContain('<strong class="mp-tname">old</strong>');
    expect(row).toContain('data-price="model">價格未知</span>');
  });
});

describe("訊息平台", () => {
  test("is Telegram, in rows like the others", () => {
    expect(tab("messaging")).toMatch(/<p class="purpose">在手機上傳訊息給拉斐爾<\/p><section class="sgroup msg" data-msg-section aria-label="Telegram"><div class="slist"><div class="row msg-head">/);
  });
});

describe("資料", () => {
  test("is the dictation history (how long it is kept, and a clear), then the conversation with 拉斐爾 (a clear)", () => {
    const html = tab("data");
    expect(groups(html)).toEqual(["聽寫紀錄", "拉斐爾的對話"]);
    expect(names(html)).toEqual(["保留聽寫紀錄", "清除聽寫紀錄", "清除拉斐爾的對話"]);
    expect(described(html)).toEqual([]);
    expect(html).toContain("歷史紀錄只存在這台電腦上");
  });

  test("keeps the dictation history for a choice of days, a dropdown that shows the one in force", () => {
    const row = rowOf(tab("data"), "保留聽寫紀錄");
    expect(row).toMatch(/<button type="button" class="sel"[^>]*aria-label="保留聽寫紀錄" title="30 天" data-k="retention">/);
    expect(RETENTIONS.map((c) => c.value)).toEqual(["1", "7", "30", "365", "36500"]);
    expect(rowOf(tab("data", { brain: { ...brain, history_retention_days: 36_500 } }), "保留聽寫紀錄")).toContain("一直保留");
    // a retention set elsewhere that is none of the choices is still shown
    expect(rowOf(tab("data", { brain: { ...brain, history_retention_days: 90 } }), "保留聽寫紀錄")).toContain("90 天");
  });

  test("clears with a bordered button that asks once more, in red", () => {
    const html = tab("data");
    expect(rowOf(html, "清除聽寫紀錄")).toContain('<button type="button" class="btn danger" data-act="clear-dictation" data-k="clear-dictation">清除</button>');
    expect(rowOf(html, "清除拉斐爾的對話")).toContain('<button type="button" class="btn danger" data-act="clear-conversation" data-k="clear-conversation">清除</button>');
    const asking = tab("data", { confirmingClear: "dictation" });
    expect(rowOf(asking, "清除聽寫紀錄")).toContain('class="btn danger" data-act="clear-dictation" data-k="clear-dictation">確定清除？<');
    expect(rowOf(asking, "清除拉斐爾的對話")).toContain(">清除<");
  });

  test("has no export: the app cannot save a file where the user chooses yet", () => {
    expect(tab("data")).not.toMatch(/匯出|export/i);
  });
});

describe("搜尋設定", () => {
  const where = (query: string): string[] => searchSettings(query).map((hit) => `${hit.pane}:${hit.text}`);

  test("finds a row by its name, in the window's language, and says where it is", () => {
    expect(where("麥克風")).toEqual(["dictation:麥克風"]);
    expect(where("天氣")).toEqual(["agent:天氣的城市"]);
    expect(where("版本")).toEqual(["about:版本"]);
  });

  test("finds a row by its name in the other languages too, and shows it in the window's", () => {
    expect(where("microphone")).toEqual(["dictation:麥克風"]);
    expect(where("マイク")).toEqual(["dictation:麥克風"]);
    setLocale("en");
    expect(where("麥克風")).toEqual(["dictation:Microphone"]);
  });

  test("finds the shortcuts by the word for them, though no row is called that", () => {
    const shortcuts = ["dictation:聽寫", "dictation:貼上上一次", "agent:問拉斐爾"];
    expect(where("快捷鍵")).toEqual(shortcuts);
    expect(where("hotkey")).toEqual(shortcuts);
    expect(where("ショートカット")).toEqual(shortcuts);
  });

  test("no longer finds Raphael's picture: it is changed in his details panel on the 拉斐爾 page", () => {
    for (const locale of LOCALES) {
      setLocale(locale);
      for (const query of ["頭像", "大頭貼", "avatar", "Picture", "アイコン"]) expect(where(query), query).toEqual([]);
    }
  });

  test("finds Raphael's memory by its name and by what one asks him to do, in any of the languages", () => {
    for (const query of ["記憶", "記住", "memory", "Remember", "メモリ", "覚える"]) {
      expect(where(query), query).toEqual(["agent:記憶"]);
    }
    setLocale("en");
    expect(where("記憶")).toEqual(["agent:Memory"]);
    setLocale("ja");
    expect(where("memory")).toEqual(["agent:記憶"]);
  });

  test("finds a tab by its name, before its rows, and a brand by its name; case does not matter", () => {
    expect(where("連接")[0]).toBe("connections:連接");
    expect(where("telegram")).toEqual(["messaging:訊息平台"]);
    expect(where("OPENROUTER")).toEqual(["models:新增供應商", "usage:金鑰", "usage:OpenRouter 帳號本月用量"]);
    expect(where("模型")).toEqual(["models:模型與用量", "models:語音轉文字", "models:整理文字", "models:拉斐爾的回答"]);
    expect(where("Models & usage")[0]).toBe("models:模型與用量");
  });

  test("finds nothing for nothing, and says so for what it does not know", () => {
    expect(searchSettings("  ")).toEqual([]);
    expect(where("zzz")).toEqual([]);
    expect(searchResultsHTML([])).toContain("找不到符合的設定");
  });

  test("lists what it found as real buttons, each with the tab it is in under it", () => {
    const html = searchResultsHTML(searchSettings("麥克風"));
    expect(html).toBe('<button type="button" class="nav set-hit" data-hit="0"><span class="hit-t">麥克風</span><span class="hit-p">聽寫</span></button>');
  });

  test("knows a row of every tab, and each row it knows is drawn on its tab under that name, in every language", () => {
    expect(new Set(SEARCH_INDEX.map((e) => canonicalPane(e.pane)))).toEqual(new Set(IDS));
    for (const locale of LOCALES) {
      setLocale(locale);
      for (const entry of SEARCH_INDEX) {
        if (!entry.label) continue;
        const name = (t as unknown as (key: string) => string)(entry.label).replace("&", "&amp;");
        // a row's name, or the name of a section (電腦操作 is found as a whole)
        const html = tab(entry.pane, { memory: emptyMemoryView(), rules: emptyRulesView(), services: emptyServicesView() });
        const headings = [...html.matchAll(/<h2(?: class="[^"]*")?>(.*?)<\/h2>/g)].map((m) => m[1]);
        // a capability of 服務 is a row of the rounded list, named by its `.nm`
        const items = [...html.matchAll(/<span class="nm">(.*?)<\/span>/g)].map((m) => m[1]);
        const addButton = /data-pv="directory"[^>]*>(.*?)<\/button>/.exec(html)?.[1].replace(/<[^>]*>/g, "") ?? "";
        expect([...names(html), ...items, ...headings, addButton].map((shown) => shown.replace("&#39;", "'")), `${locale} ${entry.label}`).toContain(name);
      }
    }
  });
});

describe("關於", () => {
  test("has the version, and the license behind a button", () => {
    const html = tab("about", { version: "0.2.0" });
    expect(rowOf(html, "版本")).toContain(">0.2.0</span>");
    expect(rowOf(html, "開源授權")).toContain('<button type="button" class="btn" data-act="license">查看</button>');
  });

  test("shows no number while the version is not known, rather than one written in the code", () => {
    expect(rowOf(tab("about", { version: "" }), "版本")).toContain('<span class="val num"></span>');
  });
});

describe("while the core does not answer", () => {
  test("the tabs that need it say so, and its settings cannot be changed; this computer's still can", () => {
    const down = { brain: null };
    for (const id of ["general", "appearance", "dictation", "agent", "usage", "data"] as const) expect(tab(id, down)).toContain("core-note");
    expect(tab("about", down)).not.toContain("core-note");
    expect(rowOf(tab("data", down), "保留聽寫紀錄")).toMatch(/data-k="retention" disabled>/);
    expect(rowOf(tab("data", down), "清除拉斐爾的對話")).not.toContain("disabled");
    const dictation = tab("dictation", down);
    expect(rowOf(dictation, "你說的語言")).toMatch(/data-k="languages" disabled>/);
    expect(rowOf(dictation, "麥克風")).not.toContain("disabled");
    expect(rowOf(dictation, "聽寫")).not.toContain("disabled");
    const agent = tab("agent", down);
    expect(rowOf(agent, "天氣的城市")).toContain("disabled");
    expect(rowOf(agent, "問拉斐爾")).not.toContain("disabled");
    const usage = tab("usage", down);
    expect(usage).not.toContain('id="limit-input"');
    for (const name of ["語音轉文字", "整理文字", "拉斐爾的回答"]) expect(rowOf(tab("models", down), name)).toMatch(/data-k="[\w-]+" disabled>/);
  });
});

describe("電腦操作", () => {
  const EXCEL = { exe: "excel.exe", name: "Microsoft Excel" };
  const CALC = { exe: "calculatorapp.exe", name: "Calculator" };
  const withApps = (apps: DeviceSettings["computer_apps"]) => tab("agent", { device: { ...device, computer_apps: apps } });

  test("is the last section of 拉斐爾: a row per always-allowed app with a borderless trash button", () => {
    const html = withApps([EXCEL, CALC]);
    expect(html).toContain("<h2>電腦操作</h2>");
    expect(html.lastIndexOf("<h2>")).toBe(html.indexOf("<h2>電腦操作</h2>"));
    expect(names(html).slice(-2)).toEqual(["Microsoft Excel", "Calculator"]);
    const excel = rowOf(html, "Microsoft Excel");
    expect(excel).toContain('data-act="computer-remove"');
    expect(excel).toContain('data-exe="excel.exe"');
    expect(excel).toContain('aria-label="移除"');
    // a trash icon only: no word beside it, no helper text anywhere
    expect(excel.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim()).toBe("Microsoft Excel");
    expect(described(html)).toEqual([]);
    expect(buttons(html).filter((b) => b.includes("computer-remove"))).toHaveLength(2);
  });

  test("with no app: one muted row, nothing else", () => {
    for (const apps of [[], undefined as unknown as DeviceSettings["computer_apps"]]) {
      const html = withApps(apps);
      expect(html).toContain('<span class="val">沒有一律允許的 App</span>');
      expect(html).not.toContain("computer-remove");
    }
    setLocale("en");
    expect(withApps([])).toContain("No apps are always allowed");
    setLocale("ja");
    expect(withApps([])).toContain("常に許可されたアプリはありません");
  });

  test("an app name is escaped", () => {
    const html = withApps([{ exe: 'x".exe', name: "<b>Evil</b>" }]);
    expect(html).not.toContain("<b>Evil</b>");
    expect(html).toContain("&lt;b&gt;Evil&lt;/b&gt;");
    expect(html).toContain('data-exe="x&quot;.exe"');
  });

  test("settings from before the feature have no always-allowed app", () => {
    const old = { ...device } as Partial<DeviceSettings>;
    delete old.computer_apps;
    expect(computerApps(old as DeviceSettings)).toEqual([]);
  });

  test("the settings wire the button to the removal of that one app, never a whole list", () => {
    // two apps removed quickly must not compute both lists from the settings the window had
    const source = readFileSync(path.resolve(import.meta.dirname, "settings.ts"), "utf8");
    expect(source).toContain('case "computer-remove"');
    expect(source).toContain("removeComputerApp(exe)");
    expect(source).not.toContain("computer_apps: computerAppsWithout");
  });

  test("the search finds the section by its name in any language", () => {
    expect(searchSettings("電腦操作").map((hit) => `${hit.pane}:${hit.text}`)).toEqual(["agent:電腦操作"]);
    expect(searchSettings("computer use").map((hit) => hit.pane)).toEqual(["agent"]);
    expect(searchSettings("コンピューター").map((hit) => hit.pane)).toEqual(["agent"]);
  });
});
