import { afterEach, expect, test, vi } from "vitest";
import {
  AP,
  Emblem,
  ECHO_MAX,
  REVEAL_FEATHER,
  RATE,
  VA,
  canvasSize,
  drawEmblem,
  echoAlpha,
  kf,
  params,
  revealSlices,
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
  const calls: Array<{ name: string; args: unknown[]; alpha?: number }> = [];
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
        calls.push({ name: prop, args, alpha: target.globalAlpha as number });
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

test("frames counts the animation steps, so the page can tell a stalled loop", () => {
  const e = new Emblem(fakeCanvas(), 150);
  expect(e.frames).toBe(0);
  e.step(1 / 60);
  e.step(1 / 60);
  expect(e.frames).toBe(2);
});

// ---- the glyph ring draws itself in after the key is released ----

const transcribing = (done: number, total: number) => ({ stage: "transcribing" as const, done, total, audioSeconds: 5 });
const DONE = { stage: "done" as const, done: 0, total: 0, audioSeconds: 0 };
const TOP = -Math.PI / 2;

test("the reveal runs clockwise from twelve o'clock over its fraction, with a feathered edge", () => {
  const slices = revealSlices(0.25, 0);
  expect(slices[0][0]).toBeCloseTo(TOP, 12);
  expect(slices[0][2]).toBe(1);
  expect(slices[slices.length - 1][1]).toBeCloseTo(0, 12); // a quarter: up to three o'clock
  // one after the other, no gaps, fading towards the head
  for (let k = 1; k < slices.length; k++) {
    expect(slices[k][0]).toBeCloseTo(slices[k - 1][1], 12);
    expect(slices[k][2]).toBeLessThan(slices[k - 1][2]);
  }
  expect(slices.length).toBeGreaterThan(2);
  expect(slices[slices.length - 1][2]).toBeLessThan(0.3);
  // the feather is only the last few degrees
  expect(slices[0][1] - slices[0][0]).toBeCloseTo(Math.PI / 2 - REVEAL_FEATHER, 12);
});

test("the reveal maps nothing to nothing, the whole to the whole ring, and never outside the circle", () => {
  expect(revealSlices(0, 0)).toEqual([]);
  expect(revealSlices(-1, 0)).toEqual([]);
  expect(revealSlices(1, 0)).toEqual([[TOP, TOP + Math.PI * 2, 1]]);
  expect(revealSlices(2, 0)).toEqual([[TOP, TOP + Math.PI * 2, 1]]);
  expect(revealSlices(0.4, 1)).toEqual([[TOP, TOP + Math.PI * 2, 1]]);
  for (const f of [0.001, 0.01, 0.2, 0.6, 0.95, 0.998]) {
    const slices = revealSlices(f, 0);
    expect(slices[0][0]).toBeCloseTo(TOP, 12);
    expect(slices[slices.length - 1][1]).toBeCloseTo(TOP + Math.PI * 2 * f, 12);
    for (const [from, to, alpha] of slices) {
      expect(to).toBeGreaterThan(from);
      expect(alpha).toBeGreaterThan(0);
      expect(alpha).toBeLessThanOrEqual(1);
    }
  }
});

test("the rest of the ring comes back at `rest` (when a notice takes the emblem)", () => {
  const slices = revealSlices(0.5, 0.4);
  const last = slices[slices.length - 1];
  expect(last[0]).toBeCloseTo(TOP + Math.PI, 12);
  expect(last[1]).toBeCloseTo(TOP + Math.PI * 2, 12);
  expect(last[2]).toBe(0.4);
  for (const [, , alpha] of slices) expect(alpha).toBeGreaterThanOrEqual(0.4);
  expect(revealSlices(0, 0.4)).toEqual([[TOP, TOP + Math.PI * 2, 0.4]]);
});

/** The calls that draw the glyph ring: the texture, or the plain ring's strokes at 0.73 R and beyond. */
function glyphCalls(calls: { name: string; args: unknown[] }[], R = 75): number {
  return calls.filter(
    (c) =>
      c.name === "drawImage" ||
      (c.name === "arc" && c.args[0] === 0 && c.args[1] === 0 && (c.args[2] as number) > R * 0.7 && (c.args[2] as number) <= R * 1.01),
  ).length;
}

function frame(phase: Phase, t: number, reveal?: { fraction: number; rest: number; flash: number; echo: number } | null) {
  const { ctx, calls } = recordingContext();
  drawEmblem(ctx, 75, params(phase, t, 0.2), { tq: t, rot: 0.4, dim: 1, calm: 1, lvl: 0.2, reveal });
  return calls;
}

test("an empty reveal draws no glyph ring at all, but the inner part as usual", () => {
  const empty = frame("listening", 0, { fraction: 0, rest: 0, flash: 0, echo: 0 });
  expect(glyphCalls(empty)).toBe(0);
  expect(empty.some((c) => c.name === "stroke")).toBe(true); // the white ring
  expect(empty.some((c) => c.name === "fill")).toBe(true); // the light in the middle
  expect(glyphCalls(frame("listening", 0))).toBeGreaterThan(0); // without a reveal: the whole ring
});

test("a partial reveal draws the glyph ring clipped to wedges from twelve o'clock, with a head", () => {
  const calls = frame("thinking", 0, { fraction: 0.25, rest: 0, flash: 0, echo: 0 });
  expect(glyphCalls(calls)).toBeGreaterThan(0);
  const wedges = calls.filter((c) => c.name === "arc" && c.args[0] === 0 && c.args[1] === 0 && (c.args[2] as number) > 75 * 1.01);
  expect(wedges[0].args[3]).toBeCloseTo(TOP, 12);
  expect(wedges[wedges.length - 1].args[4]).toBeCloseTo(0, 12);
  expect(calls.filter((c) => c.name === "clip").length).toBe(wedges.length);
  // the head, at three o'clock on the glyphs
  expect(calls.some((c) => c.name === "createRadialGradient" && Math.abs((c.args[0] as number) - 75 * 0.855) < 1)).toBe(true);
});

test("every reveal draws with finite numbers in every phase", () => {
  const phases: Phase[] = ["appear", "vanish", "steady", "listening", "thinking"];
  for (const phase of phases) {
    for (const fraction of [0, 0.001, 0.3, 0.6, 0.95, 1]) {
      for (const rest of [0, 0.5]) {
        const calls = frame(phase, 0.3, { fraction, rest, flash: fraction === 1 ? 1 : 0, echo: rest });
        for (const call of calls) {
          for (const arg of call.args) {
            if (typeof arg === "number") expect(Number.isFinite(arg), `${phase} ${fraction} ${call.name}`).toBe(true);
          }
        }
      }
    }
  }
});

test("the done flare draws the whole ring once more, added on top", () => {
  const plain = glyphCalls(frame("thinking", 0, { fraction: 1, rest: 0, flash: 0, echo: 0 }));
  expect(glyphCalls(frame("thinking", 0, { fraction: 1, rest: 0, flash: 1, echo: 0 }))).toBeGreaterThan(plain);
});

test("the revealed ring fades with the glyphs when the emblem vanishes", () => {
  expect(glyphCalls(frame("vanish", 0.167, { fraction: 1, rest: 0, flash: 0, echo: 0 }))).toBeGreaterThan(0);
  expect(glyphCalls(frame("vanish", 0.367, { fraction: 1, rest: 0, flash: 0, echo: 0 }))).toBe(0);
});

/** An emblem whose frames are recorded. */
function recordedEmblem() {
  const rec = recordingContext();
  const canvas = { ...fakeCanvas(), getContext: () => rec.ctx } as unknown as HTMLCanvasElement;
  return { canvas, rec };
}

test("listening shows only the inner part: no glyph ring, from the appear animation on", () => {
  const { canvas, rec } = recordedEmblem();
  const e = new Emblem(canvas, 150);
  e.appear();
  e.set("listening");
  for (let i = 0; i < 48; i++) {
    e.step(1 / 24);
    e.draw();
  }
  expect(e.phase).toBe("listening");
  expect(e.revealShown).toEqual({ fraction: 0, rest: 0, flash: 0, echo: 0 });
  expect(glyphCalls(rec.calls)).toBe(0);
  expect(rec.calls.some((c) => c.name === "stroke")).toBe(true);
});

test("after the stop the ring draws itself in with the progress, on the 24 fps clock", () => {
  const e = new Emblem(fakeCanvas(), 150);
  e.appear();
  e.set("listening");
  for (let i = 0; i < 24; i++) e.step(1 / 24);
  e.step(1 / 48); // half way through a tick
  e.set("thinking");
  e.progress(transcribing(1, 2));
  e.step(0.01);
  expect(e.revealShown?.fraction).toBe(0); // same tick: not drawn yet
  e.step(1 / 24);
  expect(e.revealShown?.fraction).toBeGreaterThan(0);
  for (let i = 0; i < 48; i++) e.step(1 / 24);
  expect(e.revealShown?.fraction).toBeCloseTo(0.3, 3);
  e.progress(DONE);
  const before = e.revealShown?.fraction;
  e.step(0.005);
  expect(e.revealShown?.fraction).toBe(before);
  e.step(1 / 24);
  expect(e.revealShown).toMatchObject({ fraction: 1, rest: 0 });
  expect(e.revealShown?.flash).toBeGreaterThan(0);
});

test("done: the whole ring, then the usual vanish", () => {
  const e = new Emblem(fakeCanvas(), 150);
  e.appear();
  e.set("listening");
  for (let i = 0; i < 24; i++) e.step(1 / 24);
  e.set("thinking");
  e.progress(DONE);
  for (let i = 0; i < 12; i++) e.step(1 / 24);
  expect(e.revealShown).toEqual({ fraction: 1, rest: 0, flash: 0, echo: 0 });
  e.vanish();
  e.step(1 / 24);
  expect(e.phase).toBe("vanish");
  expect(e.revealShown?.fraction).toBe(1);
  for (let i = 0; i < 12; i++) e.step(1 / 24);
  expect(e.phase).toBe("hidden");
});

test("a notice after the recording brings back the whole ring, and late progress is ignored", () => {
  const e = new Emblem(fakeCanvas(), 150);
  e.appear();
  e.set("listening");
  for (let i = 0; i < 24; i++) e.step(1 / 24);
  e.set("thinking");
  e.progress(transcribing(1, 2));
  for (let i = 0; i < 6; i++) e.step(1 / 24);
  const f = e.revealShown?.fraction ?? 0;
  e.clearProgress();
  e.set("steady");
  e.step(1 / 24);
  const mid = e.revealShown?.rest ?? 0;
  expect(mid).toBeGreaterThan(0);
  expect(mid).toBeLessThan(1);
  expect(e.revealShown?.fraction).toBeGreaterThanOrEqual(f); // what was drawn stays
  e.progress(DONE); // the dictation is over
  for (let i = 0; i < 12; i++) e.step(1 / 24);
  expect(e.revealShown).toBeNull(); // the whole ring, as always
});

test("a notice on its own, and the home page's emblem, show the whole ring", () => {
  const notice = new Emblem(fakeCanvas(), 150);
  notice.appear();
  notice.set("steady");
  notice.step(1 / 24);
  expect(notice.revealShown).toBeNull();
  const home = new Emblem(fakeCanvas(), 110, { visible: true });
  home.set("steady");
  home.step(1 / 24);
  expect(home.revealShown).toBeNull();
  home.progress(DONE); // not a dictation: ignored
  home.step(1 / 24);
  expect(home.revealShown).toBeNull();
});

test("a hidden emblem ignores progress, and the next dictation starts with no ring", () => {
  const e = new Emblem(fakeCanvas(), 150);
  e.progress(transcribing(1, 2));
  e.step(0.1);
  e.appear();
  e.set("listening");
  e.progress(DONE);
  e.step(0.1);
  expect(e.revealShown?.fraction).toBe(1);
  e.vanish();
  e.progress(transcribing(0, 1)); // too late: ignored
  for (let i = 0; i < 24; i++) e.step(1 / 24);
  expect(e.phase).toBe("hidden");
  e.appear();
  e.set("listening");
  e.step(0.1);
  expect(e.revealShown).toEqual({ fraction: 0, rest: 0, flash: 0, echo: 0 });
});

test("with reduced motion the ring draws in by steps, without easing or flare", () => {
  vi.stubGlobal("window", { matchMedia: () => ({ matches: true }), devicePixelRatio: 1 });
  const e = new Emblem(fakeCanvas(), 150);
  e.appear();
  e.set("listening");
  e.step(1 / 24 + 1e-3);
  expect(e.revealShown).toEqual({ fraction: 0, rest: 0, flash: 0, echo: 0 });
  e.set("thinking");
  e.progress(transcribing(1, 2));
  e.step(1 / 24);
  expect(e.revealShown?.fraction).toBeCloseTo(0.3, 10);
  e.progress(DONE);
  e.step(1 / 24);
  expect(e.revealShown).toEqual({ fraction: 1, rest: 0, flash: 0, echo: 0 });
  // a notice brings the rest back at once
  e.clearProgress();
  e.set("steady");
  e.step(1 / 24);
  expect(e.revealShown).toBeNull();
});

// ---- the voice's echo on the glyph ring while recording ----

test("the echo is not there when quiet and grows with the voice, up to a cap under the drawn-in ring", () => {
  expect(echoAlpha(0)).toBe(0);
  expect(echoAlpha(0.05)).toBe(0); // the white ring's rest level
  expect(echoAlpha(Number.NaN)).toBe(0);
  let last = 0;
  for (let l = 0; l <= 1.0001; l += 0.01) {
    const a = echoAlpha(l);
    expect(a).toBeGreaterThanOrEqual(last);
    last = a;
  }
  expect(echoAlpha(0.5)).toBeGreaterThanOrEqual(0.35);
  expect(echoAlpha(0.5)).toBeLessThanOrEqual(0.45);
  expect(echoAlpha(1)).toBeGreaterThan(echoAlpha(0.5));
  expect(echoAlpha(1)).toBe(ECHO_MAX);
  expect(ECHO_MAX).toBeLessThanOrEqual(0.6); // the reveal draws its ring at 1
});

test("the echo draws the whole ring faintly while recording, nothing when quiet", () => {
  expect(glyphCalls(frame("listening", 0, { fraction: 0, rest: 0, flash: 0, echo: 0 }))).toBe(0);
  const calls = frame("listening", 0, { fraction: 0, rest: 0, flash: 0, echo: 0.4 });
  expect(glyphCalls(calls)).toBeGreaterThan(0);
  expect(calls.some((c) => c.name === "clip")).toBe(false); // one piece, all around
  // even an echo out of range stays well under the drawn-in ring (drawn at 1)
  const loud = frame("listening", 0, { fraction: 0, rest: 0, flash: 0, echo: 5 });
  const glyphStrokes = loud.filter((c, i) => {
    const before = loud[i - 1];
    return c.name === "stroke" && before?.name === "arc" && (before.args[2] as number) > 75 * 0.7;
  });
  expect(glyphStrokes.length).toBeGreaterThan(0);
  for (const c of glyphStrokes) expect(c.alpha).toBeLessThanOrEqual(ECHO_MAX);
});

test("the echo follows the microphone while recording and fades out over 0.2 s after the stop", () => {
  const e = new Emblem(fakeCanvas(), 150);
  e.appear();
  e.set("listening");
  e.step(1 / 24 + 1e-3);
  expect(e.revealShown?.echo).toBe(0); // quiet
  e.level = 0.5;
  e.step(1 / 24);
  const speaking = e.revealShown?.echo ?? 0;
  expect(speaking).toBeCloseTo(echoAlpha(0.5), 10);
  e.level = 1;
  e.step(1 / 24);
  expect(e.revealShown?.echo).toBeGreaterThan(speaking);
  e.level = 0.5;
  e.step(1 / 24);
  e.set("thinking");
  e.progress(transcribing(1, 4));
  e.step(1 / 24);
  const fading = e.revealShown?.echo ?? 0;
  expect(fading).toBeGreaterThan(0);
  expect(fading).toBeLessThan(speaking);
  for (let i = 0; i < 5; i++) e.step(1 / 24);
  expect(e.revealShown?.echo).toBe(0);
  expect(e.revealShown?.fraction).toBeGreaterThan(0); // while the ring draws itself in
});

test("with reduced motion the echo still follows the voice and goes at once on the stop", () => {
  vi.stubGlobal("window", { matchMedia: () => ({ matches: true }), devicePixelRatio: 1 });
  const e = new Emblem(fakeCanvas(), 150);
  e.appear();
  e.set("listening");
  e.level = 0.5;
  e.step(1 / 24 + 1e-3);
  expect(e.revealShown?.echo).toBeCloseTo(echoAlpha(0.5), 10);
  e.set("thinking");
  e.step(1 / 24);
  expect(e.revealShown?.echo).toBe(0);
});
