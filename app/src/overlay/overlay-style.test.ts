/// <reference types="node" />
// How the words beside the emblem look and where they stand is written in overlay.css. There is no
// browser here, so these tests read the style sheet, work out its calc()s for each emblem size the
// app offers (device_settings.rs) in the window the app makes for it (overlay.rs), and check that
// every line is written one way, in one place, and that a question's line never covers its buttons.

import { describe, expect, test } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";
import { canvasSize } from "./emblem";

const here = import.meta.dirname;
const rust = (file: string) => readFileSync(path.join(here, "..", "..", "src-tauri", "src", file), "utf8");
const styles = readFileSync(path.join(here, "overlay.css"), "utf8")
  .replace(/\/\*[\s\S]*?\*\//g, "")
  .replace(/@import\s+[^;]+;/g, "");

/** A constant of the Rust shell, as it is written there. */
function rustNumber(file: string, name: string): number {
  const found = rust(file).match(new RegExp(`const ${name}: f64 = ([\\d.]+);`));
  if (!found) throw new Error(`${name} is not in ${file}`);
  return Number(found[1]);
}

/** The emblem sizes of the settings, smallest first. */
const SIZES: number[] = (() => {
  const found = rust("device_settings.rs").match(/pub const EMBLEM_SIZES: \[u32; \d+\] = \[([\d, ]+)\];/);
  if (!found) throw new Error("EMBLEM_SIZES is not in device_settings.rs");
  return found[1].split(",").map(Number).sort((a, b) => a - b);
})();
const WINDOW_WIDTH = rustNumber("overlay.rs", "WINDOW_WIDTH");
const SUBTITLE_ROOM = rustNumber("overlay.rs", "SUBTITLE_ROOM");

/** Every rule as its selectors and declarations, in the order they are written. */
const RULES: Array<{ selectors: string[]; declarations: Array<[string, string]> }> = [
  ...styles.matchAll(/([^{}]+)\{([^{}]*)\}/g),
].map(([, selectors, body]) => ({
  selectors: selectors.split(",").map((one) => one.trim()),
  declarations: [...body.matchAll(/([\w-]+)\s*:\s*([^;]+)/g)].map(([, property, value]) => [property, value.trim()]),
}));

/** The value the last declaration of `property` for exactly `selector` gives. */
function value(selector: string, property: string): string | undefined {
  return RULES.filter((rule) => rule.selectors.includes(selector))
    .flatMap((rule) => rule.declarations)
    .filter(([name]) => name === property)
    .pop()?.[1];
}

// ---------- calc() ----------

/**
 * `expression` (a length of the style sheet: px, vw, var(), calc(), min()) in px, for an emblem of
 * `px` in its window. Enough of CSS for this style sheet, and an error for anything else.
 */
function px(expression: string, emblem: number): number {
  const root = new Map<string, string>(RULES.filter((rule) => rule.selectors.includes(":root")).flatMap((rule) => rule.declarations));
  // overlay.ts sets these from the settings.
  root.set("--e", `${emblem}px`);
  root.set("--c", `${canvasSize(emblem)}px`);
  const tokens = expression.match(/-?\d*\.?\d+(?:px|vw)?|var\(\s*--[\w-]+\s*\)|calc\(|min\(|max\(|[()+\-*/,]/g) ?? [];
  if (tokens.join("").replace(/\s/g, "") !== expression.replace(/\s/g, "")) throw new Error(`cannot read ${expression}`);
  let at = 0;
  const peek = () => tokens[at];
  const take = () => tokens[at++];
  const sum = (): number => {
    let total = product();
    while (peek() === "+" || peek() === "-") total = take() === "+" ? total + product() : total - product();
    return total;
  };
  const product = (): number => {
    let total = term();
    while (peek() === "*" || peek() === "/") total = take() === "*" ? total * term() : total / term();
    return total;
  };
  const list = (): number[] => {
    const values = [sum()];
    while (peek() === ",") {
      take();
      values.push(sum());
    }
    if (take() !== ")") throw new Error(`a bracket is missing in ${expression}`);
    return values;
  };
  const term = (): number => {
    const token = take();
    if (token === undefined) throw new Error(`${expression} ends too soon`);
    if (token === "calc(" || token === "(") return list()[0];
    if (token === "min(") return Math.min(...list());
    if (token === "max(") return Math.max(...list());
    const name = token.match(/^var\(\s*(--[\w-]+)\s*\)$/)?.[1];
    if (name) {
      const defined = root.get(name);
      if (defined === undefined) throw new Error(`${name} is not defined`);
      return px(defined, emblem);
    }
    if (token.endsWith("vw")) return (parseFloat(token) * WINDOW_WIDTH) / 100;
    const number = parseFloat(token);
    if (Number.isNaN(number)) throw new Error(`cannot read ${token} in ${expression}`);
    return number;
  };
  const result = sum();
  if (at !== tokens.length) throw new Error(`cannot read ${expression}`);
  return result;
}

/** The length `property` of `selector` gives, for an emblem of `emblem` px. */
function length(selector: string, property: string, emblem: number): number {
  const written = value(selector, property);
  if (written === undefined) throw new Error(`${selector} has no ${property}`);
  return px(written, emblem);
}

/** Everything about where the words and the buttons stand, from the window's bottom-right corner. */
function stage(emblem: number) {
  const canvas = canvasSize(emblem);
  const lineHeight = 14 * 1.45;
  return {
    window: { width: WINDOW_WIDTH, height: canvas + SUBTITLE_ROOM },
    // The canvas is square and in the corner: the circle is as far from the bottom as from the right.
    circle: { edge: px("var(--margin)", emblem), far: px("var(--margin) + var(--e)", emblem) },
    lineHeight,
    caption: { right: length(".progress", "right", emblem), bottom: length(".progress", "bottom", emblem) },
    line: {
      right: length(".subs", "right", emblem),
      bottom: length(".subs", "bottom", emblem),
      width: length(".subs", "max-width", emblem),
    },
    question: { bottom: length(".subs.asking", "bottom", emblem) },
    buttons: {
      right: length(".ask", "right", emblem),
      bottom: length(".ask", "bottom", emblem),
      top: length(".ask", "bottom", emblem) + px("var(--ask-h)", emblem),
      // two pills, 260 px at most together (overlay.css)
      left: length(".ask", "right", emblem) + 260,
    },
  };
}

test("the reader of the style sheet works", () => {
  expect(px("calc(10px + 2 * 3px)", 150)).toBe(16);
  expect(px("min(380px, calc(100vw - 200px))", 150)).toBe(Math.min(380, WINDOW_WIDTH - 200));
  expect(px("var(--e)", 194)).toBe(194);
  expect(px("var(--margin)", 150)).toBe((canvasSize(150) - 150) / 2);
  expect(() => px("1em", 150)).toThrow();
});

test("the app offers three emblem sizes, and the overlay is checked at each", () => {
  expect(SIZES).toEqual([120, 150, 194]);
});

// ---------- one look ----------

describe("every line is written one way", () => {
  const LOOK = ["color", "font", "font-size", "font-weight", "font-style", "line-height", "letter-spacing", "text-shadow"];
  const ELEMENTS = /\.(progress|subs|sub-line)\b/;

  test("the caption and Raphael's lines share one rule: white, 600 at 14 px, with the caption's outline", () => {
    const shared = RULES.filter((rule) => rule.selectors.includes(".progress") && rule.selectors.includes(".sub-line"));
    expect(shared.length).toBeGreaterThan(0);
    expect(value(".progress", "font")).toBe("600 14px/1.45 var(--zh-sub)");
    expect(value(".sub-line", "font")).toBe(value(".progress", "font"));
    expect(value(".sub-line", "color")).toBe("#fff");
    expect(value(".progress", "color")).toBe("#fff");
    expect(value(".progress", "text-shadow")).toBe(
      "0 0 2px #000, 1px 1px 0 #000, -1px -1px 0 #000, 1px -1px 0 #000, -1px 1px 0 #000",
    );
    expect(value(".sub-line", "text-shadow")).toBe(value(".progress", "text-shadow"));
  });

  test("no other rule changes how a line looks: not an answer, not a question, not the caption", () => {
    for (const rule of RULES) {
      if (!rule.selectors.some((selector) => ELEMENTS.test(selector))) continue;
      const together = rule.selectors.includes(".progress") && rule.selectors.includes(".sub-line");
      for (const [property] of rule.declarations) {
        if (LOOK.includes(property)) expect(together, `${rule.selectors.join(", ")} sets ${property}`).toBe(true);
      }
    }
  });

  test.each([
    ["ja", "var(--ja)"],
    ["en", "var(--ui)"],
  ])("a line in %s is set in that language's font, the caption and the subtitles alike", (language, font) => {
    expect(value(`.progress:lang(${language})`, "font-family")).toBe(font);
    expect(value(`.sub-line:lang(${language})`, "font-family")).toBe(font);
  });

  test("Chinese (and a percentage, which has no language) is set in the Chinese font", () => {
    expect(value(".progress", "font")).toContain("var(--zh-sub)");
  });
});

// ---------- one place ----------

describe.each(SIZES)("beside an emblem of %i px", (emblem) => {
  const at = stage(emblem);

  test("the caption and Raphael's lines stand in one place", () => {
    expect(at.caption.right).toBe(at.line.right);
    expect(at.caption.bottom).toBe(at.line.bottom);
  });

  test("the words stay to the left of the emblem's circle, and beside it", () => {
    expect(at.line.right).toBeGreaterThan(at.circle.far);
    // a line of one row stands between the bottom and the top of the circle, not under it
    expect(at.line.bottom).toBeGreaterThan(at.circle.edge);
    expect(at.line.bottom + at.lineHeight).toBeLessThan(at.circle.far);
  });

  test("a line wraps within the window", () => {
    expect(at.line.right + at.line.width).toBeLessThanOrEqual(at.window.width);
    expect(at.line.width).toBeGreaterThanOrEqual(280);
  });

  test("a long answer has the room of six lines above its place, a question that of four above its buttons", () => {
    expect(Math.floor((at.window.height - at.line.bottom) / at.lineHeight)).toBeGreaterThanOrEqual(6);
    expect(Math.floor((at.window.height - at.question.bottom) / at.lineHeight)).toBeGreaterThanOrEqual(4);
  });

  test("a question rises over its buttons, with air between them", () => {
    expect(at.question.bottom).toBeGreaterThan(at.line.bottom);
    expect(at.question.bottom - at.buttons.top).toBeGreaterThanOrEqual(8);
  });

  test("the buttons stand inside the window, under the words, beside the emblem", () => {
    expect(at.buttons.right).toBe(at.line.right);
    expect(at.buttons.bottom).toBeGreaterThan(0);
    expect(at.buttons.left).toBeLessThanOrEqual(at.window.width);
    expect(at.buttons.top).toBeLessThan(at.question.bottom);
  });
});

test("the room beside the largest emblem holds the longest waiting line notices.rs allows", () => {
  // notices.rs holds every waiting line to about this many px at 14 px.
  const allowed = Number(rust("notices.rs").match(/const CAPTION_ROOM_PX: usize = (\d+);/)?.[1]);
  expect(allowed).toBeGreaterThan(0);
  const largest = stage(SIZES[SIZES.length - 1]);
  expect(largest.window.width - largest.caption.right).toBeGreaterThanOrEqual(allowed);
  expect(largest.line.width).toBeGreaterThanOrEqual(allowed);
});
