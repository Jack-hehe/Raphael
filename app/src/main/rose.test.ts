import { describe, expect, test } from "vitest";
import { roseGeometry, roseSVG, wedgePath } from "./rose";

describe("the geometry of the petals", () => {
  test("six equal wedges of 60 degrees, from the top, clockwise", () => {
    const rose = roseGeometry([1, 1, 1, 1, 1, 1]);
    expect(rose.petals).toHaveLength(6);
    rose.petals.forEach((petal, i) => {
      expect(petal.start).toBeCloseTo((i * Math.PI) / 3, 10);
      expect(petal.end).toBeCloseTo(((i + 1) * Math.PI) / 3, 10);
    });
    expect(rose.petals[5].end).toBeCloseTo(2 * Math.PI, 10);
  });

  test("the area of a petal is its share: the radius goes with the square root, the biggest reaches the rim", () => {
    const rose = roseGeometry([40, 10, 0, 0, 0, 0]);
    expect(rose.petals[0].radius).toBe(rose.radius);
    expect(rose.petals[1].radius).toBeCloseTo(rose.radius / 2, 10); // a quarter of the share, half the reach
    const area = (r: number): number => r * r;
    expect(area(rose.petals[1].radius) / area(rose.petals[0].radius)).toBeCloseTo(10 / 40, 10);
  });

  test("a kind with nothing has no petal, and nothing at all has none", () => {
    const rose = roseGeometry([5, 0, 3, 0, 0, 0]);
    expect(rose.petals.map((p) => p.radius > 0)).toEqual([true, false, true, false, false, false]);
    expect(rose.petals[1].path).toBe("");
    expect(roseGeometry([0, 0, 0]).petals.every((p) => p.path === "")).toBe(true);
    expect(roseGeometry([]).petals).toEqual([]);
  });

  test("negative, missing and infinite amounts count as nothing", () => {
    const rose = roseGeometry([-4, Number.NaN, Number.POSITIVE_INFINITY, 2]);
    expect(rose.petals.map((p) => p.radius > 0)).toEqual([false, false, false, true]);
  });

  test("a small share is still drawn, at the shortest reach", () => {
    const rose = roseGeometry([10_000, 1, 0, 0, 0, 0]);
    expect(rose.petals[1].radius).toBe(4);
    expect(roseGeometry([10_000, 1], { minRadius: 7 }).petals[1].radius).toBe(7);
  });

  test("the size of the picture sets the centre and the rim, and the guide circles are thirds of the way out", () => {
    const rose = roseGeometry([1, 1], { size: 100 });
    expect(rose.size).toBe(100);
    expect(rose.center).toBe(50);
    expect(rose.radius).toBe(48);
    expect(rose.rings).toEqual([16, 32, 48]);
  });

  test("the wedge path of the first petal", () => {
    const rose = roseGeometry([1, 0, 0, 0, 0, 0]);
    // from the centre (110, 110) straight up to the rim (110, 2), then clockwise to 60 degrees
    expect(rose.petals[0].path).toBe("M110 110L110 2A108 108 0 0 1 203.53 56Z");
  });

  test("the wedge of the second petal begins where the first ended", () => {
    const rose = roseGeometry([0, 1, 0, 0, 0, 0]);
    expect(rose.petals[1].path).toBe("M110 110L203.53 56A108 108 0 0 1 203.53 164Z");
  });

  test("a wedge with no reach is nothing", () => {
    expect(wedgePath(110, 0, 0, 1)).toBe("");
    expect(wedgePath(110, -3, 0, 1)).toBe("");
  });

  test("a wedge wider than a half circle takes the long way round", () => {
    expect(wedgePath(50, 40, 0, (3 * Math.PI) / 2)).toContain("A40 40 0 1 1");
  });

  test("a single petal is the whole circle", () => {
    const rose = roseGeometry([3]);
    expect(rose.petals).toHaveLength(1);
    expect(rose.petals[0].path).toBe("M110 2A108 108 0 1 1 110 218A108 108 0 1 1 110 2Z");
  });
});

describe("the drawing", () => {
  const views = [
    { id: "ai", title: "AI assistants: 54%" },
    { id: "dev", title: "Development tools: 33%" },
    { id: "docs", title: "Documents: 7%" },
  ];

  test("guide circles, a petal for each kind that has something, a title on each, and a label for the whole", () => {
    const svg = roseSVG(roseGeometry([54, 33, 0]), views, "Share of your dictation");
    expect(svg).toMatch(/^<svg class="rose" width="220" height="220" viewBox="0 0 220 220" role="img" aria-label="Share of your dictation">/);
    expect(svg.match(/<circle class="rose-ring"/g)).toHaveLength(3);
    expect(svg.match(/<path class="rose-petal p-/g)).toHaveLength(2);
    expect(svg).toContain('class="rose-petal p-ai"');
    expect(svg).toContain('class="rose-petal p-dev"');
    expect(svg).not.toContain("p-docs");
    expect(svg).toContain("<title>AI assistants: 54%</title>");
  });

  test("words that come from outside go through the escape", () => {
    const svg = roseSVG(roseGeometry([1, 1]), [{ id: 'ai" onload="x', title: '<img src=x onerror=alert(1)>' }, { id: "dev", title: "&" }], '"><script>x</script>');
    expect(svg).not.toContain("<script>");
    expect(svg).not.toContain("<img");
    expect(svg).toContain("&lt;img src=x onerror=alert(1)&gt;");
    expect(svg).toContain("&quot;&gt;&lt;script&gt;x&lt;/script&gt;");
    expect(svg).toContain("<title>&amp;</title>");
    // the id of a kind becomes a class: only letters, digits and dashes get through
    expect(svg).toContain('class="rose-petal p-aionloadx"');
  });

  test("nothing to draw is just the guide circles", () => {
    const svg = roseSVG(roseGeometry([0, 0]), [], "none");
    expect(svg).not.toContain("<path");
    expect(svg.match(/<circle/g)).toHaveLength(3);
  });
});
