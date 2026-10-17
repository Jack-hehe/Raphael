import { afterEach, expect, test, vi } from "vitest";
import {
  AP,
  Emblem,
  RATE,
  VA,
  canvasSize,
  drawEmblem,
  kf,
  params,
  tick,
  type Phase,
} from "./emblem";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.resetModules();
});

// ---- numbers from spec §6.3 and §6.4 ----

test("tick quantizes to 24 fps", () => {
  expect(tick(0.05)).toBeCloseTo(1 / 24);
  expect(tick(0.0416)).toBe(0);
});

test("kf interpolates linearly", () => {
  expect(kf([[0, 0], [1, 10]], 0.25)).toBeCloseTo(2.5);
  expect(kf([[0, 0], [1, 10]], 5)).toBe(10);
});

test("appear keyframes", () => {
  expect(params("appear", 0.1, 0)!.ringA).toBe(0); // point stage before 0.15 s
  expect(params("appear", 0.433, 0)!.ringR).toBeCloseTo(0.6);
  expect(params("appear", 0.833, 0)!.glyphR).toBeCloseTo(0.86);
  expect(params("appear", 0.7, 0)!.glyphMix).toBeCloseTo(1);
});

test("vanish keyframes", () => {
  expect(params("vanish", 0.467, 0)!.ringR).toBeCloseTo(0);
  expect(params("vanish", 0.367, 0)!.glyphA).toBeCloseTo(0);
});

test("rotation rate", () => {
  expect(RATE).toBeCloseTo((3.55 * Math.PI) / 180);
});

test("the glyph ring turns at exactly 3.55 degrees a second", () => {
  // (the check above only has two digits of precision, which cannot tell 3.5 from 3.55)
  expect((RATE * 180) / Math.PI).toBeCloseTo(3.55, 10);
});

// ---- the renderer's other numbers and the port itself ----

test("the animations last as long as the spec says", () => {
  expect(AP.dur).toBe(0.833);
  expect(VA.dur).toBe(0.467);
});

test("a hidden emblem draws nothing", () => {
  expect(params("hidden", 0, 0)).toBeNull();
});

test("the white ring is about 1 px when quiet and 7 px when loud (R = 97)", () => {
  const quiet = params("listening", 0, 0)!;
  const loud = params("speaking", 0, 1)!;
  expect(quiet.ringW * 97).toBeCloseTo(1.16, 1);
  expect(loud.ringW * 97).toBeCloseTo(7.18, 1);
});

test("the canvas leaves room for the rays: 1.3 times the diameter", () => {
  expect([120, 150, 194].map(canvasSize)).toEqual([156, 195, 252]);
});

// A canvas context that records every call, so the port can be exercised without a browser.
function recordingContext() {
  const calls: Array<{ name: string; args: unknown[] }> = [];
  const state: Record<string, unknown> = {
    globalAlpha: 1,
    globalCompositeOperation: "source-over",
    filter: "none",
  };
  const ctx = new Proxy(state, {
    get(target, prop: string) {
      if (prop in target) return target[prop];
      if (prop === "createRadialGradient") {
        return (...args: unknown[]) => {
          calls.push({ name: prop, args });
          return { addColorStop: () => {} };
        };
      }
      return (...args: unknown[]) => {
        calls.push({ name: prop, args });
      };
    },
    set(target, prop: string, value) {
      target[prop] = value;
      return true;
    },
  }) as unknown as CanvasRenderingContext2D;
  return { ctx, calls };
}

test("every phase draws with finite numbers, with or without the glyph texture", () => {
  const phases: Phase[] = ["appear", "vanish", "steady", "listening", "thinking", "speaking"];
  for (const phase of phases) {
    const duration = phase === "appear" ? AP.dur : phase === "vanish" ? VA.dur : 1;
    for (let t = 0; t <= duration + 1e-9; t += 1 / 24) {
      const lvl = (t * 7) % 1;
      const { ctx, calls } = recordingContext();
      drawEmblem(ctx, 75, params(phase, tick(t), lvl), { tq: tick(t), rot: 0.4, dim: 1, calm: 1, lvl });
      expect(calls.length, `${phase} at ${t}`).toBeGreaterThan(0);
      for (const call of calls) {
        for (const arg of call.args) {
          if (typeof arg === "number") expect(Number.isFinite(arg), `${phase} ${call.name}`).toBe(true);
        }
      }
    }
  }
});

test("hidden draws nothing at all", () => {
  const { ctx, calls } = recordingContext();
  drawEmblem(ctx, 75, params("hidden", 0, 0), { tq: 0, rot: 0, dim: 1, calm: 1, lvl: 0 });
  expect(calls).toEqual([]);
});

// ---- the glyph texture ----

class FakeImage {
  static outcome: "load" | "error" = "load";
  naturalWidth = 0;
  naturalHeight = 0;
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  set src(_url: string) {
    queueMicrotask(() => {
      if (FakeImage.outcome === "load") {
        this.naturalWidth = 100;
        this.naturalHeight = 100;
        this.onload?.();
      } else {
        this.onerror?.();
      }
    });
  }
}

