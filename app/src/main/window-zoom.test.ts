// @vitest-environment happy-dom
import { describe, expect, test } from "vitest";
import { ZOOM_LEVELS, installWindowZoom, nextZoom, zoomAction } from "./window-zoom";

const key = (code: string, init: Partial<KeyboardEventInit> = {}) =>
  new KeyboardEvent("keydown", { code, ctrlKey: true, cancelable: true, ...init });

function memory(initial?: string) {
  const values = new Map<string, string>(initial === undefined ? [] : [["raphael.zoom", initial]]);
  return { getItem: (k: string) => values.get(k) ?? null, setItem: (k: string, v: string) => void values.set(k, v), values };
}

describe("window zoom", () => {
  test("three steps either way of 100%, and no further", () => {
    expect(ZOOM_LEVELS).toEqual([0.7, 0.8, 0.9, 1, 1.1, 1.2, 1.3]);
    expect(nextZoom(1, "in")).toBe(1.1);
    expect(nextZoom(1.3, "in")).toBe(1.3);
    expect(nextZoom(0.7, "out")).toBe(0.7);
    expect(nextZoom(1.2, "reset")).toBe(1);
    // A level saved by another version snaps to the nearest step before moving.
    expect(nextZoom(1.25, "in")).toBe(1.3);
    expect(nextZoom(4, "out")).toBe(1.2);
  });

  test("Ctrl with plus, minus or zero on either keyboard row", () => {
    expect(zoomAction(key("Equal"))).toBe("in");
    expect(zoomAction(key("NumpadAdd"))).toBe("in");
    expect(zoomAction(key("Minus"))).toBe("out");
    expect(zoomAction(key("NumpadSubtract"))).toBe("out");
    expect(zoomAction(key("Digit0"))).toBe("reset");
    expect(zoomAction(key("Equal", { ctrlKey: false }))).toBeNull();
    expect(zoomAction(key("Equal", { altKey: true }))).toBeNull();
    expect(zoomAction(key("KeyA"))).toBeNull();
  });

  test("keys move the zoom within the limits, stop the browser's own zoom and remember the level", async () => {
    const target = new EventTarget();
    const applied: number[] = [];
    const storage = memory("1.2");
    installWindowZoom(target, async (factor) => { applied.push(factor); }, storage);
    await Promise.resolve();
    expect(applied).toEqual([1.2]);
    for (let i = 0; i < 5; i++) {
      const event = key("Equal");
      target.dispatchEvent(event);
      expect(event.defaultPrevented).toBe(true);
    }
    await Promise.resolve();
    expect(applied[applied.length - 1]).toBe(1.3);
    expect(storage.values.get("raphael.zoom")).toBe("1.3");
    target.dispatchEvent(key("Digit0"));
    await Promise.resolve();
    expect(applied[applied.length - 1]).toBe(1);
  });

  test("a missing or broken saved value starts at 100%", async () => {
    const applied: number[] = [];
    installWindowZoom(new EventTarget(), async (factor) => { applied.push(factor); }, memory("nonsense"));
    await Promise.resolve();
    expect(applied).toEqual([1]);
  });
});
