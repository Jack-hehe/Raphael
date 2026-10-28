import { afterEach, describe, expect, test } from "vitest";
import { setLocale } from "./i18n";
import { ico } from "./ui";
import {
  DEFAULT_LANGUAGES,
  type LanguageGroup,
  chooseLanguageVariant,
  languagePanelHTML,
  languageSelectHTML,
  languageSummary,
  normalizeLanguages,
  selectedLanguageGroups,
  toggleTranscriptionLanguage,
} from "./languages";

afterEach(() => setLocale("zh-TW"));

/** A representative `languages.list` response, including names that must be escaped. */
const GROUPS: LanguageGroup[] = [
  { id: "zh", name: "中文", variants: [{ id: "zh-TW", name: "繁體中文（台灣）" }, { id: "zh-HK", name: "繁體中文（香港）" }, { id: "zh-CN", name: "簡體中文（中國）" }, { id: "yue-HK", name: "粵語（香港）" }] },
  { id: "en", name: "English", variants: [{ id: "en-US", name: "English (US)" }, { id: "en-GB", name: "English (UK)" }] },
  { id: "ja", name: "日本語", variants: [{ id: "ja-JP", name: "日本語" }] },
  { id: "ko", name: "한국어", variants: [{ id: "ko-KR", name: "한국어" }] },
  { id: "fr", name: "Français", variants: [{ id: "fr-FR", name: "Français" }] },
  { id: "de", name: "Deutsch", variants: [{ id: "de-DE", name: "Deutsch" }] },
  { id: "x", name: "<b>x</b>", variants: [{ id: "x-1", name: "<i>one</i>" }, { id: "x-2", name: "two" }] },
];

describe("transcription languages mapped to core variant ids", () => {
  test("reads existing non-default variants in the saved priority order", () => {
    const selected = ["en-GB", "yue-HK", "ja-JP"];
    expect(selectedLanguageGroups(GROUPS, selected).map((g) => g.id)).toEqual(["en", "zh", "ja"]);
    expect(normalizeLanguages(selected)).toEqual(["en-GB", "yue-HK", "ja-JP"]);
  });

  test("adding a language appends its first variant without changing existing variants", () => {
    expect(toggleTranscriptionLanguage(GROUPS, ["zh-HK"], "en")).toEqual({ ok: true, languages: ["zh-HK", "en-US"] });
    expect(toggleTranscriptionLanguage(GROUPS, ["zh-CN"], "ja")).toEqual({ ok: true, languages: ["zh-CN", "ja-JP"] });
  });

  test("removing a language removes its chosen variant and preserves the others", () => {
    expect(toggleTranscriptionLanguage(GROUPS, ["en-GB", "zh-HK", "ja-JP"], "zh")).toEqual({ ok: true, languages: ["en-GB", "ja-JP"] });
  });

  test("keeps at least one transcription language", () => {
    expect(toggleTranscriptionLanguage(GROUPS, ["yue-HK"], "zh")).toMatchObject({ ok: false });
  });

  test("rejects a sixth language but permits removing one at the limit", () => {
    const five = ["zh-HK", "en-GB", "ja-JP", "ko-KR", "fr-FR"];
    expect(toggleTranscriptionLanguage(GROUPS, five, "de")).toMatchObject({ ok: false });
    expect(toggleTranscriptionLanguage(GROUPS, five, "en")).toEqual({ ok: true, languages: ["zh-HK", "ja-JP", "ko-KR", "fr-FR"] });
    expect(five).toEqual(["zh-HK", "en-GB", "ja-JP", "ko-KR", "fr-FR"]);
  });

  test("rejects groups absent from the core list rather than inventing variant ids", () => {
    expect(toggleTranscriptionLanguage(GROUPS, ["zh-TW"], "unknown")).toMatchObject({ ok: false });
    expect(toggleTranscriptionLanguage([], ["zh-TW"], "en")).toMatchObject({ ok: false });
  });
});

describe("language variants", () => {
  test("replaces one selected language's variant in place", () => {
    expect(chooseLanguageVariant(GROUPS, ["en-GB", "zh-TW", "ja-JP"], "zh-CN")).toEqual({ ok: true, languages: ["en-GB", "zh-CN", "ja-JP"] });
  });

  test("selecting the current variant keeps the language selected", () => {
    expect(chooseLanguageVariant(GROUPS, ["zh-HK"], "zh-HK")).toEqual({ ok: true, languages: ["zh-HK"] });
  });

  test("swaps a variant when five languages are selected", () => {
    expect(chooseLanguageVariant(GROUPS, ["zh-TW", "en-US", "ja-JP", "ko-KR", "fr-FR"], "en-GB")).toEqual({ ok: true, languages: ["zh-TW", "en-GB", "ja-JP", "ko-KR", "fr-FR"] });
  });

  test("does not add languages from the variant picker", () => {
    expect(chooseLanguageVariant(GROUPS, ["zh-TW"], "en-GB")).toMatchObject({ ok: false });
    expect(chooseLanguageVariant(GROUPS, ["zh-TW"], "unknown")).toMatchObject({ ok: false });
  });
});

