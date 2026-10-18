/// <reference types="node" />
import { afterEach, describe, expect, test } from "vitest";
import { catalogs, getLocale, onLocaleChange, setLocale, t, translatedMessage } from "./index";
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import ts from "typescript";
import { durationParts } from "../stats";
import { DICTATION_MODES } from "../dictation";
import { transcriptionPanelHTML } from "../languages";
import { describeError } from "../format";

afterEach(() => setLocale("zh-TW"));

describe("main window translations", () => {
  test("all locales have exactly the same keys and interpolation parameters", () => {
    const keys = Object.keys(catalogs["zh-TW"]).sort();
    for (const messages of Object.values(catalogs)) {
      expect(Object.keys(messages).sort()).toEqual(keys);
      for (const key of keys) {
        const params = (value: string) => [...value.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();
        expect(params(messages[key as keyof typeof messages])).toEqual(params(catalogs["zh-TW"][key as keyof typeof messages]));
      }
    }
  });

  test("defaults to Traditional Chinese and validates stored locales", () => {
    expect(getLocale()).toBe("zh-TW");
    setLocale("unsupported");
    expect(getLocale()).toBe("zh-TW");
  });

  test("notifies the open window immediately, only when the locale changes", () => {
    const seen: string[] = [];
    const stop = onLocaleChange(() => seen.push(t("nav.settings")));
    setLocale("en");
    setLocale("en");
    setLocale("ja");
    stop();
    setLocale("zh-TW");
    expect(seen).toEqual(["Settings", "設定"]);
  });

  test("refreshes module-level choices, durations and language panels without reimporting", () => {
    setLocale("en");
    expect(DICTATION_MODES[1].label).toBe("Hold to talk");
    expect(durationParts(3660)).toEqual([["1", t("unit.hours")], ["1", t("unit.minutes")]]);
    expect(transcriptionPanelHTML(null, ["zh-TW"])).not.toContain("語言");
    setLocale("ja");
    expect(DICTATION_MODES[1].label).not.toBe("Hold to talk");
    expect(durationParts(3660).flat()).not.toContain(undefined);
  });

  test("keeps user data containing placeholders literal", () => {
    setLocale("en");
    expect(t("dictionary.022", { p0: "{p0}<script>" })).toContain("{p0}<script>");
  });

  test("keeps an existing error translated through repeated locale changes", () => {
    let message = t("onboarding.049");
    for (const locale of ["en", "ja", "zh-TW"] as const) {
      setLocale(locale);
      message = translatedMessage(message)();
      expect(message).toBe(t("onboarding.049"));
    }
    expect(translatedMessage("User data stays literal")()).toBe("User data stays literal");
  });

  test("retranslates nested error copy while preserving literal user data", () => {
    const error = "Raphael's core is not running";
    const errorMessage = translatedMessage(t("dictionary.021", { p0: () => describeError(error) }));
    const word = t("format.010");
    const userMessage = translatedMessage(t("dictionary.022", { p0: word }));
    for (const locale of ["en", "ja"] as const) {
      setLocale(locale);
      expect(errorMessage()).toContain(t("format.010"));
      expect(userMessage()).toContain(word);
    }
  });

  test("a captured notice resolver survives a large dictionary redraw", () => {
    const message = translatedMessage(t("onboarding.049"));
    for (let n = 0; n < 600; n++) t("dictionary.006", { p0: `word-${n}` });
    setLocale("en");
    expect(message()).toBe(t("onboarding.049"));
  });

  test("translates known Rust errors while preserving external diagnostic details", () => {
    const diagnostic = "OS error 5";
    for (const locale of ["en", "ja"] as const) {
      setLocale(locale);
      expect(describeError(catalogs["zh-TW"]["backend.conflictingKeys"])).toBe(t("backend.conflictingKeys"));
      expect(describeError(catalogs["zh-TW"]["backend.save"].replace("{p0}", diagnostic))).toBe(t("backend.save", { p0: diagnostic }));
    }
  });

  test("main-window source has no hard-coded CJK UI literals outside the catalogue", () => {
    const root = path.resolve(import.meta.dirname, "..");
    const files = [root, path.join(root, "pages")].flatMap((dir) =>
      readdirSync(dir).filter((f) => f.endsWith(".ts") && !f.endsWith(".test.ts")).map((f) => path.join(dir, f)),
    );
    const found: string[] = [];
    for (const file of files) {
      const source = readFileSync(file, "utf8");
      const ast = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true);
      function scan(node: ts.Node): void {
        if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node) || ts.isTemplateExpression(node)) {
          const text = ts.isTemplateExpression(node) ? node.head.text + node.templateSpans.map((s) => s.literal.text).join("") : node.text;
          if (/[\u3400-\u9fff\u3040-\u30ff]/.test(text) && !(text.startsWith("《") && text.endsWith("》"))) found.push(`${path.basename(file)}: ${text}`);
        }
        ts.forEachChild(node, scan);
      }
      scan(ast);
    }
    expect(found).toEqual([]);
  });
});
