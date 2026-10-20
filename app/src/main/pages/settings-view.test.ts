import { afterEach, describe, expect, test } from "vitest";
import { setLocale } from "../i18n";
import type { LanguageGroup } from "../languages";
import type { BrainSettings, CatalogModel, DeviceSettings, ModelCatalog, SettingsPane, Status } from "../types";
import {
  APPEARANCES,
  PANES,
  type SettingsView,
  loadingHTML,
  microphoneChoices,
  modelPickerLook,
  paneHTML,
  priceTexts,
} from "./settings-view";

afterEach(() => setLocale("zh-TW"));

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
  context_length: stt ? null : 1_000_000, created_at_ms: NOW, capabilities: {}, recommended: true,
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

const view = (over: Partial<SettingsView> = {}): SettingsView => ({
  pane: "general", device, brain, hasKey: true, openrouter, spend, catalog, checkingOpenrouter: false,
  devices: { devices: [{ index: 0, name: REALTEK, is_default: true }], selected: null, automatic_device: REALTEK },
  altgr: false, version: "0.1.0", replacingKey: false, keyBusy: false, keyError: null, limitDraft: null, limitError: null,
  cityDraft: null, cityError: null, languageGroups: GROUPS, now: NOW, ...over,
});
const tab = (pane: SettingsPane, over: Partial<SettingsView> = {}): string => paneHTML(view({ pane, ...over }));
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
  test("are General, Dictation, Raphael, Models & usage and About, in each language", () => {
    expect(IDS).toEqual(["general", "dictation", "agent", "usage", "about"]);
    const titles = { "zh-TW": ["一般", "聽寫", "拉斐爾", "模型與用量", "關於"], en: ["General", "Dictation", "Raphael", "Models & usage", "About"], ja: ["一般", "音声入力", "ラファエル", "モデルと使用量", "アプリ情報"] };
    for (const locale of LOCALES) {
      setLocale(locale);
      expect(PANES.map((p) => p.title())).toEqual(titles[locale]);
    }
  });

  test("each opens with its name and one short line on what it is for", () => {
    for (const locale of LOCALES) {
      setLocale(locale);
      for (const id of IDS) {
        const html = tab(id);
        const title = PANES.find((p) => p.id === id)!.title();
        expect(html.startsWith(`<h1 id="set-title" tabindex="-1">${title.replace("&", "&amp;")}</h1><p class="purpose">`)).toBe(true);
        expect(html.match(/class="purpose"/g)).toHaveLength(1);
        const purpose = /<p class="purpose">(.*?)<\/p>/.exec(html)![1];
        expect(purpose.length).toBeGreaterThan(3);
        expect(purpose.length).toBeLessThan(60);
      }
    }
  });

  test("say what they are for in the owner's words", () => {
    const purpose = (id: SettingsPane, over: Partial<SettingsView> = {}): string => /<p class="purpose">(.*?)<\/p>/.exec(tab(id, over))![1];
    expect(purpose("general")).toBe("介面和桌面圖案的外觀");
    expect(purpose("dictation")).toBe("按快捷鍵說話，拉斐爾把你說的打出來");
    expect(purpose("agent")).toBe("按 右 Alt + Space 問拉斐爾");
    expect(purpose("usage")).toBe("拉斐爾用的雲端模型和花費");
  });

  test("keep the name and the purpose while the settings are being read", () => {
    const html = loadingHTML(view({ pane: "usage" }));
    expect(html).toContain('<h1 id="set-title" tabindex="-1">模型與用量</h1>');
    expect(html).toContain('<p class="purpose">');
    expect(html).toContain("settings-skeleton");
  });
});

describe("the owner's rules, in every tab and language", () => {
  test("an action is a button with a shape and a choice is a dropdown or a switch: nothing else can be clicked", () => {
    for (const locale of LOCALES) {
      setLocale(locale);
      for (const id of IDS) {
        for (const over of [{}, { hasKey: false }, { brain: null }, { altgr: true }]) {
          const html = tab(id, over);
          for (const tag of buttons(html)) expect(tag).toMatch(/class="(btn[ "]|sel[ "]|tg")/);
          expect(html).not.toMatch(/quiet|class="link"|<a\b/);
        }
      }
    }
  });

  test("no tab shows the options of a setting in a row: no segmented controls, radio rows or chips", () => {
    for (const id of IDS) {
      const html = tab(id);
      expect(html).not.toMatch(/role="radio|aria-pressed|class="tabs"|class="chip|lchip/);
    }
  });

  test("a line under a row's name only where the setting would otherwise be set wrong", () => {
    expect(described(tab("general"))).toEqual(["字幕"]);
    expect(described(tab("dictation"))).toEqual(["你說的語言"]);
    expect(described(tab("agent"))).toEqual(["天氣的城市"]);
    // the key's status, the month's forecast, what the limit does, and what each model costs
    expect(described(tab("usage"))).toEqual(["金鑰", "Raphael 本月花費", "每月上限", "語音轉文字", "整理文字", "拉斐爾的回答"]);
  });
});