test("a texture that fails to load gives null and the vector ring is drawn", async () => {
  vi.stubGlobal("Image", FakeImage);
  FakeImage.outcome = "error";
  const emblem = await import("./emblem");
  expect(await emblem.loadGlyphTexture("/private/glyph-ring.png")).toBeNull();

  const { ctx, calls } = recordingContext();
  const p = emblem.params("steady", 0, 0.3)!;
  emblem.drawEmblem(ctx, 75, p, { tq: 0, rot: 0, dim: 1, calm: 1, lvl: 0.3 });
  expect(calls.some((c) => c.name === "drawImage")).toBe(false);
  expect(calls.some((c) => c.name === "setLineDash")).toBe(true); // the dashed vector ring
});

test("a texture that loads is returned and drawn instead of the vector ring", async () => {
  vi.stubGlobal("Image", FakeImage);
  FakeImage.outcome = "load";
  const emblem = await import("./emblem");
  const image = await emblem.loadGlyphTexture("/private/glyph-ring.png");
  expect(image).not.toBeNull();

  const { ctx, calls } = recordingContext();
  const p = emblem.params("steady", 0, 0.3)!;
  emblem.drawEmblem(ctx, 75, p, { tq: 0, rot: 0, dim: 1, calm: 1, lvl: 0.3 });
  const drawn = calls.filter((c) => c.name === "drawImage");
  expect(drawn).toHaveLength(1);
  expect(drawn[0].args[0]).toBe(image);
  expect(calls.some((c) => c.name === "setLineDash")).toBe(false);
});

// ---- the Emblem class ----

function fakeCanvas(): HTMLCanvasElement {
  return {
    style: {},
    width: 0,
    height: 0,
    isConnected: true,
    getClientRects: () => [{}],
    getContext: () => null,
  } as unknown as HTMLCanvasElement;
}

test("the canvas is sized from the diameter", () => {
  const canvas = fakeCanvas();
  new Emblem(canvas, 150);
  expect(canvas.style.width).toBe("195px");
  expect(canvas.style.height).toBe("195px");
  expect(canvas.width).toBe(195); // device pixel ratio 1 outside a browser
});

test("a new emblem is hidden", () => {
  expect(new Emblem(fakeCanvas(), 150).phase).toBe("hidden");
});

test("appear plays for 0.833 s and then settles in the mode", () => {
  const e = new Emblem(fakeCanvas(), 150);
  e.set("listening");
  e.appear();
  expect(e.phase).toBe("appear");
  expect(e.appearLeft).toBeCloseTo(0.833);
  e.step(0.8);
  expect(e.phase).toBe("appear");
  expect(e.appearLeft).toBeCloseTo(0.033);
  e.step(0.04);
  expect(e.phase).toBe("listening");
  expect(e.appearLeft).toBe(0);
});

test("vanish plays for 0.467 s and then the emblem is hidden", () => {
  const e = new Emblem(fakeCanvas(), 150, { visible: true });
  expect(e.phase).toBe("steady");
  e.vanish();
  expect(e.phase).toBe("vanish");
  e.step(0.46);
  expect(e.phase).toBe("vanish");
  e.step(0.01);
  expect(e.phase).toBe("hidden");
});

test("vanish does nothing to a hidden emblem", () => {
  const e = new Emblem(fakeCanvas(), 150);
  e.vanish();
  expect(e.phase).toBe("hidden");
});

test("a mode set during the appear animation is used when it ends", () => {
  const e = new Emblem(fakeCanvas(), 150);
  e.appear();
  e.set("speaking");
  expect(e.phase).toBe("appear");
  e.step(0.9);
  expect(e.phase).toBe("speaking");
});

test("set changes the phase of a visible emblem at once", () => {
  const e = new Emblem(fakeCanvas(), 150, { visible: true });
  e.set("thinking");
  expect(e.phase).toBe("thinking");
});

test("the glyph ring turns clockwise at 3.55 degrees a second, six times faster when thinking", () => {
  const steady = new Emblem(fakeCanvas(), 150, { visible: true, rot0: 0 });
  const thinking = new Emblem(fakeCanvas(), 150, { visible: true, rot0: 0 });
  thinking.set("thinking");
  steady.step(1);
  thinking.step(1);
  expect(steady.rot).toBeCloseTo(RATE);
  expect(thinking.rot).toBeCloseTo(6 * RATE);
});

test("the ring follows the level only while listening or speaking", () => {
  const e = new Emblem(fakeCanvas(), 150, { visible: true });
  e.set("listening");
  e.level = 0.8;
  e.step(1 / 24 + 1e-3);
  expect(e.lvl).toBeCloseTo(0.8);

  e.set("thinking");
  e.step(1 / 24);
  expect(e.lvl).toBeCloseTo(0.05); // the quiet base width

  e.set("speaking");
  e.level = 0.5;
  e.step(1 / 24);
  expect(e.lvl).toBeCloseTo(0.5);

  e.set("steady");
  e.step(1 / 24);
  expect(e.lvl).toBeCloseTo(0.05);
});

