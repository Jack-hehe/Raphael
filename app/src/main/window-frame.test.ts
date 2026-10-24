/// <reference types="node" />
// The custom window frame is built from index.html and styles.css. There is no browser here, so
// these tests read the two files and check the rules that keep the frame usable.

import { describe, expect, test } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";

const styles = readFileSync(path.join(import.meta.dirname, "styles.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
const html = readFileSync(path.join(import.meta.dirname, "..", "..", "index.html"), "utf8");

/** The declarations of the rules that name `selector`, in the order they are written. */
function declarations(selector: string): Array<[string, string]> {
  const found: Array<[string, string]> = [];
  for (const [, selectors, body] of styles.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    if (!selectors.split(",").some((one) => one.trim() === selector)) continue;
    for (const [, property, value] of body.matchAll(/([\w-]+)\s*:\s*([^;]+)/g)) found.push([property, value.trim()]);
  }
  return found;
}

/** The value the last declaration of any of `properties` gives. */
const lastOf = (selector: string, ...properties: string[]): string | undefined =>
  declarations(selector).filter(([property]) => properties.includes(property)).pop()?.[1];

describe("keyboard focus and the window controls", () => {
  test("the window controls come after the page in the document, so that Tab reaches the page first", () => {
    const chrome = html.indexOf('id="window-chrome"');
    expect(chrome).toBeGreaterThan(-1);
    for (const id of ["nav", "main", "ob"]) {
      expect(html.indexOf(`id="${id}"`), id).toBeGreaterThan(-1);
      expect(html.indexOf(`id="${id}"`), id).toBeLessThan(chrome);
    }
  });

  test("the maximize button starts as not pressed: its state is aria-pressed alone", () => {
    expect(html.match(/<button[^>]*data-window-action="maximize"[^>]*>/)?.[0]).toContain('aria-pressed="false"');
  });

  test("every window button shows its focus ring inside itself, where the window edge and resize handles do not cover it", () => {
    const ring = declarations(".window-button:focus-visible");
    expect(ring.map(([property]) => property)).not.toContain("outline");
    expect(lastOf(".window-button:focus-visible", "outline-offset")).toMatch(/^-\d+px$/);
  });

  test("the close button changes glyph color and retains its focus ring without a surface", () => {
    expect(lastOf(".window-close:hover", "background")).toBe("transparent");
    expect(lastOf(".window-close:hover", "color")).toBe("var(--err)");
    expect(lastOf(".window-close:hover:focus-visible", "outline-color")).toBe("var(--focus)");
  });

  test("window controls stay transparent while settings dim the window", () => {
    expect(lastOf(':root[data-window-modal="true"] .window-button:focus-visible:not(:hover)', "background")).toBe("transparent");
  });

  test("the page scrolls a focused element to below the window controls that cover its top", () => {
    const top = lastOf(".main", "scroll-padding", "scroll-padding-block", "scroll-padding-block-start", "scroll-padding-top");
    expect(top).toBeDefined();
    expect(top).toContain("var(--window-controls-height)");
  });
});
