// Conversation geometry stays responsive while type and rows use the shared scale.
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, test } from "vitest";

const read = (name: string): string => readFileSync(join(__dirname, name), "utf8");
const strip = (css: string): string => css.replace(/\/\*[\s\S]*?\*\//g, "");
const agent = strip(read("agent.css"));
const members = strip(read("members.css"));
const both = agent + "\n" + members + "\n" + strip(read("conversations.css"));

/** The block that sets the tokens: its selectors and its declarations. */
const block = /(\.page-agent, \.mb-scrim[^{]*)\{([^{}]*)\}/.exec(agent);

/** `--pa-name: calc(A px + B * var(--pa-u))` as [A, A + B]: the size in a small window and in a maximised one. */
function ends(name: string): [number, number] {
  const m = new RegExp(`--${name}: calc\\(([\\d.]+)px \\+ ([\\d.]+) \\* var\\(--pa-u\\)\\);`).exec(block![2]);
  if (!m) throw new Error(`--${name} is not a fluid token`);
  return [Number(m[1]), Number(m[1]) + Number(m[2])];
}

describe("the page's responsive sizes", () => {
  test("the tokens are on the page and what it opens at the body, never on :root", () => {
    expect(block).not.toBeNull();
    const selectors = block![1].split(",").map((s) => s.trim());
    expect(selectors).toEqual([".page-agent", ".mb-scrim", ".menu.mb-avpop", ".menu.mb-menu", ".menu.mb-toolpop"]);
    expect(/(^|[\s,])(:root|html|body|\*)([\s,{]|$)/.test(block![1])).toBe(false);
  });

  test("the sizes grow from 0 at 960 px wide to the full step at 1920 px, and no further", () => {
    expect(block![2]).toContain("--pa-t: clamp(0px, calc(100vw - 960px), 960px);");
    expect(block![2]).toContain("--pa-u: calc(var(--pa-t) / 960);");
  });

  test("the columns: the members 260 to 400 px, the details 300 to 460 px", () => {
    expect(block![2]).toContain("--pa-col: clamp(260px, 21vw, 400px);");
    expect(block![2]).toContain("--pa-det: clamp(300px, 24vw, 460px);");
    expect(members).toContain(".page.page-agent { position: relative; max-width: none; height: 100%; padding: 0; display: grid; grid-template-columns: var(--pa-col) minmax(0, 1fr);");
    expect(members).toContain('.page.page-agent[data-det="open"]:not(.is-room) { grid-template-columns: var(--pa-col) minmax(0, 1fr) var(--pa-det); }');
  });

  test("the columns and controls retain their responsive geometry", () => {
    const wanted: Record<string, [number, number]> = {
      "pa-tool": [30, 48], "pa-pill-h": [32, 52], "pa-pill-av": [22, 36],
      "pa-colw": [760, 1010], "pa-cmp-btn": [36, 44], "pa-cmp-fs": [15.5, 17],
      "pa-dclose": [34, 48], "pa-det-av": [64, 96],
    };
    for (const [name, [small, big]] of Object.entries(wanted)) expect(ends(name), name).toEqual([small, big]);
  });

  test("every token a rule reads is set, and every token set is read", () => {
    const set = new Set([...block![2].matchAll(/--(pa-[\w-]+):/g)].map((m) => m[1]));
    const used = new Set([...both.matchAll(/var\(--(pa-[\w-]+)\)/g)].map((m) => m[1]));
    expect([...used].filter((name) => !set.has(name))).toEqual([]);
    // --pa-t is read by --pa-u alone
    expect([...set].filter((name) => !used.has(name) && name !== "pa-t")).toEqual([]);
  });

  test("a face follows the sizes by --av, set again with !important over the markup's own, and nothing else is !important", () => {
    const important = [...both.matchAll(/([^;{}]+!important)/g)].map((m) => m[1].trim());
    const strays = important.filter((d) => !/^--av: var\(--pa-[\w-]+\) !important$/.test(d) && !/^animation: none !important$/.test(d) && !/^transition: none !important$/.test(d));
    expect(strays).toEqual([]);
    for (const selector of [".nav.mb-row > .av", ".conversation-trigger .mb-pill .av", ".page-agent .btn.mb-avbtn .av", ".ra-face .av", ".ask-face .av"]) {
      expect(both, selector).toContain(`${selector} { --av: var(--pa-`);
    }
  });

  test("the breakpoints stay: the panel lies over the chat under 1120 px, the column is the dropdown under 860 px", () => {
    expect(members).toMatch(/@media \(max-width: 1119px\) \{[^@]*\.mb-det \{ position: absolute;[^}]*width: var\(--pa-det\)/);
    expect(members).toMatch(/@media \(max-width: 859px\) \{[^@]*\.mb-col \{ display: none; \}[^@]*\.sel\.mb-pick \{ display: inline-flex; \}/);
  });

  test("icons stay at 16 or 20 px: they step at 1280 px wide instead of following the fluid sizes", () => {
    // (ui-rules.test.ts holds every icon rule to 16 or 20 px; these are the steps that rule leaves)
    expect(agent).toMatch(/@media \(min-width: 1280px\) \{[^@]*\.ra-tool \.i \{ width: 20px; height: 20px; \}/);
    expect(members).toMatch(/@media \(min-width: 1280px\) \{[^@]*\.mb-tool \.i[^{]*\{ width: 20px; height: 20px; \}/);
  });

  test("the wide-window block is gone: the fluid sizes cover it", () => {
    expect(agent).not.toMatch(/@media \(min-width: 1440px\)/);
  });

  test("Raphael's thread keeps its bare style: no frame, background, border or shadow on his lines or his face", () => {
    for (const selector of [".ra", ".ra-face", ".ra-body", ".ans"]) {
      const rule = new RegExp(`(^|\\n)${selector.replace(".", "\\.")} \\{([^}]*)\\}`).exec(agent);
      expect(rule, selector).not.toBeNull();
      expect(rule![2], selector).not.toMatch(/(^|[\s;])(background|border|box-shadow|outline|filter)[\w-]*\s*:/);
    }
  });
});