describe("一般", () => {
  test("has the interface language, the theme and the emblem's size as dropdowns, the subtitles and the start as switches", () => {
    const html = tab("general");
    expect(groups(html)).toEqual(["外觀", "啟動"]);
    expect(names(html)).toEqual(["介面語言", "主題", "桌面圖案大小", "字幕", "開機時自動啟動"]);
    for (const k of ["ui-language", "appearance", "size"]) expect(html).toMatch(new RegExp(`<button type="button" class="sel"[^>]*data-k="${k}"`));
    for (const k of ["subtitles", "autostart"]) expect(html).toMatch(new RegExp(`class="tg" role="switch"[^>]*data-k="${k}"`));
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
    const choices = microphoneChoices(view());
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
    const choices = microphoneChoices(view({ devices }));
    expect(choices.map((c) => c.label)).toEqual(["自動偵測（目前：HyperX Cloud II Core Wi…）", cut, REALTEK]);
    expect(choices.map((c) => c.short)).toEqual(["自動（HyperX Cloud II…）", "HyperX Cloud II Core Wi…", "麥克風排列"]);
    const chosen = rowOf(tab("dictation", { devices, device: { ...device, microphone: cut } }), "麥克風");
    expect(chosen).toContain("<span>HyperX Cloud II Core Wi…</span>");
    expect(chosen).toContain(`title="${cut}"`);
  });

  test("keeps a chosen microphone that is not plugged in, said so", () => {
    const choices = microphoneChoices(view({ device: { ...device, microphone: "USB Mic (C-Media)" } }));
    expect(choices[choices.length - 1]).toEqual({ value: "USB Mic (C-Media)", label: "USB Mic (C-Media)（未連接）", short: "USB Mic（未連接）" });
  });

  test("has no switch for pausing the media or for learning: both are always on", () => {
    const html = tab("dictation");
    expect(html).not.toContain('class="tg"');
    expect(html).not.toMatch(/pause-media|data-k="learn"|字典/);
  });
});

describe("拉斐爾", () => {
  test("has the key that asks her and the weather city, and no choice of router", () => {
    for (const locale of LOCALES) {
      setLocale(locale);
      const html = tab("agent");
      expect(html).not.toMatch(/agent-router|Jev|Luna/);
      expect(html).not.toContain('data-k="agent-model"'); // with the other models
    }
    setLocale("zh-TW");
    const html = tab("agent");
    expect(groups(html)).toEqual(["開始方式", "回答"]);
    expect(names(html)).toEqual(["問拉斐爾", "天氣的城市"]);
    expect(rowOf(html, "問拉斐爾")).toContain('<button type="button" class="btn" data-act="rec-agent" data-k="rec-agent">變更</button>');
    expect(rowOf(html, "天氣的城市")).toContain('id="agent-city" type="text" value="台北"');
  });

  test("still says how to ask her when no key is set", () => {
    const html = tab("agent", { device: { ...device, agent_keys: [] } });
    expect(html).toContain('<p class="purpose">設定快捷鍵後，按下就能問拉斐爾</p>');
    expect(rowOf(html, "問拉斐爾")).toContain("尚未設定");
  });

  test("warns about Right Alt under her key, as under the dictation key, only when the keyboard uses it as AltGr", () => {
    const warning = '<div class="note"><span class="st warn"><span class="dot warn"></span>你的鍵盤配置把右 Alt 當 AltGr 用，建議換一個快捷鍵。</span></div>';
    const html = tab("agent", { altgr: true });
    expect(html).toContain(warning);
    expect(html.indexOf(warning)).toBeGreaterThan(html.indexOf('data-row="agent-hotkey"'));
    expect(html.indexOf(warning)).toBeLessThan(html.indexOf("天氣的城市"));
    expect(tab("dictation", { altgr: true })).toContain(warning);
    expect(tab("agent")).not.toContain("AltGr");
    expect(tab("agent", { altgr: true, device: { ...device, agent_keys: ["ControlLeft+Space"] } })).not.toContain("AltGr");
  });
});

