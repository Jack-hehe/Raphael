/// <reference types="node" />
// The custom window frame is built from index.html and styles.css. There is no browser here, so
// these tests read the two files and check the rules that keep the frame usable.

import { describe, expect, test } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";

const styles = readFileSync(path.join(import.meta.dirname, "styles.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");

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
  test("the page scrolls a focused element to below the window controls that cover its top", () => {
    const top = lastOf(".main", "scroll-padding", "scroll-padding-block", "scroll-padding-block-start", "scroll-padding-top");
    expect(top).toBeDefined();
    expect(top).toContain("var(--window-controls-height)");
  });
});
