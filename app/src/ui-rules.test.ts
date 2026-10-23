// The rules of docs/ui.md that a machine can check. They read the sources, so a new violation fails
// here before anyone has to see it. The allowlists are frozen: they may shrink, never grow.

import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { describe, expect, test } from "vitest";
import { en } from "./main/i18n/en";
import { ja } from "./main/i18n/ja";
import { zhTW } from "./main/i18n/zh-TW";
import { actionRow, btn } from "./main/ui";

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

describe("action rows", () => {
  test("primary is last, Cancel immediately precedes it, and destruction is first", () => {
    const html = actionRow({
      primary: { label: "Save", attrs: ' data-action="save" disabled' },
      secondary: btn("Cancel", "", ' data-action="cancel"'),
      destructive: { label: "Delete", attrs: ' data-action="delete"' },
    });
    expect([...html.matchAll(/data-action="([^"]+)"/g)].map((m) => m[1])).toEqual(["delete", "cancel", "save"]);
    expect(html).toMatch(/class="action-destructive"><button[^>]*class="btn danger"/);
    expect(html).toMatch(/class="btn pri" data-action="save" disabled>Save<\/button><\/div>$/);
  });

  test("a delete confirmation separates danger on the left from Cancel on the right", () => {
    const html = actionRow({ secondary: btn("Cancel"), destructive: { label: "Delete" } });
    expect(html).toMatch(/Delete<\/button><\/span><button[^>]*>Cancel<\/button><\/div>$/);
    expect(html).not.toContain("btn pri");
  });

  test("shared alignment, destructive separation and sticky footers use the app's tokens", () => {
    const css = read(join(MAIN, "styles.css"));
    expect(css).toMatch(/\.action-row\s*\{[^}]*justify-content:\s*flex-end/);
    expect(css).toMatch(/\.action-destructive\s*\{[^}]*margin-inline-end:\s*auto/);
    expect(css).toMatch(/\.action-row\.is-sticky\s*\{[^}]*position:\s*sticky[^}]*bottom:\s*0[^}]*background:\s*var\(--panel\)/);
  });

  test("form footers use the shared row instead of hand-written button containers", () => {
    // Provider/model-picker work is owned by another branch; gold approval cards keep their product order.
    const rows = /<(?:div|span) class="(?:mb-dlg-act|mb-ract|av-crop-act|cs-acts|cs-device-acts|add-acts|svc-acts|mem-edit-actions|addword-actions|rules-acts)"/;
    expect(sources.filter((f) => !["providers.ts", "model-picker.ts"].includes(rel(f)) && rows.test(read(f))).map(rel)).toEqual([]);
    for (const file of ["member-dialogs.ts", "member-details.ts", "avatar-settings.ts", "connections.ts", "services.ts", "messaging.ts", "memory-view.ts", "pages/dictionary.ts", "setup-rows.ts", "onboarding/mount.ts", "safety-rules.ts", "local-models.ts"]) {
      expect(read(join(MAIN, file)), file).toContain("actionRow(");
    }
  });
});

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

  test("no clickable text: the old icon-btn class and the link look are gone", () => {
    const all = [...sources, ...i18nFiles, ...mainFiles.filter((f) => f.endsWith(".css"))].map(read).join("\n");
    expect(all).not.toMatch(/icon-btn|class="link"|class="quiet"/);
  });

  // A text button has a border; an icon-only button (trash, ×, chevron, search, settings, sliders)
  // has none, only a fill under the pointer and the focus ring. The rule changed from "every button
  // is bordered": the border is what tells a button with words in it.
  test("icon-only buttons are borderless: a fill under the pointer and a focus ring, never an edge", () => {
    const strip = (css: string): string => css.replace(/\/\*[\s\S]*?\*\//g, "");
    const styles = strip(read(join(MAIN, "styles.css")));
    const base = /(?:^|\n)\.btn\.icon\s*\{([^}]*)\}/.exec(styles)?.[1] ?? "";
    expect(base).toMatch(/border-color:\s*transparent/);
    expect(base).toMatch(/background:\s*transparent/);
    expect(styles).toMatch(/\.btn\.icon:hover:not\(:disabled\)\s*\{[^}]*background:\s*var\(--hover\)[^}]*border-color:\s*transparent/);
    expect(styles).toMatch(/\.btn\.icon:focus-visible\s*\{[^}]*outline:\s*2px solid var\(--focus\)/);
    // No other rule gives one an edge back. The exceptions: a primary one is filled with the accent
    // (its edge is its fill), and one that grows words while it asks again (確定清除？) is a text button.
    const bad: string[] = [];
    for (const file of mainFiles.filter((f) => f.endsWith(".css"))) {
      for (const [, selector, body] of strip(read(file)).matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
        const icon = selector.split(",").filter((s) => /\.btn(\.[\w-]+)*\.icon\b/.test(s) && !/\.pri\b|\.is-confirm\b/.test(s));
        if (!icon.length) continue;
        const edge = /(^|;)\s*border(-top|-right|-bottom|-left)?(-color)?\s*:\s*(?!\s|transparent|0\b|none)[^;]+/.exec(body);
        if (edge) bad.push(`${rel(file)}: ${icon.join(",").trim()} { ${edge[0].trim()} }`);
      }
    }
    expect(bad).toEqual([]);
  });

  test("the icon-only buttons are made by iconButton / iconBtn, which say what they do in their label and tooltip", () => {
    const ui = read(join(MAIN, "ui.ts"));
    expect(ui).toMatch(/export const iconButton = [\s\S]*?class="btn icon\$\{[\s\S]*?aria-label="\$\{esc\(label\)\}" title="\$\{esc\(label\)\}"/);
    // nothing else writes the class by hand
    const byHand = sources.filter((f) => rel(f) !== "ui.ts" && /class="btn icon/.test(read(f))).map(rel);
    expect(byHand).toEqual([]);
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
    const bad = sources.filter((f) => !rel(f).startsWith("onboarding/") && /["'`]onboarding\./.test(read(f))).map(rel);
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

describe("icons", () => {
  // The icons are drawn on a 16 x 16 grid with a 1.5 stroke (ui.ts ICON): at 16 px one edge of each
  // straight line falls on a whole pixel; at 12 to 15 px none does and they blur. So an icon is shown
  // at 16 px, or 20 where a larger one is meant, and keeps its stroke. The window's own minimise,
  // maximise and close are Windows' caption glyphs, drawn for 12 px: not icons of the set.
  const SIZES = new Set(["16px", "20px"]);
  const NOT_ICONS = new Set([".window-button svg"]);
  const strip = (css: string): string => css.replace(/\/\*[\s\S]*?\*\//g, "");
  const cssFiles = [...mainFiles, ...walk(join(SRC, "overlay"))].filter((f) => f.endsWith(".css"));
  const iconRules = (): { file: string; selector: string; body: string }[] =>
    cssFiles.flatMap((file) =>
      [...strip(read(file)).matchAll(/([^{}]+)\{([^{}]*)\}/g)].flatMap(([, selectors, body]) =>
        selectors
          .split(",")
          .map((s) => s.trim())
          .filter((s) => /(\.i|\bsvg)(?![\w-])/.test(s) && !NOT_ICONS.has(s))
          .map((selector) => ({ file: rel(file), selector, body })),
      ),
    );

  test("an icon is shown at 16 or 20 px, never 12 to 15", () => {
    const bad = iconRules().flatMap(({ file, selector, body }) =>
      [...body.matchAll(/(?:^|;)\s*(width|height)\s*:\s*([^;]+)/g)]
        .filter(([, , value]) => !SIZES.has(value.trim()))
        .map(([, prop, value]) => `${file}: ${selector} { ${prop}: ${value.trim()} }`),
    );
    expect(iconRules().length).toBeGreaterThan(10);
    expect(bad).toEqual([]);
  });

  test("an icon keeps the 1.5 stroke it is drawn for", () => {
    const bad = iconRules()
      .filter(({ body }) => [...body.matchAll(/stroke-width\s*:\s*([^;]+)/g)].some(([, value]) => value.trim() !== "1.5"))
      .map(({ file, selector }) => `${file}: ${selector}`);
    expect(bad).toEqual([]);
  });

  test("no page keeps a copy of an icon: every 16 x 16 drawing comes from ui.ts", () => {
    const copies = sources.filter((f) => rel(f) !== "ui.ts" && read(f).includes('viewBox="0 0 16 16"')).map(rel);
    expect(copies).toEqual([]);
  });
});