describe("模型與用量", () => {
  test("is OpenRouter (the key, the spend, the limit), then the three models", () => {
    const html = tab("usage");
    expect(groups(html)).toEqual(["OpenRouter", "模型"]);
    expect(names(html)).toEqual(["金鑰", "Raphael 本月花費", "每月上限", "OpenRouter 帳號本月用量", "語音轉文字", "整理文字", "拉斐爾的回答"]);
  });

  test("shows how OpenRouter took the key, with buttons to check again and to change it", () => {
    const key = rowOf(tab("usage"), "金鑰");
    expect(key).toContain("已連線");
    expect(key).toContain("延遲 120 ms");
    expect(buttons(key)).toEqual(['<button type="button" class="btn" data-act="or-check">', '<button type="button" class="btn" data-act="key-replace" data-k="key">']);
    expect(key).toContain(">變更</button>");
    const bad = rowOf(tab("usage", { openrouter: { ...openrouter, state: "error", error: "auth" } }), "金鑰");
    expect(bad).toContain("金鑰無效，請更換");
  });

  test("asks for a key when there is none, or while it is being changed", () => {
    for (const over of [{ hasKey: false }, { replacingKey: true }]) {
      const key = rowOf(tab("usage", over), "金鑰");
      expect(key).toContain('id="key-input" type="password"');
      expect(key).toContain('class="btn pri" data-act="key-paste"');
      expect(key).not.toContain('data-act="key-replace"');
    }
    expect(rowOf(tab("usage", { replacingKey: true }), "金鑰")).toContain('data-act="key-cancel"');
  });

  test("puts the price of what is chosen under each model's name, its picker beside, one self-contained row each", () => {
    const html = tab("usage");
    for (const [name, k, role, price, chosen] of [
      ["語音轉文字", "stt-model", "stt", "每分鐘 $0.0067", "Scribe v2"],
      ["整理文字", "model", "cleanup", "輸入 $0.10 / 輸出 $0.40 每百萬 token", "GPT-6 Luna"],
      ["拉斐爾的回答", "agent-model", "answer", "輸入 $0.10 / 輸出 $0.40 每百萬 token", "GPT-6 Luna"],
    ]) {
      const row = rowOf(html, name);
      expect(row).toContain(`data-row="${k}"`);
      expect(row).toContain(
        `<div class="d"><span class="price num" data-price="${k}">${price}</span></div></div>` +
          `<div class="ctl"><span class="mp-slot" data-model-picker="${role}"><button type="button" class="sel mp-trigger" aria-haspopup="dialog"`,
      );
      // The button names what is chosen and leaves its price to the line under the row's name.
      expect(row).toMatch(new RegExp(`data-k="${k}"><span class="mp-tname">${chosen}</span><svg`));
      expect(row).not.toContain("mp-tprice");
      expect(buttons(row)).toHaveLength(1);
    }
    expect(priceTexts(view()).note).toBe("價格來自 OpenRouter，今天更新。實際花費依 OpenRouter 選用的供應商而定。");
  });

  test("offers this computer above the cloud models of speech-to-text, which costs nothing", () => {
    expect(modelPickerLook(view(), "stt").extras).toEqual([{ id: "local", name: "本機（離線，聲音不上傳）", price: "免費" }]);
    expect(modelPickerLook(view(), "cleanup").extras).toEqual([]);
    const local = rowOf(tab("usage", { brain: { ...brain, stt_engine: "local" } }), "語音轉文字");
    expect(local).toContain('data-price="stt-model">免費</span>');
    expect(local).toContain('<span class="mp-tname">本機（離線，聲音不上傳）</span>');
  });

  test("gives each picker the catalog, what is chosen and the row's name", () => {
    const look = modelPickerLook(view(), "answer");
    expect(look).toMatchObject({ role: "answer", key: "agent-model", label: "拉斐爾的回答", current: "openai/gpt-6-luna", catalog, loading: false, showPrice: false, disabled: false });
    expect(modelPickerLook(view({ brain: { ...brain, stt_cloud_model: "openai/gpt-transcribe" } }), "stt").current).toBe("openai/gpt-transcribe");
  });

  test("says the prices are coming, and shows none it could not get", () => {
    expect(rowOf(tab("usage", { catalog: "loading" }), "整理文字")).toContain(">價格載入中…</span>");
    expect(modelPickerLook(view({ catalog: "loading" }), "cleanup")).toMatchObject({ catalog: null, loading: true });
    expect(modelPickerLook(view({ catalog: "failed" }), "cleanup")).toMatchObject({ catalog: null, loading: false });
    expect(priceTexts(view({ catalog: "failed" }))).toEqual({ model: "", "agent-model": "", "stt-model": "", note: "目前無法取得模型價格。" });
  });

  test("shows a saved model the catalog does not have by its id, with an unknown price", () => {
    const row = rowOf(tab("usage", { brain: { ...brain, cleanup_model: "x/old" } }), "整理文字");
    expect(row).toContain('<span class="mp-tname">old</span>');
    expect(row).toContain('data-price="model">價格未知</span>');
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
    for (const id of ["dictation", "agent", "usage"] as const) expect(tab(id, down)).toContain("core-note");
    for (const id of ["general", "about"] as const) expect(tab(id, down)).not.toContain("core-note");
    const dictation = tab("dictation", down);
    expect(rowOf(dictation, "你說的語言")).toMatch(/data-k="languages" disabled>/);
    expect(rowOf(dictation, "麥克風")).not.toContain("disabled");
    expect(rowOf(dictation, "聽寫")).not.toContain("disabled");
    const agent = tab("agent", down);
    expect(rowOf(agent, "天氣的城市")).toContain("disabled");
    expect(rowOf(agent, "問拉斐爾")).not.toContain("disabled");
    const usage = tab("usage", down);
    expect(rowOf(usage, "每月上限")).toContain("disabled");
    for (const name of ["語音轉文字", "整理文字", "拉斐爾的回答"]) expect(rowOf(usage, name)).toMatch(/data-k="[\w-]+" disabled>/);
  });
});
