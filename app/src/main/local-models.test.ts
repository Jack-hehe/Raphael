// The local tab of the speech-to-text picker without its page: what each model of the catalogue
// shows (name, languages, size, a line on what it is for and its state), the confirmations before a
// download or a deletion, and the progress of a download. The popover around it is tried in the app.

import { afterEach, describe, expect, test } from "vitest";
import { setLocale } from "./i18n";
import {
  type LocalView,
  faceTitle,
  formatSize,
  languagesText,
  localListHTML,
  localState,
  stateText,
  tabsHTML,
} from "./local-models";
import type { LocalModel } from "./types";

afterEach(() => setLocale("zh-TW"));

const qwen: LocalModel = { name: "qwen3-asr", title: "Qwen3-ASR", languages: ["zh", "en", "ja", "ko", "yue"], note: "dictionary", size: 878_702_423, ready: true };
const sense: LocalModel = { name: "sensevoice", title: "SenseVoice", languages: ["zh", "en", "ja", "ko", "yue"], note: "fast", size: 163_002_883, ready: false };
const breeze: LocalModel = { name: "breeze-asr-25", title: "Breeze-ASR-25", languages: ["zh", "en"], note: "taiwan", size: 1_775_001_000, ready: false };
const paraformer: LocalModel = { name: "paraformer-zh", title: "Paraformer", languages: ["zh"], note: "chinese", size: 227_405_559, ready: true };
const MODELS = [qwen, sense, breeze, paraformer];

const view = (over: Partial<LocalView> = {}): LocalView => ({ models: MODELS, active: "qwen3-asr", download: null, ...over });

/** The row of one model: from its opening tag to the next row. */
const rowOf = (html: string, name: string): string => {
  const start = html.indexOf(`data-name="${name}"`);
  const from = html.lastIndexOf("<div", start);
  const next = html.indexOf('<div class="lm-row"', start);
  return html.slice(from, next < 0 ? undefined : next);
};

describe("sizes", () => {
  test("megabytes and gigabytes, as the download is announced", () => {
    expect(formatSize(163_002_883)).toBe("163 MB");
    expect(formatSize(878_702_423)).toBe("879 MB");
    expect(formatSize(1_775_001_000)).toBe("1.8 GB");
    expect(formatSize(999_400_000)).toBe("999 MB");
    expect(formatSize(1_000_000_000)).toBe("1.0 GB");
  });
});

describe("the languages", () => {
  test("are named in the language of the window", () => {
    expect(languagesText(["zh", "en", "ja"])).toBe("中文、英文、日文");
    expect(languagesText(["zh", "en", "ja", "multi"])).toBe("中文、英文、日文、其他語言");
    setLocale("en");
    expect(languagesText(["zh", "en", "ja"])).toBe("Chinese, English, Japanese");
    setLocale("ja");
    expect(languagesText(["zh", "yue"])).toBe("中国語、広東語");
  });

  test("a code the app does not know is shown as it is", () => {
    expect(languagesText(["zh", "xx"])).toBe("中文、xx");
  });
});

describe("the state of a model", () => {
  test("is not downloaded, downloading, downloaded or in use", () => {
    expect(localState(sense, view()).kind).toBe("absent");
    expect(localState(paraformer, view()).kind).toBe("ready");
    expect(localState(qwen, view()).kind).toBe("active");
    expect(localState(sense, view({ download: { name: "sensevoice", done: 42, total: 100 } }))).toEqual({ kind: "downloading", percent: 42 });
  });

  test("a download whose size is not known yet is at 0%, and one that is complete is being installed", () => {
    expect(localState(sense, view({ download: { name: "sensevoice", done: 0, total: 0 } }))).toEqual({ kind: "downloading", percent: 0 });
    expect(localState(sense, view({ download: { name: "sensevoice", done: 100, total: 100 } })).kind).toBe("installing");
  });

  test("only the model being downloaded is downloading, and in use only while the local engine is in use", () => {
    const busy = view({ download: { name: "sensevoice", done: 1, total: 2 } });
    expect(localState(breeze, busy).kind).toBe("absent");
    expect(localState(qwen, view({ active: null })).kind).toBe("ready");
  });

  test("has its words", () => {
    expect(stateText({ kind: "absent" })).toBe("未下載");
    expect(stateText({ kind: "downloading", percent: 42 })).toBe("下載中 42%");
    expect(stateText({ kind: "installing" })).toBe("安裝中…");
    expect(stateText({ kind: "ready" })).toBe("已下載");
    expect(stateText({ kind: "active" })).toBe("使用中");
    setLocale("en");
    expect(stateText({ kind: "downloading", percent: 7 })).toBe("Downloading 7%");
    expect(stateText({ kind: "active" })).toBe("In use");
  });
});

