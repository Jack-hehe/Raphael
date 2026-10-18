import { describe, expect, test } from "vitest";
import { WARN_FROM, spendVisual, spentShare } from "./spend";

describe("the spend picture", () => {
  test("the share of the limit", () => {
    expect(spentShare(5, 10)).toBe(0.5);
    expect(spentShare(12, 10)).toBe(1.2);
    expect(spentShare(0, 0)).toBe(1);
  });

  test("a bar as wide as the share, and the two amounts", () => {
    const html = spendVisual(3.42, 20);
    expect(html).toContain('style="width:17.1%"');
    expect(html).toContain('class=""');
    expect(html).toContain("US$3.42");
  });

  test("the warning color from 80% on; never wider than full", () => {
    expect(WARN_FROM).toBe(0.8);
    expect(spendVisual(16, 20)).toContain('class="warn"');
    expect(spendVisual(25, 20)).toContain('style="width:100.0%"');
  });
});
