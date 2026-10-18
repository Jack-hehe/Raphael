import { describe, expect, test } from "vitest";
import {
  DEFAULT_LANGUAGES,
  type LanguageGroup,
  chooseLanguageVariant,
  languageButtonHTML,
  normalizeLanguages,
  selectedLanguageGroups,
  toggleTranscriptionLanguage,
  transcriptionButtonHTML,
  transcriptionPanelHTML,
  variantPanelHTML,
} from "./languages";

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

describe("language controls", () => {
  test("shows language groups as chips and variants on a separate button", () => {
    const chips = transcriptionButtonHTML(GROUPS, ["zh-HK", "en-GB"], ' data-k="languages"');
    expect(chips).toContain('class="lchip">中文</span>');
    expect(chips).toContain('class="lchip">English</span>');
    expect(chips).not.toContain("香港");
    expect(chips).not.toContain("English (UK)");
    expect(chips).toContain('data-k="languages"');
    const variants = languageButtonHTML(GROUPS, ["zh-HK", "x-1"], ' data-k="variants"');
    expect(variants).toContain("選擇語言變體");
    expect(variants).not.toContain('class="lchip"');
    expect(variants).toContain("&lt;i&gt;one&lt;/i&gt;");
    expect(variants).not.toContain("<i>one</i>");
  });

  test("transcription choices show all groups without individual variants", () => {
    const html = transcriptionPanelHTML(GROUPS, ["zh-HK", "ja-JP"]);
    expect(html).toContain('data-language="zh" aria-pressed="true"');
    expect(html).toContain('data-language="ja" aria-pressed="true"');
    expect(html).toContain('data-language="en" aria-pressed="false"');
    expect(html).not.toContain('data-variant=');
    expect(html).not.toContain("English (UK)");
  });

  test("marks unavailable additions at five while allowing chosen groups", () => {
    const html = transcriptionPanelHTML(GROUPS, ["zh-TW", "en-US", "ja-JP", "ko-KR", "fr-FR"]);
    expect(html).toContain('data-language="de" aria-pressed="false" aria-disabled="true"');
    expect(html).toContain('data-language="en" aria-pressed="true">');
  });

  test("variants only include selected languages with multiple variants", () => {
    const html = variantPanelHTML(GROUPS, ["en-GB", "zh-HK", "ja-JP"]);
    expect(html.indexOf('data-variant-group="en"')).toBeLessThan(html.indexOf('data-variant-group="zh"'));
    expect(html).toContain('data-variant="zh-HK" aria-checked="true"');
    expect(html).toContain('data-variant="zh-TW" aria-checked="false"');
    expect(html).toContain("台灣用語，例如 軟體、視窗");
    expect(html).not.toContain('data-variant="ja-JP"');
    expect(html).not.toContain('data-variant="x-1"');
  });

  test("explains when the selected languages have no variants", () => {
    expect(variantPanelHTML(GROUPS, ["ja-JP"])).toContain("目前選擇的轉錄語言沒有其他變體");
  });

  test("escapes names from the core in both pickers", () => {
    expect(transcriptionPanelHTML(GROUPS, ["zh-TW"])).toContain("&lt;b&gt;x&lt;/b&gt;");
    expect(variantPanelHTML(GROUPS, ["x-1"])).toContain("&lt;i&gt;one&lt;/i&gt;");
    expect(variantPanelHTML(GROUPS, ["x-1"])).not.toContain("<b>x</b>");
  });

  test("reports an unavailable core language list in both pickers", () => {
    expect(transcriptionPanelHTML(null, ["zh-TW"])).toContain("無法取得語言清單");
    expect(variantPanelHTML(null, ["zh-TW"])).toContain("無法取得語言清單");
  });

  test("keeps variant ids accessible before the language list loads", () => {
    expect(transcriptionButtonHTML([], ["zh-HK", "en-GB"])).toContain("zh-HK");
  });

  test("the setting remains a non-empty list of variant ids", () => {
    for (const bad of [undefined, null, "zh-TW", [], [1, ""], {}]) expect(normalizeLanguages(bad)).toEqual(DEFAULT_LANGUAGES);
  });
});