test("a level outside 0..1 or not a number cannot break the ring", () => {
  const e = new Emblem(fakeCanvas(), 150, { visible: true });
  e.set("listening");
  e.level = 7;
  e.step(1 / 24 + 1e-3);
  expect(e.lvl).toBe(1);
  e.level = Number.NaN;
  e.step(1 / 24);
  expect(e.lvl).toBe(0.05);
});

test("the ring is redrawn on the 24 fps clock only", () => {
  const e = new Emblem(fakeCanvas(), 150, { visible: true });
  expect(e.step(1 / 24 + 1e-3)).toBe(true); // first tick
  expect(e.step(0.01)).toBe(false); // same tick
  expect(e.step(0.01)).toBe(false);
  expect(e.step(1 / 24)).toBe(true); // the next one
});

test("speak makes the ring move for the given seconds and then it rests", () => {
  vi.spyOn(Math, "random").mockReturnValue(0);
  const e = new Emblem(fakeCanvas(), 150, { visible: true });
  e.speak(1);
  let peak = 0;
  for (let i = 0; i < 20; i++) {
    e.step(1 / 24);
    peak = Math.max(peak, e.lvl);
  }
  expect(peak).toBeGreaterThan(0.4);
  for (let i = 0; i < 12; i++) e.step(1 / 24);
  expect(e.lvl).toBeCloseTo(0.05);
});

test("with reduced motion the two animations are skipped and the glyph ring stands still", () => {
  // Windows' "animation effects" switch reaches the page as prefers-reduced-motion.
  vi.stubGlobal("window", { matchMedia: () => ({ matches: true }), devicePixelRatio: 1 });
  const e = new Emblem(fakeCanvas(), 150, { rot0: 0 });
  e.set("listening");
  e.appear();
  expect(e.phase).toBe("listening"); // no appear animation
  e.step(1);
  expect(e.rot).toBe(0);
  e.vanish();
  expect(e.phase).toBe("hidden"); // no vanish animation
});

test("the animation loop runs only while the emblem is on screen", async () => {
  // A browser that hands out animation frames one at a time, on request.
  const frames: FrameRequestCallback[] = [];
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => frames.push(callback));
  let now = performance.now();
  const nextFrame = (): boolean => {
    const callback = frames.shift();
    if (!callback) return false;
    now += 1000 / 60;
    callback(now);
    return true;
  };

  const { ctx } = recordingContext();
  const canvas = { ...fakeCanvas(), getContext: () => ctx } as unknown as HTMLCanvasElement;
  const emblem = await import("./emblem");
  const e = new emblem.Emblem(canvas, 150); // hidden

  // A hidden emblem needs one frame to clear its canvas, then the loop stops by itself.
  expect(nextFrame()).toBe(true);
  expect(frames).toHaveLength(0);

  // Appearing starts the loop again, and it keeps running while the emblem is shown.
  e.appear();
  expect(frames).toHaveLength(1);
  for (let i = 0; i < 90; i++) expect(nextFrame()).toBe(true); // 1.5 s: appeared, now steady
  expect(e.phase).toBe("steady");
  expect(frames).toHaveLength(1);

  // The vanish animation takes 0.467 s; after it one last frame clears the canvas and the loop ends.
  e.vanish();
  let played = 0;
  while (nextFrame()) played++;
  expect(e.phase).toBe("hidden");
  expect(played).toBeGreaterThanOrEqual(28); // 0.467 s at 60 frames a second
  expect(played).toBeLessThan(40);
  expect(frames).toHaveLength(0);
});

test("a canvas that is not on screen does not animate and does not keep the loop busy once hidden", async () => {
  const frames: FrameRequestCallback[] = [];
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => frames.push(callback));
  let now = performance.now();
  const nextFrame = (): boolean => {
    const callback = frames.shift();
    if (!callback) return false;
    now += 1000 / 60;
    callback(now);
    return true;
  };
  const { ctx } = recordingContext();
  const canvas = { ...fakeCanvas(), getContext: () => ctx, getClientRects: () => [] } as unknown as HTMLCanvasElement;
  const emblem = await import("./emblem");
  const e = new emblem.Emblem(canvas, 150); // hidden and display: none
  nextFrame();
  expect(frames).toHaveLength(0);

  e.appear();
  for (let i = 0; i < 120; i++) nextFrame();
  expect(e.phase).toBe("appear"); // the clock does not run while nobody can see the canvas
  expect(frames).toHaveLength(1); // but the emblem is waiting to be seen, so the loop goes on
});

test("while listening only the microphone moves the ring, not a made-up speech", () => {
  vi.spyOn(Math, "random").mockReturnValue(0);
  const e = new Emblem(fakeCanvas(), 150, { visible: true });
  e.set("listening");
  e.level = 0.1;
  e.speak(5);
  for (let i = 0; i < 40; i++) {
    e.step(1 / 24);
    expect(e.lvl).toBeCloseTo(0.1);
  }
});

test("a new size resizes the canvas", () => {
  const canvas = fakeCanvas();
  const e = new Emblem(canvas, 150);
  e.size(194);
  expect(canvas.style.width).toBe("252px");
  expect(canvas.width).toBe(252);
});
