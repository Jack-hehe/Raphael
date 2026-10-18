import { describe, expect, test } from "vitest";
import {
  DEFAULT_LANGUAGES,
  type LanguageGroup,
  MAX_LANGUAGES,
  groupOf,
  languageButtonHTML,
  languageLabel,
  languageName,
  languagePanelHTML,
  normalizeLanguages,
  toggleLanguage,
} from "./languages";

/** What `languages.list` answers (a part of it). */
const GROUPS: LanguageGroup[] = [
  {
    id: "zh",
    name: "中文",
    variants: [
      { id: "zh-TW", name: "繁體中文（台灣）" },
      { id: "zh-HK", name: "繁體中文（香港）", note: "粵語" },
      { id: "zh-CN", name: "簡體中文（中國）" },
    ],
  },
  { id: "en", name: "English", variants: [{ id: "en-US", name: "English (US)" }, { id: "en-GB", name: "English (UK)" }] },
  { id: "ja", name: "日本語", variants: [{ id: "ja-JP", name: "日本語" }] },
  { id: "ko", name: "한국어", variants: [{ id: "ko-KR", name: "한국어" }] },
  { id: "fr", name: "Français", variants: [{ id: "fr-FR", name: "Français" }] },
  { id: "de", name: "Deutsch", variants: [{ id: "de-DE", name: "Deutsch" }] },
  { id: "x", name: "<b>x</b>", variants: [{ id: "x-1", name: "<i>one</i>" }, { id: "x-2", name: "two" }] },
];

describe("toggleLanguage", () => {
  test("adds a language at the end", () => {
    expect(toggleLanguage(GROUPS, ["zh-TW"], "ja-JP")).toEqual({ ok: true, languages: ["zh-TW", "ja-JP"] });
  });

  test("another variant of a group takes the place of the one chosen", () => {
    expect(toggleLanguage(GROUPS, ["en-US", "zh-TW", "ja-JP"], "zh-HK")).toEqual({ ok: true, languages: ["en-US", "zh-HK", "ja-JP"] });
  });

  test("a chosen language is dropped", () => {
    expect(toggleLanguage(GROUPS, ["zh-TW", "ja-JP"], "zh-TW")).toEqual({ ok: true, languages: ["ja-JP"] });
  });

  test("the last language stays", () => {
    const result = toggleLanguage(GROUPS, ["zh-TW"], "zh-TW");
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.reason).toContain("至少");
  });

  test("no sixth language, but a variant can still be swapped at five", () => {
    const five = ["zh-TW", "en-US", "ja-JP", "ko-KR", "fr-FR"];
    expect(five).toHaveLength(MAX_LANGUAGES);
    const sixth = toggleLanguage(GROUPS, five, "de-DE");
    expect(sixth.ok).toBe(false);
    if (!sixth.ok) expect(sixth.reason).toContain("5");
    expect(toggleLanguage(GROUPS, five, "en-GB")).toEqual({ ok: true, languages: ["zh-TW", "en-GB", "ja-JP", "ko-KR", "fr-FR"] });
  });

  test("an id the list does not know is added like a single language", () => {
    expect(toggleLanguage(GROUPS, ["zh-TW"], "it-IT")).toEqual({ ok: true, languages: ["zh-TW", "it-IT"] });
    expect(toggleLanguage([], ["zh-TW"], "en-US")).toEqual({ ok: true, languages: ["zh-TW", "en-US"] });
  });
});

describe("names", () => {
  test("a variant is called by its name; an unknown id by itself", () => {
    expect(languageName(GROUPS, "zh-HK")).toBe("繁體中文（香港）");
    expect(languageName(GROUPS, "it-IT")).toBe("it-IT");
    expect(groupOf(GROUPS, "en-GB")?.id).toBe("en");
    expect(groupOf(GROUPS, "it-IT")).toBeUndefined();
  });

  test("the setting is a list of ids, never empty", () => {
    expect(normalizeLanguages(["zh-TW", "ja-JP"])).toEqual(["zh-TW", "ja-JP"]);
    for (const bad of [undefined, null, "zh-TW", [], [1, ""], {}]) expect(normalizeLanguages(bad)).toEqual(DEFAULT_LANGUAGES);
    expect(DEFAULT_LANGUAGES).toEqual(["zh-TW"]);
  });

  test("the button shows each chosen language as a chip, escaped", () => {
    const html = languageButtonHTML(GROUPS, ["zh-TW", "x-1"], ' data-k="languages"');
    expect(html).toContain('data-k="languages"');
    expect(html).toContain('<span class="lchip">繁體中文（台灣）</span>');
    expect(html).toContain("&lt;i&gt;one&lt;/i&gt;");
    expect(html).not.toContain("<i>one</i>");
    expect(languageLabel(GROUPS, ["zh-TW", "ja-JP"])).toBe("語言：繁體中文（台灣）、日本語");
  });
});

describe("the panel", () => {
  test("groups with variants fold; the open one lists them with the chosen one pressed", () => {
    const closed = languagePanelHTML(GROUPS, ["zh-HK", "ja-JP"], null);
    expect(closed).toContain('data-group="zh" aria-expanded="false"');
    expect(closed).not.toContain('data-lang="zh-TW"');
    expect(closed).toContain("已選 2／5");
    expect(closed).toContain("會自動偵測你說的語言");
    const open = languagePanelHTML(GROUPS, ["zh-HK", "ja-JP"], "zh");
    expect(open).toContain('data-group="zh" aria-expanded="true"');
    expect(open).toContain('data-lang="zh-HK" aria-pressed="true"');
    expect(open).toContain('data-lang="zh-TW" aria-pressed="false"');
    expect(open).toContain("粵語");
    expect(open).toContain('data-lang="ja-JP" aria-pressed="true"');
  });

  test("at five, what cannot be added is marked, and a group with a choice is not", () => {
    const html = languagePanelHTML(GROUPS, ["zh-TW", "en-US", "ja-JP", "ko-KR", "fr-FR"], "zh");
    expect(html).toContain('data-lang="de-DE" aria-pressed="false" aria-disabled="true"');
    expect(html).toContain('data-lang="zh-HK" aria-pressed="false">');
  });

  test("names from the core are escaped", () => {
    const html = languagePanelHTML(GROUPS, ["zh-TW"], "x");
    expect(html).toContain("&lt;b&gt;x&lt;/b&gt;");
    expect(html).not.toContain("<b>x</b>");
    expect(html).not.toContain("<i>one</i>");
  });

  test("languages with variants come first; the single ones follow under 其他語言", () => {
    const html = languagePanelHTML(GROUPS, ["zh-TW"], null);
    const zh = html.indexOf('data-group="zh"');
    const others = html.indexOf("其他語言");
    const ja = html.indexOf('data-lang="ja-JP"');
    expect(zh).toBeGreaterThan(-1);
    expect(others).toBeGreaterThan(zh);
    expect(ja).toBeGreaterThan(others);
    expect(html).not.toContain('data-group="ja"');
  });

  test("without a list it says so", () => {
    expect(languagePanelHTML(null, ["zh-TW"], null)).toContain("無法取得語言清單");
  });
});
