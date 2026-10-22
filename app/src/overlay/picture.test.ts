import { describe, expect, test } from "vitest";
import { AP, PICTURE_RING_REST, VA, pictureRect, pictureRingAlpha, pictureScale } from "./emblem";
import { pictureOf } from "./overlay";

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