describe("the list", () => {
  test("shows every model with its name, languages, size, what it is for and its state", () => {
    const html = localListHTML(view(), null);
    const row = rowOf(html, "sensevoice");
    expect(row).toContain("SenseVoice");
    expect(row).toContain("中文、英文、日文、韓文、粵語 · 163 MB · 速度最快");
    expect(row).toContain("未下載");
    expect(rowOf(html, "qwen3-asr")).toContain("使用中");
    expect(rowOf(html, "qwen3-asr")).toContain("879 MB · 最準，會參考詞典");
    expect(rowOf(html, "breeze-asr-25")).toContain("1.8 GB");
    expect(rowOf(html, "breeze-asr-25")).toContain("台灣華語");
    expect(html.match(/class="lm-row"/g)).toHaveLength(MODELS.length);
  });

  test("keeps the order of the catalogue", () => {
    const html = localListHTML(view(), null);
    const at = MODELS.map((m) => html.indexOf(`data-name="${m.name}"`));
    expect(at).toEqual([...at].sort((a, b) => a - b));
  });

  test("a row says all of it to a screen reader", () => {
    const row = rowOf(localListHTML(view(), null), "sensevoice");
    expect(row).toContain('aria-label="SenseVoice, 中文、英文、日文、韓文、粵語, 163 MB, 速度最快, 未下載"');
  });

  test("the model in use is marked, and only a downloaded one that is not in use can be deleted", () => {
    const html = localListHTML(view(), null);
    expect(rowOf(html, "qwen3-asr")).toContain('aria-current="true"');
    expect(rowOf(html, "qwen3-asr")).not.toContain('data-act="delete"');
    expect(rowOf(html, "sensevoice")).not.toContain('data-act="delete"');
    const deletable = rowOf(html, "paraformer-zh");
    expect(deletable).toContain('data-act="delete"');
    expect(deletable).toContain('aria-label="刪除 Paraformer"');
    expect(deletable).not.toContain("aria-current");
  });

  test("with the cloud in use no model is in use, and the downloaded ones can be deleted", () => {
    const html = localListHTML(view({ active: null }), null);
    expect(html).not.toContain("aria-current");
    expect(rowOf(html, "qwen3-asr")).toContain('data-act="delete"');
  });

  test("every action is a real button, never a word to click", () => {
    const html = localListHTML(view({ download: { name: "sensevoice", done: 1, total: 2 } }), { kind: "delete", name: "paraformer-zh" });
    for (const tag of html.match(/<[a-z]+ [^>]*data-act="[^"]+"[^>]*>/g) ?? []) expect(tag.startsWith("<button type=\"button\"")).toBe(true);
    expect(html.match(/data-act=/g)!.length).toBeGreaterThan(3);
  });

  test("the name of a model from the core is escaped", () => {
    const html = localListHTML(view({ models: [{ ...sense, title: "<img src=x>" }] }), null);
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;img src=x&gt;");
  });
});

describe("choosing a model that is not downloaded", () => {
  test("asks first, with the size, and has a button to download and one to cancel", () => {
    const row = rowOf(localListHTML(view(), { kind: "download", name: "sensevoice" }), "sensevoice");
    expect(row).toContain("下載 SenseVoice（163 MB）？");
    expect(row).toMatch(/<button type="button" class="btn pri sm" data-act="confirm"[^>]*>下載<\/button>/);
    expect(row).toMatch(/<button type="button" class="btn sm" data-act="dismiss"[^>]*>取消<\/button>/);
    expect(row).not.toContain('data-act="use"');
  });

  test("asks in the language of the window", () => {
    setLocale("en");
    const row = rowOf(localListHTML(view(), { kind: "download", name: "breeze-asr-25" }), "breeze-asr-25");
    expect(row).toContain("Download Breeze-ASR-25 (1.8 GB)?");
    expect(row).toContain(">Download</button>");
    expect(row).toContain(">Cancel</button>");
  });

  test("only the row asked about asks", () => {
    const html = localListHTML(view(), { kind: "download", name: "sensevoice" });
    expect(rowOf(html, "breeze-asr-25")).toContain('data-act="use"');
    expect(html.match(/data-act="confirm"/g)).toHaveLength(1);
  });
});

