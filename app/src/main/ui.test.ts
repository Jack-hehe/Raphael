/// <reference types="node" />
import { describe, expect, test } from "vitest";
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { btn, menuPlacement, sel, selText } from "./ui";

describe("popover placement", () => {
  const viewport = { width: 720, height: 480, headerBottom: 32 };

  test("a short menu stays below and right aligned with its trigger", () => {
    expect(menuPlacement({ top: 80, bottom: 112, right: 600 }, { width: 240, height: 120 }, viewport))
      .toEqual({ left: 360, top: 116, maxHeight: 356 });
  });

  test("an upward menu is clamped below the custom header and scrolls instead of covering it", () => {
    const placement = menuPlacement({ top: 300, bottom: 332, right: 600 }, { width: 320, height: 400 }, viewport);
    expect(placement).toEqual({ left: 280, top: 40, maxHeight: 256 });
    expect(placement.top + placement.maxHeight).toBe(296);
  });

  test("a tall menu uses the larger space below its trigger while retaining the bottom gap", () => {
    const placement = menuPlacement({ top: 100, bottom: 132, right: 600 }, { width: 320, height: 600 }, viewport);
    expect(placement).toEqual({ left: 280, top: 136, maxHeight: 336 });
    expect(placement.top + placement.maxHeight).toBe(472);
  });

  test("a menu that fits above stays beside the trigger rather than snapping to the header", () => {
    expect(menuPlacement({ top: 400, bottom: 432, right: 710 }, { width: 200, height: 100 }, viewport))
      .toEqual({ left: 510, top: 296, maxHeight: 356 });
  });

  test("horizontal overflow is clamped while a browser without custom chrome keeps its normal top gap", () => {
    expect(menuPlacement({ top: 100, bottom: 132, right: 900 }, { width: 320, height: 600 }, { ...viewport, headerBottom: 0 }))
      .toEqual({ left: 392, top: 136, maxHeight: 336 });
    expect(menuPlacement({ top: 300, bottom: 332, right: 180 }, { width: 320, height: 400 }, { ...viewport, headerBottom: 0 }))
      .toEqual({ left: 8, top: 8, maxHeight: 288 });
  });
});

describe("dropdowns", () => {
  test("a closed dropdown shows a choice's short name and keeps the whole one in its tooltip", () => {
    const html = sel([{ value: "a", label: "Microphone (Realtek Audio)", short: "Microphone" }], "a", "Mic", ' data-k="mic"');
    expect(html).toContain("<span>Microphone</span>");
    expect(html).toContain('title="Microphone (Realtek Audio)"');
    expect(html).toContain('aria-label="Mic" ');
    expect(html).toContain('aria-haspopup="menu"');
  });

  test("without a short name it shows the label, and a value it does not know as it is", () => {
    expect(selText({ value: "a", label: "Alpha" }, "a")).toEqual({ shown: "Alpha", full: "Alpha" });
    expect(selText(undefined, "x/old")).toEqual({ shown: "x/old", full: "x/old" });
  });
});

describe("the main window's actions", () => {
  const root = import.meta.dirname;
  const read = (file: string): string => readFileSync(file, "utf8");
  const sources = [root, path.join(root, "pages"), path.join(root, "i18n")].flatMap((dir) =>
    readdirSync(dir).filter((f) => /\.(ts|css)$/.test(f) && !f.endsWith(".test.ts")).map((f) => path.join(dir, f)));

  test("are buttons with a shape: no quiet buttons that read as text, and no links that act", () => {
    for (const file of sources) {
      const text = read(file);
      expect(text, file).not.toMatch(/["'`][^"'`\n]*\bquiet\b[^"'`\n]*["'`]/);
      // `class=\"link\"` in the catalogs, `class="link"` in the pages, `.link` in the styles
      expect(text, file).not.toMatch(/\.btn\.quiet|class=\\?"link\\?"|\.link\b/);
    }
  });

  test("a button has an edge at rest and a fill when it is pressed", () => {
    expect(btn("變更", "sm")).toBe('<button type="button" class="btn sm">變更</button>');
    const css = read(path.join(root, "styles.css"));
    expect(css).toMatch(/\.btn \{[^}]*border: 1px solid var\(--btn-edge\)/);
    expect(css).toMatch(/\.btn:hover:not\(:disabled\) \{[^}]*background: var\(--hover\)/);
    expect(css).toMatch(/\.btn:active:not\(:disabled\) \{[^}]*background: var\(--selected\)/);
  });
});
