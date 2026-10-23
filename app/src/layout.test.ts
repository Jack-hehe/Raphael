// Layout rules that keep every window from scrolling sideways. A real layout needs a browser, so
// these read the style sheets: they catch the patterns that made content wider than its window
// (a grid whose implicit column is as wide as its longest unbroken line, a scroll container
// without a horizontal guard, 100vw inside a container that has a scrollbar).

import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const MAIN_DIR = join(__dirname, "main");
const OVERLAY_DIR = join(__dirname, "overlay");

const sheets = (dir: string): { file: string; css: string }[] =>
  readdirSync(dir)
    .filter((name) => name.endsWith(".css"))
    .map((name) => ({ file: name, css: readFileSync(join(dir, name), "utf8").replace(/\/\*[\s\S]*?\*\//g, "") }));

interface Rule {
  file: string;
  selector: string;
  body: string;
}

/** Every `selector { declarations }` of the sheets, at any depth of @media and @container. */
function rulesOf(files: { file: string; css: string }[]): Rule[] {
  const rules: Rule[] = [];
  for (const { file, css } of files) {
    for (const m of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      const selector = m[1].trim().replace(/\s+/g, " ");
      if (!selector.startsWith("@")) rules.push({ file, selector, body: m[2] });
    }
  }
  return rules;
}

const mainRules = rulesOf(sheets(MAIN_DIR));
const overlayRules = rulesOf(sheets(OVERLAY_DIR));
const ruleFor = (rules: Rule[], selector: string): Rule[] => rules.filter((r) => r.selector === selector);

describe("no sideways scrolling", () => {
  it("the page and every scrolling part of the main window clip sideways overflow", () => {
    for (const selector of [
      ".main",
      ".side",
      ".modal-nav",
      ".modal-body",
      ".menu",
      ".ob-body",
      ".lang-list",
      ".mp-scroll",
      ".mp-ddlist",
      ".ra-scroll",
      ".mb-scroll",
      ".mb-det-scroll",
      ".mb-dlg-body",
    ]) {
      const found = ruleFor(mainRules, selector);
      expect(found.length, selector).toBeGreaterThan(0);
      expect(
        found.some((r) => /overflow-x:\s*(hidden|clip)/.test(r.body) && /overflow-y:\s*auto/.test(r.body)),
        `${selector} scrolls up and down only`,
      ).toBe(true);
    }
    expect(ruleFor(mainRules, "html, body").some((r) => /overflow-x:\s*hidden/.test(r.body))).toBe(true);
  });

  it("the overlay window does not scroll at all", () => {
    expect(ruleFor(overlayRules, "html, body").some((r) => /overflow:\s*hidden/.test(r.body))).toBe(true);
  });

  it("no rule scrolls with the overflow shorthand, which would leave the other axis open", () => {
    for (const r of mainRules) expect(r.body, `${r.file}: ${r.selector}`).not.toMatch(/(^|[;\s])overflow:\s*(auto|scroll)/);
  });

  it("no width is taken from the viewport, which counts the scrollbar", () => {
    for (const r of [...mainRules, ...overlayRules]) {
      expect(r.body, `${r.file}: ${r.selector}`).not.toMatch(/(^|[;\s])(min-|max-)?width:\s*(\d+(\.\d+)?)vw/);
    }
  });

  // `display: grid` alone makes one implicit column as wide as the widest unbroken line in it, so a
  // long nowrap line pushes the whole column past its container. The column must be able to shrink.
  it("every grid either names its columns or has a fixed size", () => {
    const fixed = /(^|[;\s])(width|inset|grid-template-areas):/;
    for (const r of mainRules) {
      if (!/display:\s*(inline-)?grid/.test(r.body)) continue;
      const named = /grid-template-columns:\s*[^;]*(minmax\(0|\d+px|repeat\(|var\()/.test(r.body);
      expect(named || fixed.test(r.body) || /grid-template-rows/.test(r.body), `${r.file}: ${r.selector}`).toBe(true);
    }
  });

  it("the tracks of a grid that follow the content may shrink to nothing", () => {
    for (const r of mainRules) {
      const columns = /grid-template-columns:\s*([^;]*)/.exec(r.body)?.[1];
      if (columns === undefined) continue;
      // 1fr alone means minmax(auto, 1fr): it cannot get narrower than the content.
      expect(columns, `${r.file}: ${r.selector}`).not.toMatch(/(^|\s)1fr(\s|$)/);
    }
  });

  it("long member names, routine schedules and chip buttons truncate or wrap without pushing their row past its width", () => {
    expect(ruleFor(mainRules, ".mb-row-t b:not(:only-child)").some((r) => /max-width:\s*70%/.test(r.body))).toBe(true);
    expect(ruleFor(mainRules, ".nav.mb-chief b").some((r) => /text-overflow:\s*ellipsis/.test(r.body))).toBe(true);
    expect(ruleFor(mainRules, ".mb-rt-main span").some((r) => /text-overflow:\s*ellipsis/.test(r.body))).toBe(true);
    expect(ruleFor(mainRules, ".mb-chip .chip-x").some((r) => /flex:\s*none/.test(r.body))).toBe(true);
    expect(ruleFor(mainRules, ".ra-hi").some((r) => /overflow-wrap:\s*anywhere/.test(r.body))).toBe(true);
  });
});
