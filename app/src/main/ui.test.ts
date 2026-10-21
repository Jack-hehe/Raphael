/// <reference types="node" />
import { describe, expect, test } from "vitest";
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { btn, menuPlacement, meter, sel, selText, setMeter } from "./ui";

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

  test("a choice whose label is shortened keeps the whole text for the tooltip", () => {
    const choice = { value: "", label: "Automatic (current: HyperX Cloud II…)", short: "Automatic (HyperX…)", title: "Automatic (current: Microphone (HyperX Cloud II Core Wireless))" };
    expect(selText(choice, "")).toEqual({ shown: "Automatic (HyperX…)", full: choice.title });
    expect(sel([choice], "", "Mic")).toContain(`title="${choice.title}"`);
  });
});

describe("the microphone meter", () => {
  /** Bars that keep which classes they have, as the meter's `<i>` would. */
  const bars = (n: number) =>
    Array.from({ length: n }, () => {
      const on = new Set<string>();
      return { on, classList: { toggle: (name: string, force: boolean) => void (force ? on.add(name) : on.delete(name)) } };
    });
  const lit = (row: ReturnType<typeof bars>) => row.filter((bar) => bar.on.has("on")).length;
  const meterOf = (row: ReturnType<typeof bars>) => ({ querySelectorAll: () => row }) as unknown as Element;

  test("is a row of small bars, fourteen unless it is given more", () => {
    expect(meter()).toBe(`<span class="meter" aria-hidden="true">${"<i></i>".repeat(14)}</span>`);
    expect(meter(46).match(/<i><\/i>/g)).toHaveLength(46);
  });

  test("lights its bars from the left with the loudness, however many it has", () => {
    for (const n of [14, 46]) {
      const row = bars(n);
      setMeter(meterOf(row), 0.5);
      expect(lit(row)).toBe(Math.round(0.525 * n));
      expect(row.slice(0, lit(row)).every((bar) => bar.on.has("on"))).toBe(true);
      setMeter(meterOf(row), 1);
      expect(lit(row)).toBe(n);
      setMeter(meterOf(row), 0);
      expect(lit(row)).toBe(0);
    }
    expect(() => setMeter(null, 1)).not.toThrow();
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
