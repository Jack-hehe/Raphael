import { afterEach, expect, test, vi } from "vitest";
import {
  AP,
  Emblem,
  BOOST_MAX,
  OPERATING_SPEEDUP,
  RATE,
  VA,
  canvasSize,
  drawEmblem,
  boostAmount,
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

// ---- the timing and keyframe numbers of the animation ----

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

test("the appear and vanish animations last 0.833 s and 0.467 s", () => {
  expect(AP.dur).toBe(0.833);
  expect(VA.dur).toBe(0.467);
});

test("a hidden emblem draws nothing", () => {
  expect(params("hidden", 0, 0)).toBeNull();
});

test("the white ring is about 1 px when quiet and 7 px when loud (R = 97)", () => {
  const quiet = params("listening", 0, 0)!;
  const loud = params("listening", 0, 1)!;
  expect(quiet.ringW * 97).toBeCloseTo(1.16, 1);
  expect(loud.ringW * 97).toBeCloseTo(7.18, 1);
});

test("the canvas leaves room for the rays: 1.3 times the diameter", () => {
  expect([120, 150, 194].map(canvasSize)).toEqual([156, 195, 252]);
});

// A canvas context that records every call, so the port can be exercised without a browser.
function recordingContext() {
  const calls: Array<{ name: string; args: unknown[]; alpha?: number; op?: string; style?: string }> = [];
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
        calls.push({ name: prop, args, alpha: target.globalAlpha as number, op: target.globalCompositeOperation as string, style: target.strokeStyle as string });
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
  const phases: Phase[] = ["appear", "vanish", "steady", "listening", "thinking"];
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

test("the light-surface inner ring keeps warm gold edges instead of a neutral white outline", () => {
  const { ctx, calls } = recordingContext();
  const p = { ...params("steady", 0, 0.05)!, glyphA: 0, rays: 0, coreA: 0 };
  drawEmblem(ctx, 64, p, { tq: 1, rot: 0, dim: 1, calm: 0.7, lvl: 0.05, surface: "light" });
  const strokes = calls.filter((call) => call.name === "stroke");
  expect(strokes).toHaveLength(2);
  for (const stroke of strokes) {
    const rgb = stroke.style?.match(/^rgba?\((\d+),(\d+),(\d+)/);
    expect(rgb, "light ring strokes must carry warm color").not.toBeNull();
    expect(Number(rgb![1]) - Number(rgb![3])).toBeGreaterThan(40);
    expect(stroke.op).toBe("source-over");
  }
});

test("the dark and transparent-surface inner ring keeps its original white light", () => {
  const p = { ...params("steady", 0, 0.05)!, glyphA: 0, rays: 0, coreA: 0 };
  for (const surface of ["dark", undefined] as const) {
    const { ctx, calls } = recordingContext();
    drawEmblem(ctx, 64, p, { tq: 1, rot: 0, dim: 1, calm: 0.7, lvl: 0.05, surface });
    const strokes = calls.filter((call) => call.name === "stroke");
    expect(strokes[strokes.length - 1].style).toBe("#ffffff");
  }
});

test("light-surface rays and core use source-over while transparent overlays retain added light", () => {
  const p = { ...params("steady", 0, 0.05)!, glyphA: 0, ringA: 0 };
  for (const surface of ["light", "dark"] as const) {
    const { ctx, calls } = recordingContext();
    drawEmblem(ctx, 64, p, { tq: 1, rot: 0, dim: 1, calm: 0.7, lvl: 0.05, surface });
    const light = calls.filter((call) => call.name === "stroke" || call.name === "fill");
    expect(light.length).toBeGreaterThan(1);
    for (const call of light) expect(call.op).toBe(surface === "light" ? "source-over" : "lighter");
  }
});

test("an app emblem follows its document theme without changing a transparent overlay's default", () => {
  const rec = recordingContext();
  const theme = { theme: "light" };
  const canvas = {
    ...fakeCanvas(),
    getContext: () => rec.ctx,
    ownerDocument: { documentElement: { dataset: theme } },
  } as unknown as HTMLCanvasElement;
  const emblem = new Emblem(canvas, 128, { visible: true });
  emblem.draw();
  expect(rec.calls.some((call) => call.name === "stroke" && call.style === "#ffffff")).toBe(false);
  theme.theme = "dark";
  rec.calls.length = 0;
  emblem.draw();
  expect(rec.calls.some((call) => call.name === "stroke" && call.style === "#ffffff")).toBe(true);
  emblem.dispose();
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
  expect(await emblem.loadGlyphTexture("/brand/glyph-ring.png")).toBeNull();

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
  const image = await emblem.loadGlyphTexture("/brand/glyph-ring.png");
  expect(image).not.toBeNull();

  const { ctx, calls } = recordingContext();
  const p = emblem.params("steady", 0, 0.3)!;
  emblem.drawEmblem(ctx, 75, p, { tq: 0, rot: 0, dim: 1, calm: 1, lvl: 0.3 });
  const drawn = calls.filter((c) => c.name === "drawImage");
  expect(drawn).toHaveLength(1);
  expect(drawn[0].args[0]).toBe(image);
  expect(calls.some((c) => c.name === "setLineDash")).toBe(false);
});

test("light glyphs use the repaired texture as an alpha mask while dark glyphs keep their color", async () => {
  vi.stubGlobal("Image", FakeImage);
  FakeImage.outcome = "load";
  const buffers: Array<{ canvas: object; rec: ReturnType<typeof recordingContext> }> = [];
  vi.stubGlobal("document", { createElement: () => {
    const rec = recordingContext();
    const canvas = { width: 0, height: 0, getContext: () => rec.ctx };
    buffers.push({ canvas, rec });
    return canvas;
  } });
  const emblem = await import("./emblem");
  const image = await emblem.loadGlyphTexture("/brand/glyph-ring.png");
  const p = { ...emblem.params("steady", 0, 0.05)!, ringA: 0, rays: 0, coreA: 0 };
  const options = { tq: 1, rot: 0, dim: 0.8, calm: 0.7, lvl: 0.05 };
  const light = recordingContext();
  emblem.drawEmblem(light.ctx, 64, p, { ...options, surface: "light" });
  const tinted = buffers[0]!;
  const repaired = buffers[1]!;
  expect(light.calls.find((call) => call.name === "drawImage")?.args[0]).toBe(tinted.canvas);
  expect(tinted.rec.calls.find((call) => call.name === "drawImage")?.args[0]).toBe(repaired.canvas);
  expect(repaired.rec.calls.find((call) => call.name === "drawImage")?.args[0]).toBe(image);
  expect(tinted.rec.calls.find((call) => call.name === "fillRect")?.op).toBe("source-in");
  expect(light.calls.find((call) => call.name === "drawImage")?.alpha).toBeCloseTo(0.8);
  const dark = recordingContext();
  emblem.drawEmblem(dark.ctx, 64, p, { ...options, surface: "dark" });
  expect(dark.calls.find((call) => call.name === "drawImage")?.args[0]).toBe(repaired.canvas);
  emblem.drawEmblem(dark.ctx, 64, p, { ...options, surface: "dark", rot: 1.4 });
  expect(buffers).toHaveLength(2);
  expect(repaired.rec.calls.filter((call) => call.name === "clip")).toHaveLength(1);
});

test("light glyph appearance and recording boosts do not add washed-out white fringes", async () => {
  vi.stubGlobal("Image", FakeImage);
  FakeImage.outcome = "load";
  const mask = recordingContext();
  const tinted = { width: 0, height: 0, getContext: () => mask.ctx };
  vi.stubGlobal("document", { createElement: () => tinted });
  const emblem = await import("./emblem");
  await emblem.loadGlyphTexture("/brand/glyph-ring.png");
  for (const boost of [undefined, 0.3]) {
    const rec = recordingContext();
    const p = { ...emblem.params("steady", 0, 0.05)!, glyphGlow: 0.4, glyphMix: 0.5, ringA: 0, rays: 0, coreA: 0 };
    emblem.drawEmblem(rec.ctx, 64, p, { tq: 1, rot: 0, dim: 1, calm: 0.7, lvl: 0.05, surface: "light", boost });
    const images = rec.calls.filter((call) => call.name === "drawImage");
    expect(images.length).toBeGreaterThan(1);
    expect(images.every((call) => call.op === "source-over" && call.args[0] === tinted)).toBe(true);
  }
});

test("the light vector fallback does not introduce the texture's brown contour", () => {
  const rec = recordingContext();
  const p = { ...params("steady", 0, 0.05)!, ringA: 0, rays: 0, coreA: 0 };
  drawEmblem(rec.ctx, 64, p, { tq: 1, rot: 0, dim: 1, calm: 0.7, lvl: 0.05, surface: "light" });
  const strokes = rec.calls.filter((call) => call.name === "stroke");
  expect(strokes.length).toBeGreaterThan(0);
  expect(strokes.some((call) => call.style === "rgba(38,16,2,0.6)")).toBe(false);
});

test("the transparent recording overlay removes dark matte colors while retaining white light", async () => {
  vi.stubGlobal("Image", FakeImage);
  FakeImage.outcome = "load";
  const mask = recordingContext();
  const tinted = { width: 0, height: 0, getContext: () => mask.ctx };
  vi.stubGlobal("document", { createElement: () => tinted });
  const renderer = await import("./emblem");
  const image = await renderer.loadGlyphTexture("/brand/glyph-ring.png");
  const rec = recordingContext();
  const canvas = { ...fakeCanvas(), getContext: () => rec.ctx } as unknown as HTMLCanvasElement;
  const options = { surface: "transparent" as const };
  const emblem = new renderer.Emblem(canvas, 150, options);
  emblem.appear();
  emblem.set("listening");
  emblem.level = 0.7;
  for (let i = 0; i < 30; i++) emblem.step(1 / 24);
  emblem.draw();
  const images = rec.calls.filter((call) => call.name === "drawImage");
  expect(images.length).toBeGreaterThan(1);
  expect(images.every((call) => call.args[0] !== image)).toBe(true);
  expect(mask.calls.some((call) => call.name === "fillRect" && call.op === "source-in")).toBe(true);
  const strokes = rec.calls.filter((call) => call.name === "stroke");
  expect(strokes.some((call) => call.style === "rgba(30,14,0,0.2)")).toBe(false);
  expect(strokes.some((call) => call.style === "#ffffff")).toBe(true);
  expect(images.some((call) => call.op === "lighter")).toBe(true);
  emblem.dispose();
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

test("vanish while already vanishing does not start the animation over", () => {
  const e = new Emblem(fakeCanvas(), 150, { visible: true });
  e.vanish();
  e.step(0.3);
  e.vanish();
  expect(e.phase).toBe("vanish");
  // 0.3 s in already: hidden at 0.467 s, not 0.3 s later than that
  e.step(0.17);
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
  e.set("thinking");
  expect(e.phase).toBe("appear");
  e.step(0.9);
  expect(e.phase).toBe("thinking");
});

test("set changes the phase of a visible emblem at once", () => {
  const e = new Emblem(fakeCanvas(), 150, { visible: true });
  e.set("thinking");
  expect(e.phase).toBe("thinking");
});

test("the glyph ring turns clockwise at 3.55 degrees a second in every mode", () => {
  for (const mode of ["steady", "listening", "thinking"] as const) {
    const e = new Emblem(fakeCanvas(), 150, { visible: true, rot0: 0 });
    e.set(mode);
    e.step(1);
    expect(e.rot, mode).toBeCloseTo(RATE);
  }
});

test("the ring follows the level only while listening", () => {
  const e = new Emblem(fakeCanvas(), 150, { visible: true });
  e.set("listening");
  e.level = 0.8;
  e.step(1 / 24 + 1e-3);
  expect(e.lvl).toBeCloseTo(0.8);

  e.set("thinking");
  e.step(1 / 24);
  expect(e.lvl).toBeCloseTo(0.05); // the quiet base width

  e.set("steady");
  e.level = 0.5;
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

test("frames counts the animation steps, so the page can tell a stalled loop", () => {
  const e = new Emblem(fakeCanvas(), 150);
  expect(e.frames).toBe(0);
  e.step(1 / 60);
  e.step(1 / 60);
  expect(e.frames).toBe(2);
});

// ---- recording: the whole emblem as always, the ring a little brighter with the voice ----

/** The calls that draw the glyph ring: the texture, or the plain ring's strokes at 0.73 R and beyond. */
function glyphCalls(calls: { name: string; args: unknown[] }[], R = 75): number {
  return calls.filter(
    (c) =>
      c.name === "drawImage" ||
      (c.name === "arc" && c.args[0] === 0 && c.args[1] === 0 && (c.args[2] as number) > R * 0.7 && (c.args[2] as number) <= R * 1.01),
  ).length;
}

function frame(phase: Phase, t: number, boost?: number) {
  const { ctx, calls } = recordingContext();
  drawEmblem(ctx, 75, params(phase, t, 0.2), { tq: t, rot: 0.4, dim: 1, calm: 1, lvl: 0.2, boost });
  return calls;
}

test("every boost draws with finite numbers in every phase", () => {
  const phases: Phase[] = ["appear", "vanish", "steady", "listening", "thinking"];
  for (const phase of phases) {
    for (const boost of [undefined, 0, 0.002, 0.1, BOOST_MAX, 5, -1, Number.NaN, Number.POSITIVE_INFINITY]) {
      for (const call of frame(phase, 0.3, boost)) {
        for (const arg of call.args) {
          if (typeof arg === "number") expect(Number.isFinite(arg), `${phase} ${boost} ${call.name}`).toBe(true);
        }
      }
    }
  }
});

test("the ring fades with the glyphs when the emblem vanishes", () => {
  expect(glyphCalls(frame("vanish", 0.167))).toBeGreaterThan(0);
  expect(glyphCalls(frame("vanish", 0.367))).toBe(0);
});

test("the boost is 0 when quiet, grows with the voice and stays modest", () => {
  expect(boostAmount(0)).toBe(0);
  expect(boostAmount(0.05)).toBe(0); // the white ring's rest level
  expect(boostAmount(Number.NaN)).toBe(0);
  expect(boostAmount(-1)).toBe(0);
  let last = 0;
  for (let l = 0; l <= 1.0001; l += 0.01) {
    const a = boostAmount(l);
    expect(a).toBeGreaterThanOrEqual(last);
    last = a;
  }
  expect(boostAmount(0.25)).toBeGreaterThan(0);
  expect(boostAmount(0.5)).toBeGreaterThan(boostAmount(0.25));
  expect(boostAmount(1)).toBeGreaterThan(boostAmount(0.5));
  expect(boostAmount(1)).toBe(BOOST_MAX);
  expect(boostAmount(5)).toBe(BOOST_MAX);
  expect(BOOST_MAX).toBeGreaterThanOrEqual(0.25);
  expect(BOOST_MAX).toBeLessThanOrEqual(0.35);
});

test("while recording the whole ring is drawn at its normal look, and the boost adds a little light on top", () => {
  const plain = frame("listening", 0); // the emblem as always
  expect(glyphCalls(frame("listening", 0, 0))).toBe(glyphCalls(plain));
  expect(plain.some((c) => c.name === "clip")).toBe(false); // one piece, all around
  const loud = frame("listening", 0, 5);
  expect(glyphCalls(loud)).toBeGreaterThan(glyphCalls(plain));
  expect(loud.some((c) => c.name === "clip")).toBe(false);
  // the vector ring's strokes: the normal ones at full strength, the boost added on top and capped
  const glyphStrokes = loud.filter((c, i) => {
    const before = loud[i - 1];
    return c.name === "stroke" && before?.name === "arc" && (before.args[2] as number) > 75 * 0.7;
  });
  const normal = glyphStrokes.filter((c) => c.op !== "lighter");
  const added = glyphStrokes.filter((c) => c.op === "lighter");
  expect(normal.length).toBeGreaterThan(0);
  for (const c of normal) expect(c.alpha).toBe(1);
  expect(added.length).toBeGreaterThan(0);
  for (const c of added) expect(c.alpha).toBeLessThanOrEqual(BOOST_MAX + 1e-9);
});

/** An emblem whose frames are recorded. */
function recordedEmblem() {
  const rec = recordingContext();
  const canvas = { ...fakeCanvas(), getContext: () => rec.ctx } as unknown as HTMLCanvasElement;
  return { canvas, rec };
}

test("a dictation keeps the whole ring from the recording to the end: no sweep, no clip, no flare", () => {
  const { canvas, rec } = recordedEmblem();
  const e = new Emblem(canvas, 150, { surface: "transparent" });
  e.appear();
  e.set("listening");
  for (let i = 0; i < 30; i++) e.step(1 / 24);
  e.set("thinking"); // the key is released
  for (let round = 0; round < 3; round++) {
    for (let i = 0; i < 24; i++) e.step(1 / 24);
    rec.calls.length = 0;
    e.draw();
    expect(e.phase).toBe("thinking");
    expect(glyphCalls(rec.calls), `second ${round}`).toBeGreaterThan(0);
    expect(rec.calls.some((call) => call.name === "clip")).toBe(false);
  }
  e.dispose();
});

test("recording shows the whole emblem, never hides the ring, and the boost follows the voice", () => {
  const { canvas, rec } = recordedEmblem();
  const e = new Emblem(canvas, 150);
  e.appear();
  e.set("listening");
  for (let i = 0; i < 24; i++) e.step(1 / 24); // the appear animation
  e.step(1e-3);
  expect(e.phase).toBe("listening");
  const levels = [0, 0.05, 0.3, 0.9, 0.1, 1, 0.5, 0, 0.7, 0.2];
  for (let i = 0; i < 48; i++) {
    const lvl = levels[i % levels.length];
    e.level = lvl;
    rec.calls.length = 0;
    e.step(1 / 24);
    e.draw();
    expect(e.boost).toBe(boostAmount(lvl));
    expect(glyphCalls(rec.calls), `tick ${i}`).toBeGreaterThan(0);
    expect(rec.calls.some((c) => c.name === "clip")).toBe(false);
  }
});

test("the boost ends as the key is released, and a notice has none", () => {
  const e = new Emblem(fakeCanvas(), 150);
  e.appear();
  e.set("listening");
  e.level = 0.8;
  for (let i = 0; i < 24; i++) e.step(1 / 24);
  e.step(1 / 48); // half way through a tick
  expect(e.boost).toBeGreaterThan(0.2);
  e.set("thinking");
  expect(e.boost).toBe(0); // at once, not at the next tick
  e.step(1 / 24);
  expect(e.boost).toBe(0);
  e.set("steady");
  e.step(1 / 24);
  expect(e.boost).toBe(0);
});

test("Esc while recording vanishes from the whole ring", () => {
  const { canvas, rec } = recordedEmblem();
  const e = new Emblem(canvas, 150);
  e.appear();
  e.set("listening");
  e.level = 0.6;
  for (let i = 0; i < 30; i++) e.step(1 / 24);
  e.vanish();
  e.step(1 / 24);
  rec.calls.length = 0;
  e.draw();
  expect(e.phase).toBe("vanish");
  expect(glyphCalls(rec.calls)).toBeGreaterThan(0);
  expect(rec.calls.some((c) => c.name === "clip")).toBe(false);
});

test("a notice on its own, and the home page's emblem, have no boost", () => {
  const notice = new Emblem(fakeCanvas(), 150);
  notice.appear();
  notice.set("steady");
  notice.step(1 / 24);
  expect(notice.boost).toBe(0);
  const home = new Emblem(fakeCanvas(), 110, { visible: true });
  home.set("steady");
  home.level = 1;
  home.step(1 / 24);
  expect(home.boost).toBe(0);
});

test("the next dictation starts without the last one's boost", () => {
  const e = new Emblem(fakeCanvas(), 150);
  e.appear();
  e.set("listening");
  e.level = 0.9;
  for (let i = 0; i < 30; i++) e.step(1 / 24);
  expect(e.boost).toBeGreaterThan(0);
  e.vanish();
  for (let i = 0; i < 24; i++) e.step(1 / 24);
  expect(e.phase).toBe("hidden");
  e.appear();
  expect(e.boost).toBe(0);
  e.level = 0;
  e.set("listening");
  e.step(0.1);
  expect(e.boost).toBe(0);
});

test("with reduced motion the boost still follows the voice while the ring stands still", () => {
  vi.stubGlobal("window", { matchMedia: () => ({ matches: true }), devicePixelRatio: 1 });
  const e = new Emblem(fakeCanvas(), 150, { rot0: 0 });
  e.appear();
  e.set("listening");
  e.level = 0.5;
  e.step(1 / 24 + 1e-3);
  expect(e.boost).toBe(boostAmount(0.5));
  expect(e.rot).toBe(0);
  e.set("thinking");
  expect(e.boost).toBe(0);
});

test("the glyph ring turns 2.5 times faster while Raphael operates an app", () => {
  const e = new Emblem(fakeCanvas(), 150, { visible: true, rot0: 0 });
  e.set("operating");
  expect(e.phase).toBe("operating");
  e.step(1);
  expect(e.rot).toBeCloseTo(RATE * 2.5);
  expect(OPERATING_SPEEDUP).toBe(2.5);
});

test("operating looks like thinking: the same frame, the same quiet ring", () => {
  expect(params("operating", 0, 0.3)).toEqual(params("thinking", 0, 0.3));
  const e = new Emblem(fakeCanvas(), 150, { visible: true });
  e.level = 0.8;
  e.set("operating");
  e.step(1 / 24);
  expect(e.lvl).toBeCloseTo(0.05);
});

test("the ring already turns fast while the emblem appears into the operating mode", () => {
  const e = new Emblem(fakeCanvas(), 150, { rot0: 0 });
  e.set("operating");
  e.appear();
  e.step(0.5);
  expect(e.phase).toBe("appear");
  expect(e.rot).toBeCloseTo(0.5 * RATE * 2.5);
});

test("with reduced motion the operating ring stands still too", () => {
  vi.stubGlobal("window", { matchMedia: () => ({ matches: true }), devicePixelRatio: 1 });
  const e = new Emblem(fakeCanvas(), 150, { rot0: 0 });
  e.set("operating");
  e.appear();
  expect(e.phase).toBe("operating");
  e.step(1);
  expect(e.rot).toBe(0);
});
