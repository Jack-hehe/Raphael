/// <reference types="node" />
// No coloured frame shows where the pointer clicked or where the person types: a click shows where
// it went, and the caret shows where typing goes. A focus ring is drawn only while Tab moves the
// focus, when focus-keys.ts has set data-keys on <html>. There is no browser here, so these tests
// read every stylesheet under src and check its rules.

import { describe, expect, test } from "vitest";
import { readFileSync, readdirSync } from "node:fs";
import { join, relative } from "node:path";

const SRC = import.meta.dirname;

function cssFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) return cssFiles(full);
    return entry.name.endsWith(".css") ? [full] : [];
  });
}

const strip = (css: string): string => css.replace(/\/\*[\s\S]*?\*\//g, "");
const read = (file: string): string => strip(readFileSync(file, "utf8"));
const rel = (file: string): string => relative(SRC, file).split("\\").join("/");

interface Rule { file: string; selectors: string[]; body: string }

/** Every rule of every stylesheet, the ones inside @media included; @-rule preludes are skipped. */
const rules: Rule[] = cssFiles(SRC).flatMap((file) =>
  [...read(file).matchAll(/([^{}]+)\{([^{}]*)\}/g)].map(([, selectors, body]) => ({
    file: rel(file),
    selectors: selectors.split(",").map((one) => one.trim()),
    body,
  })),
);

/** The declarations of a rule body that draw a frame: an outline, a ring of shadow, an edge, a stroke. */
function rings(body: string): string[] {
  const found: string[] = [];
  for (const [, property, value] of body.matchAll(/([\w-]+)\s*:\s*([^;]+)/g)) {
    const v = value.trim();
    if (/^outline(-style|-width)?$/.test(property) && !/^(none|0(px)?)$/.test(v)) found.push(`${property}: ${v}`);
    else if (property === "box-shadow" && v !== "none") found.push(`${property}: ${v}`);
    else if (/^border(-top|-right|-bottom|-left)?(-color)?$/.test(property)) found.push(`${property}: ${v}`);
    else if (/^stroke(-width)?$/.test(property)) found.push(`${property}: ${v}`);
  }
  return found;
}

const FOCUS = /:focus(-visible|-within)?\b/;
const GATE = /^(:where\(:root\[data-keys\]\)|:root\[data-keys\])\s/;

/**
 * Focus styles that are not a frame and so stay under the pointer too. The composer's shadow
 * deepens a little while it is typed into; its edge does not change colour.
 */
const SOFT = new Map([[".composer:focus-within", /^box-shadow: 0 6px 24px color-mix\(/]]);

describe("focus rings follow the keyboard only", () => {
  test("the stylesheets were found", () => {
    const files = new Set(rules.map((rule) => rule.file));
    for (const file of ["main/styles.css", "main/agent.css", "main/model-picker.css", "overlay/overlay.css"]) {
      expect(files.has(file), file).toBe(true);
    }
  });

  test("every frame drawn on focus is gated on :root[data-keys]", () => {
    const bad: string[] = [];
    for (const rule of rules) {
      const drawn = rings(rule.body);
      if (!drawn.length) continue;
      for (const selector of rule.selectors.filter((one) => FOCUS.test(one) && !GATE.test(one))) {
        const soft = SOFT.get(selector);
        if (soft && drawn.every((one) => soft.test(one))) continue;
        bad.push(`${rule.file}: ${selector} { ${drawn.join("; ")} }`);
      }
    }
    expect(bad).toEqual([]);
  });

  test("the browser's own ring is turned off, and the app's ring comes back under the keyboard alone", () => {
    const styles = rules.filter((rule) => rule.file === "main/styles.css");
    const base = styles.find((rule) => rule.selectors.length === 1 && rule.selectors[0] === ":focus-visible");
    expect(base?.body).toMatch(/outline:\s*none/);
    const keyed = styles.find((rule) => rule.selectors.includes(":where(:root[data-keys]) :focus-visible"));
    expect(keyed?.body).toMatch(/outline:\s*2px solid var\(--focus\)/);
    // The overlay never takes the keyboard's focus, so it has no ring at all.
    const overlay = rules.filter((rule) => rule.file === "overlay/overlay.css");
    expect(overlay.find((rule) => rule.selectors.includes(":focus-visible"))?.body).toMatch(/outline:\s*none/);
  });

  test("an open dropdown, a field typed into and the search pill draw no frame, even under the keyboard", () => {
    const quiet = [
      /^\.sel\[aria-expanded="true"\]$/,
      /\.mp-dd\[aria-expanded="true"\]$/,
      /^(:where\(:root\[data-keys\]\) )?\.field:focus-within$/,
      /^(:where\(:root\[data-keys\]\) )?\.ra-find:focus-within$/,
      /^(:where\(:root\[data-keys\]\) )?\.trybox:focus$/,
      /^(:where\(:root\[data-keys\]\) )?\.mem-(body|title):focus$/,
    ];
    const bad = rules.flatMap((rule) =>
      rule.selectors.filter((one) => quiet.some((q) => q.test(one)) && rings(rule.body).length).map((one) => `${rule.file}: ${one}`),
    );
    expect(bad).toEqual([]);
  });

  test("the composer keeps its edge colour while it is typed into", () => {
    const composer = rules.filter((rule) => rule.selectors.includes(".composer:focus-within"));
    expect(composer.length).toBeGreaterThan(0);
    for (const rule of composer) expect(rule.body).not.toMatch(/border/);
  });
});
