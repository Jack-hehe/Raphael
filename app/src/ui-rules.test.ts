// The rules of docs/ui.md that a machine can check. They read the sources, so a new violation fails
// here before anyone has to see it. The allowlists are frozen: they may shrink, never grow.

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { describe, expect, test } from "vitest";
import { en } from "./main/i18n/en";
import { ja } from "./main/i18n/ja";
import { zhTW } from "./main/i18n/zh-TW";

const SRC = __dirname;
const MAIN = join(SRC, "main");

const walk = (dir: string): string[] =>
  readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? (name === "service-logos" ? [] : walk(path)) : [path];
  });
const rel = (path: string): string => relative(MAIN, path).split(sep).join("/");
const read = (path: string): string => readFileSync(path, "utf8");

const mainFiles = walk(MAIN);
/** Application code of the main window: every .ts that is not a test. */
const sources = mainFiles.filter((f) => f.endsWith(".ts") && !f.endsWith(".test.ts") && !f.includes(`${sep}i18n${sep}`));
const i18nFiles = mainFiles.filter((f) => f.includes(`${sep}i18n${sep}`) && !f.endsWith(".test.ts"));

describe("buttons", () => {
  // Every <button> is one of the shapes of the component inventory (docs/ui.md), or a part of a menu / list / dialog.
  const ALLOWED_CLASS = [
    "btn", "sel", "tg", "tab", "nav", "usage-box", "menu-action", "lang-item", "lm-main", "lm-del", "mp-star", "mp-opt", "mp-dd", "chip-x", "sess",
  ];
  const ALLOWED_ROLE = ["option", "menuitem", "menuitemradio", "radio", "checkbox", "switch", "tab"];
  const classOf = (tag: string): string[] => (/\bclass="([^"]*)"/.exec(tag)?.[1] ?? "").split(/\s+/).filter(Boolean);
  const roleOf = (tag: string): string => /\brole="([^"]*)"/.exec(tag)?.[1] ?? "";

  test("every <button> in the code and the translations carries an allowed class or role", () => {
    const bad: string[] = [];
    for (const file of [...sources, ...i18nFiles]) {
      for (const [raw] of read(file).matchAll(/<button\b[^>]*>/g)) {
        // translations escape their quotes; templates hold ${...}
        const tag = raw.replace(/\\"/g, '"').replace(/\$\{[^}]*\}/g, "");
        const ok = classOf(tag).some((c) => ALLOWED_CLASS.includes(c)) || ALLOWED_ROLE.includes(roleOf(tag));
        if (!ok) bad.push(`${rel(file)}: ${tag}`);
      }
    }
    expect(bad).toEqual([]);
  });

  test("no clickable text: the old borderless icon button and the link look are gone", () => {
    const all = [...sources, ...i18nFiles, ...mainFiles.filter((f) => f.endsWith(".css"))].map(read).join("\n");
    expect(all).not.toMatch(/icon-btn|class="link"|class="quiet"/);
  });
});

describe("colours", () => {
  const stripComments = (css: string): string => css.replace(/\/\*[\s\S]*?\*\//g, "");
  const COLOUR = /#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(/;

  test("no #hex / rgb() in the style sheets outside the token blocks of styles.css", () => {
    const bad: string[] = [];
    for (const file of mainFiles.filter((f) => f.endsWith(".css"))) {
      let css = stripComments(read(file));
      // the token blocks: custom properties only
      if (rel(file) === "styles.css") css = css.replace(/:root(\[data-theme="dark"\])?\s*\{[^}]*\}/g, "");
      css.split("\n").forEach((line, i) => COLOUR.test(line) && bad.push(`${rel(file)}:${i + 1}: ${line.trim().slice(0, 80)}`));
    }
    expect(bad).toEqual([]);
  });

  test("the token block holds colours only as custom properties, brand colours as --brand-*", () => {
    const css = stripComments(read(join(MAIN, "styles.css")));
    const blocks = [...css.matchAll(/:root(?:\[data-theme="dark"\])?\s*\{([^}]*)\}/g)].map((m) => m[1]);
    for (const body of blocks) {
      for (const decl of body.split(";").map((d) => d.trim()).filter(Boolean)) {
        if (COLOUR.test(decl)) expect(decl).toMatch(/^--[a-z0-9-]+:/);
      }
    }
    const brand = [...css.matchAll(/(--brand-[a-z0-9-]+):\s*#/g)].map((m) => m[1]);
    expect(brand).toContain("--brand-telegram");
  });

  test("no colour literals in the main window's code, apart from the service logos", () => {
    const logoFiles = ["service-icons.ts", "brand.ts"];
    const bad = sources
      .filter((f) => !logoFiles.includes(rel(f)))
      .filter((f) => /#[0-9a-fA-F]{6}\b|\brgba?\(/.test(read(f)))
      .map(rel);
    expect(bad).toEqual([]);
  });
});

describe("tabs", () => {
  test("the segmented filter is used by History and Dictionary only", () => {
    const users = sources.filter((f) => /kind: "filter"|\btabs seg\b/.test(read(f))).map(rel).sort();
    expect(users).toEqual(["pages/dictionary.ts", "pages/history.ts", "ui.ts"]);
  });

  test("there is one tab implementation: the old class names are gone", () => {
    const all = [...sources, ...i18nFiles, ...mainFiles.filter((f) => f.endsWith(".css"))].map(read).join("\n");
    expect(all).not.toMatch(/cs-seg|class="mp-tabs?"|\.mp-tab\b|btn seg/);
  });
});

describe("translations", () => {
  test("onboarding.* keys are used by the guide alone", () => {
    const bad = sources.filter((f) => rel(f) !== "onboarding.ts" && /["'`]onboarding\./.test(read(f))).map(rel);
    expect(bad).toEqual([]);
  });

  test("no new numbered keys outside the ones that exist", () => {
    // The numbered keys (page.001) are frozen at 220 (lower it when some are renamed): new strings get names (page.meaning).
    const numbered = (cat: Record<string, string>): number => Object.keys(cat).filter((k) => /\.\d{3}$/.test(k)).length;
    expect(numbered(zhTW)).toBeLessThanOrEqual(220);
  });

  // The strings that still carry markup. Structure belongs in code; this list only shrinks (docs/ui.md, backlog).
  const MARKUP_ALLOWLIST = new Set([
    "languages.007", "languages.008", "main.004", "main.005", "common.or",
    "onboarding.010", "onboarding.013", "onboarding.014", "onboarding.019", "onboarding.035", "onboarding.036", "onboarding.037",
    "onboarding.038", "onboarding.039", "onboarding.040", "onboarding.043", "onboarding.044",
    "dictionary.004", "dictionary.005", "dictionary.008", "dictionary.009", "dictionary.018", "dictionary.019",
    "history.005", "history.006", "history.010", "history.011", "history.013",
    "home.017", "home.025", "home.026", "settings.082", "settings.084", "history.dayEmpty",
  ]);

  test.each([
    ["zh-TW", zhTW],
    ["en", en],
    ["ja", ja],
  ] as const)("%s: only the frozen list of strings contains '<'", (_name, catalog) => {
    const offenders = Object.entries(catalog as Record<string, string>)
      .filter(([, value]) => value.includes("<"))
      .map(([key]) => key)
      .filter((key) => !MARKUP_ALLOWLIST.has(key));
    expect(offenders).toEqual([]);
  });
});
