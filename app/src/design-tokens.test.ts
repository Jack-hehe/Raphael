import { readFileSync, readdirSync } from "node:fs";
import { join, relative } from "node:path";
import { expect, test } from "vitest";

const source = __dirname;
const cssFiles = (directory: string): string[] => readdirSync(directory, { withFileTypes: true }).flatMap(entry =>
  entry.isDirectory() ? cssFiles(join(directory, entry.name)) : entry.name.endsWith(".css") ? [join(directory, entry.name)] : []);

// Frozen exceptions: parallel-owned controls/run rows and the separate native overlay.
// Remove entries as those owners adopt tokens; never add entries or increase counts.
const legacy: Record<string, number> = {
  "main/agent.css | .page-agent .btn.ra-tool | border-radius: 50%": 1,
  "main/agent.css | .page-agent .btn.cmp-voice | border-radius: 50%": 1,
  "main/agent.css | .composer .btn.cmp-send | border-radius: 50%": 1,
  "main/run-analytics.css | .run-bars small,.run-span small,.run-row small,.run-whole small,.run-window small | font-size: 11px": 1,
  "main/run-analytics.css | .run-row-copy strong | font-size: 13px": 1,
  "main/run-analytics.css | .run-row-numbers | font-size: 12px": 1,
  "main/run-analytics.css | .run-row-copy>.run-model | font-size: 11px": 1,
  "main/run-analytics.css | .run-card | border-radius: 10px": 1,
  "main/run-analytics.css | .run-list + .ua-note | font-size: 11px": 1,
  "overlay/overlay.css | #picture | border-radius: 50%": 1,
  "overlay/overlay.css | .progress, .sub-line | font: 600 14px/1.45 var(--zh-sub)": 1,
  "overlay/overlay.css | .ask-btn | font: 700 15px/1 var(--zh-sub)": 1,
  "overlay/overlay.css | .ask-fill | border-radius: 2px": 1,
  'overlay/overlay.css | .ask[data-tier="T3"] .ask-yes::after | border-radius: 2px': 1,
};

test("type and radius literals outside styles.css cannot grow beyond the frozen exceptions", () => {
  const found: Record<string, number> = {};
  for (const path of cssFiles(source)) {
    const file = relative(source, path).replace(/\\/g, "/");
    if (file === "main/styles.css") continue;
    const css = readFileSync(path, "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
    for (const [, selectors, body] of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      const selector = selectors.trim().replace(/\s+/g, " ");
      for (const [, property, raw] of body.matchAll(/(?<![\w-])(font-size|font|border-(?:(?:top|bottom)-(?:left|right)-)?radius)\s*:\s*([^;{}]+)/g)) {
        const value = raw.trim();
        // Font shorthand may contain a numeric weight and a separate line height.
        const size = property === "font" ? value.split("/")[0] : value;
        if (!/\d(?:px|em|rem|%|vw|vh|pt)(?![\w])/.test(size.replace(/var\([^)]*\)/g, ""))) continue;
        const key = `${file} | ${selector} | ${property}: ${value}`;
        found[key] = (found[key] ?? 0) + 1;
      }
    }
  }
  for (const [key, count] of Object.entries(found)) expect(count, key).toBeLessThanOrEqual(legacy[key] ?? 0);
});

test("the shared scale has six type sizes, six spaces and three radius sizes", () => {
  const css = readFileSync(join(source, "main/styles.css"), "utf8");
  const values = (prefix: string) => [...css.matchAll(new RegExp(`--${prefix}-[\\w-]+:\\s*([\\d.]+)px;`, "g"))].map(match => Number(match[1]));
  expect(values("fs")).toEqual([12, 13, 14, 15, 18, 22]);
  expect(values("sp")).toEqual([4, 8, 12, 16, 24, 32]);
  expect([...new Set(values("r"))]).toEqual([8, 12, 999]);
});

test("the settings dialog cannot use a pill radius that clips its navigation and close control", () => {
  const css = readFileSync(join(source, "main/styles.css"), "utf8");
  const modal = css.match(/^\.modal \{([^}]+)\}/m)![1];
  expect(modal).toContain("border-radius: var(--r-lg)");
});
