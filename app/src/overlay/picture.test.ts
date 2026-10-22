import { afterEach, describe, expect, test, vi } from "vitest";
import { AP, Emblem, PICTURE_RING_REST, VA, pictureRect, pictureRingAlpha, pictureScale, pictureStyle } from "./emblem";
import { pictureOf } from "./overlay";

/** A 2D context that only records the names of what is drawn. */
function recording() {
  const calls: string[] = [];
  const ctx = new Proxy({} as Record<string, unknown>, {
    get: (target, prop: string) =>
      prop in target ? target[prop] : () => (calls.push(prop), prop.startsWith("create") ? { addColorStop: () => {} } : undefined),
    set: (target, prop: string, value) => ((target[prop] = value), true),
  }) as unknown as CanvasRenderingContext2D;
  const canvas = { style: {}, width: 0, height: 0, isConnected: true, getClientRects: () => [{}], getContext: () => ctx } as unknown as HTMLCanvasElement;
  return { canvas, calls };
}

const IMAGE = { complete: true, naturalWidth: 200, naturalHeight: 100 } as HTMLImageElement;
const CROP = { x: 0.5, y: 0.5, zoom: 1 };
const layerBox = () => ({ hidden: true, style: {} as Record<string, string> });

describe("a picture that may move, in the overlay", () => {
  afterEach(() => vi.unstubAllGlobals());

  test("the picture itself is shown over the canvas, which draws only the ring, so that a GIF or WebP plays", () => {
    const { canvas, calls } = recording();
    const layer = layerBox();
    const e = new Emblem(canvas, 150, { visible: true });
    e.setPicture(IMAGE, CROP, layer as unknown as HTMLElement);
    e.draw();
    expect(calls).not.toContain("drawImage");
    expect(calls).toContain("stroke"); // the thin ring
    expect(layer.hidden).toBe(false);
    expect(layer.style.transform).toBe("scale(1)");
    expect(layer.style.opacity).toBe("1");
    // it grows in and shrinks away with the circle the canvas would have drawn
    e.vanish();
    e.step(VA.dur / 2);
    e.draw();
    expect(Number(/scale\(([\d.]+)\)/.exec(layer.style.transform)![1])).toBeCloseTo(pictureScale("vanish", VA.dur / 2), 2);
    e.step(VA.dur);
    e.draw();
    expect(e.phase).toBe("hidden");
    expect(layer.hidden).toBe(true);
    // the emblem again: the picture goes
    const again = layerBox();
    e.appear();
    e.setPicture(IMAGE, CROP, again as unknown as HTMLElement);
    e.step(AP.dur / 2);
    e.draw();
    expect(again.hidden).toBe(false);
    e.setPicture(null);
    e.draw();
    expect(again.hidden).toBe(true);
    e.dispose();
  });

  test("with reduced motion the canvas draws the picture's first frame and the moving picture stays hidden", () => {
    vi.stubGlobal("window", { matchMedia: () => ({ matches: true }), devicePixelRatio: 1 });
    const { canvas, calls } = recording();
    const layer = layerBox();
    const e = new Emblem(canvas, 150, { visible: true });
    e.setPicture(IMAGE, CROP, layer as unknown as HTMLElement);
    e.draw();
    expect(calls).toContain("drawImage");
    expect(layer.hidden).toBe(true);
    e.dispose();
  });

  test("the moving picture is cut as the main window cuts it", () => {
    expect(pictureStyle({ x: 1 / 3, y: 0, zoom: 1.23456 })).toBe("object-position:33.3% 0%;transform-origin:33.3% 0%;transform:scale(1.235)");
  });
});

describe("the person's picture in the emblem's place", () => {
  test("it is cut as the main window cuts it: covering the circle, enlarged about the chosen point", () => {
    // a wide picture, at the middle, not enlarged: its height fills the circle, its sides hang out evenly
    expect(pictureRect(200, 100, 100, { x: 0.5, y: 0.5, zoom: 1 })).toEqual({ x: -50, y: 0, w: 200, h: 100 });
    // at its left edge
    expect(pictureRect(200, 100, 100, { x: 0, y: 0.5, zoom: 1 })).toEqual({ x: 0, y: 0, w: 200, h: 100 });
    // enlarged twice about its middle
    expect(pictureRect(100, 100, 100, { x: 0.5, y: 0.5, zoom: 2 })).toEqual({ x: -50, y: -50, w: 200, h: 200 });
  });

  test("it grows in and shrinks away where the emblem appears and vanishes", () => {
    expect(pictureScale("hidden", 0)).toBe(0);
    expect(pictureScale("appear", 0)).toBe(0);
    expect(pictureScale("appear", AP.dur)).toBe(1);
    expect(pictureScale("appear", AP.dur / 2)).toBeGreaterThan(0.5);
    expect(pictureScale("vanish", 0)).toBe(1);
    expect(pictureScale("vanish", VA.dur)).toBe(0);
    expect(pictureScale("listening", 3)).toBe(1);
  });

  test("while listening, the thin ring around it brightens with the voice; otherwise it rests", () => {
    expect(pictureRingAlpha(0.05, true)).toBe(PICTURE_RING_REST);
    expect(pictureRingAlpha(0.4, true)).toBeGreaterThan(PICTURE_RING_REST);
    expect(pictureRingAlpha(1, true)).toBe(1);
    expect(pictureRingAlpha(1, false)).toBe(PICTURE_RING_REST);
  });

  test("only a data URL of the four kinds is drawn, its crop kept in range", () => {
    const url = "data:image/gif;base64,R0lGODlh";
    expect(pictureOf({ url, crop: { x: 2, y: -1, zoom: 9 } })).toEqual({ url, crop: { x: 1, y: 0, zoom: 4 } });
    expect(pictureOf({ url, crop: null })).toEqual({ url, crop: { x: 0.5, y: 0.5, zoom: 1 } });
    for (const bad of [null, "x", { url: "https://x/a.png" }, { url: "data:image/svg+xml;base64,PHN2Zz4=" }]) {
      expect(pictureOf(bad)).toBeNull();
    }
  });
});
