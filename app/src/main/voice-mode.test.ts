import { describe, expect, test } from "vitest";
import { FLYER_GAP_PX, FLYER_PX, flightPath, spotTransform } from "./voice-mode";

const box = (left: number, top: number, width: number, height: number) => ({ left, top, width, height });

describe("the voice mode's flight", () => {
  const stage = box(200, 100, 800, 600);
  const composer = box(220, 620, 760, 50);
  const button = box(940, 628, 34, 34);

  test("from the voice button at its size to just above the composer, in the middle, at the empty page's size", () => {
    const { from, to } = flightPath(stage, button, composer);
    expect(from).toEqual({ x: 957 - 200, y: 645 - 100, s: 34 / FLYER_PX });
    expect(to).toEqual({ x: 400, y: 620 - 100 - FLYER_PX / 2 - FLYER_GAP_PX, s: 1 });
    expect(FLYER_PX).toBe(132);
  });

  test("on the empty page it lands on the emblem in the middle, at its size", () => {
    const { to } = flightPath(stage, button, composer, box(534, 200, 132, 132));
    expect(to).toEqual({ x: 534 + 66 - 200, y: 266 - 100, s: 1 });
  });

  test("a place is a translation of the flyer's box and a scale", () => {
    expect(spotTransform({ x: 400, y: 300, s: 1 })).toBe("translate(334px, 234px) scale(1)");
    expect(spotTransform({ x: 66, y: 66, s: 34 / 132 })).toBe("translate(0px, 0px) scale(0.258)");
  });
});