describe("deleting a model", () => {
  test("asks first, and says what it frees", () => {
    const row = rowOf(localListHTML(view(), { kind: "delete", name: "paraformer-zh" }), "paraformer-zh");
    expect(row).toContain("刪除 Paraformer（釋出 227 MB）？");
    expect(row).toMatch(/data-act="confirm"[^>]*>刪除<\/button>/);
    expect(row).toMatch(/data-act="dismiss"[^>]*>取消<\/button>/);
  });
});

describe("a download", () => {
  const busy = view({ download: { name: "sensevoice", done: 41_000_000, total: 163_002_883 } });

  test("shows its progress as a bar and a percentage, and can be cancelled", () => {
    const row = rowOf(localListHTML(busy, null), "sensevoice");
    expect(row).toContain("下載中 25%");
    expect(row).toContain('role="progressbar"');
    expect(row).toContain('aria-valuenow="25"');
    expect(row).toContain('style="width:25%"');
    expect(row).toMatch(/<button type="button" class="btn sm" data-act="cancel"[^>]*>取消<\/button>/);
    expect(row).toContain("disabled");
  });

  test("after the last byte it is being installed, and the bar is full", () => {
    const row = rowOf(localListHTML(view({ download: { name: "sensevoice", done: 163_002_883, total: 163_002_883 } }), null), "sensevoice");
    expect(row).toContain("安裝中…");
    expect(row).toContain('style="width:100%"');
  });

  test("other models can not be chosen meanwhile", () => {
    const html = localListHTML(busy, null);
    expect(rowOf(html, "breeze-asr-25")).toMatch(/data-act="use"[^>]*disabled/);
    expect(rowOf(html, "paraformer-zh")).not.toContain('data-act="delete"');
  });
});

describe("while there is no catalogue", () => {
  test("says it is being read, or that it cannot be had", () => {
    expect(localListHTML(view({ models: "loading" }), null)).toContain("正在載入…");
    expect(localListHTML(view({ models: "failed" }), null)).toContain("目前無法取得本機模型清單");
  });
});

describe("the tabs", () => {
  test("are two real buttons, the open one selected", () => {
    const html = tabsHTML("local");
    expect(html).toContain('role="tablist"');
    expect(html).toMatch(/<button type="button" class="tab" role="tab" aria-selected="false" id="mp-tab-cloud"[^>]*>雲端<\/button>/);
    expect(html).toMatch(/<button type="button" class="tab" role="tab" aria-selected="true" id="mp-tab-local"[^>]*>本機<\/button>/);
    expect(tabsHTML("cloud")).toMatch(/aria-selected="true"[^>]*id="mp-tab-cloud"/);
  });

  test("only the open one is a stop of the Tab key", () => {
    expect(tabsHTML("cloud")).toMatch(/id="mp-tab-cloud"[^>]*tabindex="0"/);
    expect(tabsHTML("cloud")).toMatch(/id="mp-tab-local"[^>]*tabindex="-1"/);
  });

  test("speak the language of the window", () => {
    setLocale("ja");
    expect(tabsHTML("cloud")).toContain(">クラウド</button>");
    expect(tabsHTML("cloud")).toContain(">ローカル</button>");
  });
});

describe("what the picker's button shows", () => {
  test("is the model in use, after 本機", () => {
    expect(faceTitle(view({ active: "sensevoice" }))).toBe("本機 · SenseVoice");
  });

  test("is nothing while the cloud is in use", () => {
    expect(faceTitle(view({ active: null }))).toBeNull();
  });

  test("falls back to the id while the catalogue is not read", () => {
    expect(faceTitle(view({ models: "loading", active: "sensevoice" }))).toBe("本機 · sensevoice");
  });
});
