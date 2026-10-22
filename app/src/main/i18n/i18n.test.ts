/// <reference types="node" />
import { afterEach, describe, expect, test } from "vitest";
import { catalogs, getLocale, onLocaleChange, setLocale, t, translatedMessage } from "./index";
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import ts from "typescript";
import { durationParts } from "../stats";
import { DICTATION_MODES } from "../dictation";
import { languagePanelHTML } from "../languages";
import { describeError } from "../format";

afterEach(() => setLocale("zh-TW"));

const CJK = /[\u3400-\u9fff\u3040-\u30ff]/;

/** The string literals in `files` (TypeScript sources) that hold Chinese or Japanese text, except 《…》 notices. */
function cjkLiterals(files: string[]): string[] {
  const found: string[] = [];
  for (const file of files) {
    const source = readFileSync(file, "utf8");
    const ast = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true);
    function scan(node: ts.Node): void {
      if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node) || ts.isTemplateExpression(node)) {
        const text = ts.isTemplateExpression(node) ? node.head.text + node.templateSpans.map((s) => s.literal.text).join("") : node.text;
        if (CJK.test(text) && !(text.startsWith("《") && text.endsWith("》"))) found.push(`${path.basename(file)}: ${text}`);
      }
      ts.forEachChild(node, scan);
    }
    scan(ast);
  }
  return found;
}

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
    expect(languagePanelHTML(null, ["zh-TW"])).not.toContain("語言");
    setLocale("ja");
    expect(DICTATION_MODES[1].label).not.toBe("Hold to talk");
    expect(durationParts(3660).flat()).not.toContain(undefined);
  });

  test("keeps user data containing placeholders literal", () => {
    setLocale("en");
    expect(t("dictionary.022", { p0: "{p0}<script>" })).toContain("{p0}<script>");
  });

  test("keeps an existing error translated through repeated locale changes", () => {
    let message = t("common.limitInvalid");
    for (const locale of ["en", "ja", "zh-TW"] as const) {
      setLocale(locale);
      message = translatedMessage(message)();
      expect(message).toBe(t("common.limitInvalid"));
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
    const message = translatedMessage(t("common.limitInvalid"));
    for (let n = 0; n < 600; n++) t("dictionary.006", { p0: `word-${n}` });
    setLocale("en");
    expect(message()).toBe(t("common.limitInvalid"));
  });

  test("translates known Rust errors while preserving external diagnostic details", () => {
    const diagnostic = "OS error 5";
    for (const locale of ["en", "ja"] as const) {
      setLocale(locale);
      expect(describeError(catalogs["zh-TW"]["backend.conflictingKeys"])).toBe(t("backend.conflictingKeys"));
      expect(describeError(catalogs["zh-TW"]["backend.save"].replace("{p0}", diagnostic))).toBe(t("backend.save", { p0: diagnostic }));
    }
  });

  test("every refusal the Rust side sends the main window has words in each language", () => {
    // The shell writes these in Chinese, and device_settings.rs builds the shortcut ones from the
    // name of each shortcut: backend.ts has to know every one, or an English or Japanese window
    // shows Chinese. A word or two in quotes is part of a longer message (開啟, 關閉), and the
    // shortcuts named by their field go to the log only.
    const shell = (file: string): string =>
      readFileSync(path.resolve(import.meta.dirname, "..", "..", "..", "src-tauri", "src", file), "utf8")
        .split("#[cfg(test)]")[0]
        .replace(/^\s*\/\/.*$/gm, "");
    const settings = shell("device_settings.rs");
    const names = [...settings.matchAll(/check_keys\("([^"]+)"/g)].map((m) => m[1]).filter((name) => CJK.test(name));
    const templates = [...settings.matchAll(/format!\("\{what\}([^"]+)"\)/g)].map((m) => m[1]);
    const plain = (source: string): string[] =>
      [...source.matchAll(/"((?:[^"\\\n]|\\.)*)"/g)]
        .map((m) => m[1])
        .filter((text) => CJK.test(text) && !text.includes("{") && text.length > 2 && !names.includes(text));
    const messages = [...names.flatMap((name) => templates.map((rest) => name + rest)), ...plain(settings), ...plain(shell("commands.rs"))];
    expect(names).toHaveLength(3);
    expect(templates).toHaveLength(2);
    expect(messages.length).toBeGreaterThanOrEqual(20);
    for (const locale of ["en", "ja"] as const) {
      setLocale(locale);
      for (const message of messages) expect(describeError(message), message).not.toBe(message);
    }
  });

  test("every key of the catalogue is used by the main window's source", () => {
    // A key built at run time comes from a table of literal keys (VARIANT_NAMES, the `exact` list of
    // backend.ts, ...), so each key in use is written out as a string somewhere.
    const root = path.resolve(import.meta.dirname, "..");
    const catalogues = new Set(["zh-TW.ts", "en.ts", "ja.ts"]);
    const files = [root, path.join(root, "pages"), path.join(root, "i18n")].flatMap((dir) =>
      readdirSync(dir)
        .filter((f) => f.endsWith(".ts") && !f.endsWith(".test.ts") && !(dir.endsWith("i18n") && catalogues.has(f)))
        .map((f) => path.join(dir, f)),
    );
    const written = new Set<string>();
    for (const file of files) {
      const ast = ts.createSourceFile(file, readFileSync(file, "utf8"), ts.ScriptTarget.Latest, true);
      const scan = (node: ts.Node): void => {
        if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) written.add(node.text);
        ts.forEachChild(node, scan);
      };
      scan(ast);
    }
    expect(Object.keys(catalogs["zh-TW"]).filter((key) => !written.has(key))).toEqual([]);
  });

  test("main-window source has no hard-coded CJK UI literals outside the catalogue", () => {
    const root = path.resolve(import.meta.dirname, "..");
    const files = [root, path.join(root, "pages")].flatMap((dir) =>
      readdirSync(dir).filter((f) => f.endsWith(".ts") && !f.endsWith(".test.ts")).map((f) => path.join(dir, f)),
    );
    expect(cjkLiterals(files)).toEqual([]);
  });

  test("overlay source and page have no hard-coded CJK UI text either: Rust sends the words", () => {
    const overlay = path.resolve(import.meta.dirname, "..", "..", "overlay");
    const files = readdirSync(overlay, { recursive: true, encoding: "utf8" })
      .filter((f) => f.endsWith(".ts") && !f.endsWith(".test.ts"))
      .map((f) => path.join(overlay, f));
    expect(files.length).toBeGreaterThan(0);
    expect(cjkLiterals(files)).toEqual([]);
    const page = readFileSync(path.resolve(overlay, "..", "..", "overlay.html"), "utf8").replace(/<!--[\s\S]*?-->/g, "");
    expect(page.split("\n").filter((line) => CJK.test(line))).toEqual([]);
  });
});