describe("the language row's dropdown", () => {
  test("is a dropdown that names each chosen language by its variant, or by its own name when it has one", () => {
    const html = languageSelectHTML(GROUPS, ["zh-HK", "en-GB", "ja-JP"], ' data-k="languages"');
    expect(html).toContain('class="sel lang-sel"');
    expect(html).toContain('aria-haspopup="dialog"');
    expect(html).toContain('data-k="languages"');
    expect(html).toContain("<span>繁體中文（香港）、English (UK)、日本語</span>");
    expect(html).toContain('aria-label="你說的語言：繁體中文（香港）、English (UK)、日本語"');
    expect(html).toContain('title="繁體中文（香港）、English (UK)、日本語"');
    // a chevron like the other dropdowns: not chips, not a pencil
    expect(html).toContain(ico("down"));
    expect(html).not.toContain("lchip");
  });

  test("keeps the saved order and joins the names the way the language of the window does", () => {
    expect(languageSummary(GROUPS, ["ja-JP", "zh-TW"])).toBe("日本語、繁體中文（台灣）");
    setLocale("en");
    expect(languageSummary(GROUPS, ["ja-JP", "zh-TW"])).toBe("日本語, Traditional Chinese (Taiwan)");
  });

  test("escapes names from the core", () => {
    const html = languageSelectHTML(GROUPS, ["x-1"]);
    expect(html).toContain("&lt;i&gt;one&lt;/i&gt;");
    expect(html).not.toContain("<i>one</i>");
  });

  test("keeps variant ids readable before the language list loads", () => {
    expect(languageSelectHTML([], ["zh-HK", "en-GB"])).toContain("zh-HK、en-GB");
  });
});

describe("the language picker", () => {
  test("lists every language, the chosen ones checked", () => {
    const html = languagePanelHTML(GROUPS, ["zh-HK", "ja-JP"]);
    expect(html).toContain('data-language="zh" aria-pressed="true"');
    expect(html).toContain('data-language="ja" aria-pressed="true"');
    expect(html).toContain('data-language="en" aria-pressed="false"');
    expect(html).toContain("已選 2／5");
  });

  test("puts the variants of a chosen language right under it, as one radio group", () => {
    const html = languagePanelHTML(GROUPS, ["en-GB", "zh-HK", "ja-JP"]);
    const zh = html.indexOf('data-language="zh"');
    const group = html.indexOf('data-variant-group="zh"');
    expect(zh).toBeLessThan(group);
    expect(group).toBeLessThan(html.indexOf('data-language="en"'));
    expect(html).toContain('role="radiogroup" aria-label="中文的寫法"');
    expect(html).toContain('data-variant="zh-HK" aria-checked="true"');
    expect(html).toContain('data-variant="zh-TW" aria-checked="false"');
    expect(html).toContain('data-variant="en-GB" aria-checked="true"');
    expect(html).toContain("台灣用語，例如 軟體、視窗");
  });

  test("shows no variants for a language that is not chosen, only how many it has, and none for a language with one", () => {
    const html = languagePanelHTML(GROUPS, ["ja-JP"]);
    expect(html).not.toContain("data-variant=");
    expect(html).toContain('data-language="zh" aria-pressed="false"><span class="ck"></span><span class="mt">中文</span><span class="md">4 種寫法</span>');
    expect(html).not.toContain('data-variant-group="ja"');
  });

  test("marks the languages that cannot be added at five, while the chosen ones can still be dropped", () => {
    const html = languagePanelHTML(GROUPS, ["zh-TW", "en-US", "ja-JP", "ko-KR", "fr-FR"]);
    expect(html).toContain('data-language="de" aria-pressed="false" aria-disabled="true"');
    expect(html).toContain('data-language="en" aria-pressed="true">');
  });

  test("escapes names from the core", () => {
    const html = languagePanelHTML(GROUPS, ["x-1"]);
    expect(html).toContain("&lt;b&gt;x&lt;/b&gt;");
    expect(html).toContain("&lt;i&gt;one&lt;/i&gt;");
    expect(html).not.toContain("<b>x</b>");
    expect(html).not.toContain("<i>one</i>");
  });

  test("says when the core's language list cannot be had", () => {
    expect(languagePanelHTML(null, ["zh-TW"])).toContain("無法取得語言清單");
    expect(languagePanelHTML([], ["zh-TW"])).toContain("無法取得語言清單");
  });

  test("the setting remains a non-empty list of variant ids", () => {
    for (const bad of [undefined, null, "zh-TW", [], [1, ""], {}]) expect(normalizeLanguages(bad)).toEqual(DEFAULT_LANGUAGES);
  });
});
