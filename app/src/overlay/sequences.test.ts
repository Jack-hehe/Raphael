// The page's controller with the real emblem and a simulated browser (animation frames and
// timers on one fake clock): the event sequences that overlay.rs sends during dictations and
// notices must always end with the emblem drawn while it should be on screen.

import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { OverlayEvent, OverlayView } from "./overlay";

type EmblemModule = typeof import("./emblem");
type OverlayModule = typeof import("./overlay");

class NullView implements OverlayView {
  percent: number | null = null;
  showProgress(percent: number | null): void { this.percent = percent; }
  lines: string | null = null;
  place(): void {}
  layout(): void {}
  showLines(zh: string | null, ja: string | null): void {
    this.lines = `${zh ?? "-"}|${ja ?? "-"}`;
  }
  clearLines(): void {
    this.lines = null;
  }
}

/** Counts the canvas draws that put something on it (anything besides clearing it). */
function countingCanvas() {
  const stats = { drawn: 0, ink: false, reads: 0 };
  let marked = false;
  const state: Record<string, unknown> = { globalAlpha: 1, globalCompositeOperation: "source-over", filter: "none" };
  const stack: Array<Record<string, unknown>> = [];
  const ctx = new Proxy(state, {
    get(target, prop: string) {
      if (prop in target) return target[prop];
      if (prop === "createRadialGradient") return () => ({ addColorStop: () => {} });
      if (prop === "getImageData") return () => {
        stats.reads++;
        return { data: new Uint8ClampedArray([0, 0, 0, stats.ink ? 255 : 0]) };
      };
      return (...args: unknown[]) => {
        for (const arg of args) {
          if (typeof arg === "number") expect(Number.isFinite(arg), prop).toBe(true);
        }
        if (prop === "save") stack.push({ ...target });
        else if (prop === "restore") Object.assign(target, stack.pop());
        else if (prop === "clearRect") {
          marked = false;
          stats.ink = false;
        } else if (!marked && (target.globalAlpha as number) > 0 && (prop === "stroke" || prop === "fill" || prop === "drawImage")) {
          marked = true;
          stats.ink = true;
          stats.drawn++;
        }
      };
    },
    set(target, prop: string, value) {
      if (typeof value === "number") expect(Number.isFinite(value), prop).toBe(true);
      target[prop] = value;
      return true;
    },
  });
  const canvas = {
    style: {},
    width: 0,
    height: 0,
    isConnected: true,
    getClientRects: () => [{}],
    getContext: () => ctx,
  } as unknown as HTMLCanvasElement;
  return { canvas, stats };
}

let frames: FrameRequestCallback[];
let now: number;
let emblemModule: EmblemModule;
let overlayModule: OverlayModule;

beforeEach(async () => {
  vi.useFakeTimers();
  frames = [];
  now = 0;
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => frames.push(callback));
  vi.resetModules();
  emblemModule = await import("./emblem");
  overlayModule = await import("./overlay");
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

/** Lets `ms` milliseconds go by at 60 frames a second, timers included. */
function run(ms: number): void {
  const step = 1000 / 60;
  for (let t = 0; t < ms; t += step) {
    vi.advanceTimersByTime(step);
    now += step;
    const due = frames.splice(0);
    for (const callback of due) callback(now);
  }
}

function page() {
  const { canvas, stats } = countingCanvas();
  const emblem = new emblemModule.Emblem(canvas, 150);
  const view = new NullView();
  const controller = new overlayModule.OverlayController(emblem, view);
  const send = (...events: OverlayEvent[]) => {
    for (const event of events) controller.handle(event);
  };
  /** True when the emblem is drawn over the next 100 ms. */
  const drawsNow = (): boolean => {
    const before = stats.drawn;
    run(100);
    return stats.drawn > before;
  };
  return { emblem, view, send, drawsNow, stats, canvas, controller };
}

const SETTINGS: OverlayEvent = { type: "settings", px: 150, subtitles: "both" };
const NO_SOUND: OverlayEvent = { type: "notice", zh: "《否。沒有收到聲音，請檢查麥克風。》", ja: "《否。音声が届いていません。》" };

test("a delayed corner placement preserves the real dictation progress ring", async () => {
  const p = page();
  let placed!: () => void;
  p.view.place = () => new Promise<void>((resolve) => { placed = resolve; });
  p.send(
    { type: "show", mode: "listening" },
    { type: "mode", mode: "thinking" },
    { type: "progress", stage: "transcribing", done: 1, total: 3, audio_seconds: 5 },
  );
  expect(p.emblem.phase).toBe("hidden");
  placed();
  await Promise.resolve();
  run(1000);
  expect(p.emblem.phase).toBe("thinking");
  expect(p.emblem.revealShown).not.toBeNull();
  expect(p.drawsNow()).toBe(true);
});

test("a dictation: show, levels, thinking, hide, and the next dictation shows again", () => {
  const p = page();
  p.send(SETTINGS);
  run(100);
  for (let round = 0; round < 3; round++) {
    p.send({ type: "show", mode: "listening" });
    expect(p.drawsNow(), `round ${round}: listening`).toBe(true);
    for (let i = 0; i < 20; i++) {
      p.send({ type: "level", value: (i % 5) / 5 });
      run(50);
    }
    expect(p.emblem.phase).toBe("listening");
    p.send({ type: "mode", mode: "thinking" });
    expect(p.drawsNow()).toBe(true);
    expect(p.emblem.phase).toBe("thinking");
    p.send({ type: "hide" });
    run(1000);
    expect(p.emblem.phase).toBe("hidden");
    expect(p.drawsNow()).toBe(false);
  }
});

test("a quick tap: show and hide within the appear animation, then show again", () => {
  const p = page();
  p.send({ type: "show", mode: "listening" });
  run(200);
  p.send({ type: "mode", mode: "thinking" }, { type: "hide" });
  run(1000);
  expect(p.emblem.phase).toBe("hidden");
  p.send({ type: "show", mode: "listening" });
  expect(p.drawsNow()).toBe(true);
  run(1000);
  expect(p.emblem.phase).toBe("listening");
});

test("the no-sound notice after a dictation, then another dictation", () => {
  const p = page();
  for (let round = 0; round < 3; round++) {
    p.send({ type: "show", mode: "listening" });
    run(1000);
    p.send({ type: "mode", mode: "thinking" });
    run(300);
    p.send(NO_SOUND);
    expect(p.drawsNow(), `round ${round}: notice`).toBe(true);
    run(1000);
    expect(p.view.lines).not.toBeNull();
    run(10_000);
    expect(p.emblem.phase).toBe("hidden");
    expect(p.view.lines).toBeNull();
  }
  p.send({ type: "show", mode: "listening" });
  expect(p.drawsNow()).toBe(true);
});

test("a notice right after a hide (the vanish is cut short) still shows", () => {
  const p = page();
  p.send({ type: "show", mode: "listening" });
  run(1000);
  p.send({ type: "hide" });
  run(100);
  expect(p.emblem.phase).toBe("vanish");
  p.send(NO_SOUND);
  run(1200);
  expect(p.emblem.phase).toBe("steady");
  expect(p.view.lines).not.toBeNull();
});

test("show during a notice, show during the vanish, hide during the appear", () => {
  const p = page();
  p.send(NO_SOUND);
  run(300);
  p.send({ type: "show", mode: "listening" }); // dictation wins over the notice
  run(1000);
  expect(p.emblem.phase).toBe("listening");
  p.send({ type: "hide" });
  run(200);
  p.send({ type: "show", mode: "listening" }); // during the vanish
  run(1000);
  expect(p.emblem.phase).toBe("listening");
  p.send({ type: "hide" });
  run(1000);
  p.send({ type: "show", mode: "listening" });
  run(300);
  p.send({ type: "hide" }); // during the appear
  run(100);
  p.send({ type: "hide" }); // and again while vanishing
  run(1000);
  expect(p.emblem.phase).toBe("hidden");
  p.send({ type: "show", mode: "listening" });
  expect(p.drawsNow()).toBe(true);
});

test("a notice that comes before any show, and one that comes while hidden", () => {
  const p = page();
  p.send(SETTINGS, NO_SOUND);
  expect(p.drawsNow()).toBe(true);
  run(10_000);
  expect(p.emblem.phase).toBe("hidden");
  p.send(NO_SOUND);
  expect(p.drawsNow()).toBe(true);
});

test("a hidden emblem lets the animation loop stop, and the next show starts it again", () => {
  const p = page();
  p.send({ type: "show", mode: "listening" });
  run(1000);
  p.send({ type: "hide" });
  run(2000);
  expect(frames).toHaveLength(0);
  p.send({ type: "show", mode: "listening" });
  expect(frames).toHaveLength(1);
  expect(p.drawsNow()).toBe(true);
});

const progress = (stage: "transcribing" | "cleaning" | "done", done = 0, total = 0): OverlayEvent => ({
  type: "progress",
  stage,
  done,
  total,
  audio_seconds: 6,
});

test("a dictation with progress: the whole ring while listening, swept away at the stop, drawn in through the stages, then the emblem vanishes", () => {
  const p = page();
  p.send(SETTINGS);
  for (let round = 0; round < 2; round++) {
    p.send({ type: "show", mode: "listening" });
    run(1200);
    expect(p.emblem.revealShown, `round ${round}: a new dictation shows the whole ring`).toEqual({ fraction: 0, rest: 0, flash: 0, away: 0, boost: 0 });
    p.send({ type: "level", value: 0.6 });
    run(100);
    expect(p.emblem.revealShown?.boost).toBeGreaterThan(0);
    p.send({ type: "level", value: 0 }); // the speech ends
    p.send({ type: "mode", mode: "thinking" }, progress("transcribing", 1, 3));
    expect(p.drawsNow()).toBe(true); // 100 ms
    expect(p.emblem.revealShown?.away).toBeGreaterThan(0); // the ring is swept away first
    expect(p.emblem.revealShown?.fraction).toBe(0);
    run(220);
    const first = p.emblem.revealShown?.fraction ?? 0;
    expect(first).toBeGreaterThan(0);
    expect(first).toBeLessThanOrEqual(0.2 + 1e-9);
    p.send(progress("transcribing", 3, 3), progress("cleaning", 3, 3));
    run(800);
    const cleaning = p.emblem.revealShown?.fraction ?? 0;
    expect(cleaning).toBeGreaterThan(0.6);
    expect(cleaning).toBeLessThan(0.95);
    p.send(progress("done"));
    run(50);
    expect(p.emblem.revealShown?.fraction).toBe(1);
    p.send({ type: "hide" });
    run(1000);
    expect(p.emblem.phase).toBe("hidden");
    expect(p.drawsNow()).toBe(false);
    expect(p.view.lines).toBeNull(); // no words at any point
  }
});

test("progress then a notice (the text was only copied): the whole ring is back, the notice shows", () => {
  const p = page();
  p.send({ type: "show", mode: "listening" });
  run(1000);
  p.send({ type: "mode", mode: "thinking" }, progress("transcribing", 0, 1), progress("cleaning", 1, 1));
  run(500);
  p.send(progress("done"), NO_SOUND);
  run(400);
  expect(p.emblem.revealShown).toBeNull(); // the whole ring
  expect(p.view.lines).not.toBeNull();
  run(10_000);
  expect(p.emblem.phase).toBe("hidden");
  p.send({ type: "show", mode: "listening" });
  expect(p.drawsNow()).toBe(true);
  expect(p.emblem.revealShown?.fraction).toBe(0);
});

test("a quick tap: the reply comes during the appear animation, and the ring still closes", () => {
  const p = page();
  p.send({ type: "show", mode: "listening" });
  run(200);
  p.send({ type: "mode", mode: "thinking" }, progress("transcribing", 0, 1), progress("done"));
  run(50);
  expect(p.emblem.phase).toBe("appear");
  expect(p.emblem.revealShown?.fraction).toBe(1);
  p.send({ type: "hide" });
  run(1000);
  expect(p.emblem.phase).toBe("hidden");
  p.send({ type: "show", mode: "listening" });
  expect(p.drawsNow()).toBe(true);
  expect(p.emblem.revealShown?.fraction).toBe(0);
});

test("progress that comes after the hide changes nothing", () => {
  const p = page();
  p.send({ type: "show", mode: "listening" });
  run(1000);
  p.send({ type: "hide" });
  run(100);
  p.send(progress("cleaning", 1, 1));
  run(1000);
  expect(p.emblem.phase).toBe("hidden");
  expect(p.drawsNow()).toBe(false);
  expect(frames).toHaveLength(0);
});

test("two shows without a hide reset a swept ring and keep the real emblem drawing", () => {
  const p = page();
  p.send({ type: "show", mode: "listening" });
  run(1200);
  p.send({ type: "level", value: 0.7 }, { type: "mode", mode: "thinking" }, progress("transcribing", 1, 3));
  run(500);
  expect(p.emblem.revealShown?.away).toBe(1);
  p.send({ type: "show", mode: "listening" }, { type: "show", mode: "listening" });
  run(2000);
  expect(p.emblem.phase).toBe("listening");
  expect(p.emblem.revealShown).toMatchObject({ fraction: 0, away: 0, rest: 0 });
  expect(p.stats.ink).toBe(true);
});

test("a notice timer cannot hide the next recording after show takes over", () => {
  const p = page();
  p.send(NO_SOUND);
  run(1200); // The notice's final hide is still pending.
  p.send({ type: "show", mode: "listening" });
  run(10_000);
  expect(p.emblem.phase).toBe("listening");
  expect(p.emblem.revealShown).toMatchObject({ away: 0, fraction: 0 });
  expect(p.stats.ink).toBe(true);
  expect(p.view.lines).toBeNull();
});

test("the log's recording, Cancel, recording sequence shows again after an idle clock", () => {
  const p = page();
  p.send({ type: "show", mode: "listening" });
  run(2000);
  p.send({ type: "hide" }); // 15:47:36 Cancel, not a stop or a second start bug.
  run(1000);
  expect(p.stats.ink).toBe(false);
  now += 60_000;
  vi.advanceTimersByTime(60_000);
  p.send({ type: "show", mode: "listening" });
  run(2000);
  expect(p.emblem.phase).toBe("listening");
  expect(p.stats.ink).toBe(true);
});

test("an old hide delivered after a new show clears the real emblem despite continuing levels", () => {
  const p = page();
  p.send({ type: "show", mode: "listening" });
  run(1200);
  // The failing Rust interleaving: finish ended the first session, then a hotkey started the
  // next one before finish could publish its hide. The renderer correctly obeys that late hide.
  p.send({ type: "show", mode: "listening" }, { type: "hide" });
  for (let i = 0; i < 40; i++) {
    p.send({ type: "level", value: 0.7 });
    run(50);
  }
  expect(p.emblem.phase).toBe("hidden");
  expect(p.stats.ink).toBe(false);
  expect(frames).toHaveLength(0);
});

test("an unexpectedly hidden real emblem still warns while the controller expects paint", async () => {
  const p = page();
  const logged: Array<[string, string]> = [];
  const { FrameWatch } = await import("./diagnostics");
  const watch = new FrameWatch(() => p.emblem.frames, () => "visible", (level, message) => {
    logged.push([level, message]);
  }, { canvas: p.canvas, expected: () => p.controller.expectsPaint });
  p.send({ type: "show", mode: "listening" });
  watch.expectFrames("show");
  run(2000);
  expect(logged).toEqual([]);
  // Simulate a renderer state regression without a Rust hide: visibility intent must not be
  // inferred from the broken renderer's phase, or this blank would silently disable the watch.
  p.emblem.vanish();
  run(3000);
  expect(p.emblem.phase).toBe("hidden");
  expect(logged).toEqual([["warn", expect.stringContaining("all-transparent")]]);
});

test("an expired notice stops paint monitoring without an all-transparent warning", async () => {
  const p = page();
  const logged: Array<[string, string]> = [];
  const { FrameWatch } = await import("./diagnostics");
  const watch = new FrameWatch(() => p.emblem.frames, () => "visible", (level, message) => {
    logged.push([level, message]);
  }, { canvas: p.canvas, expected: () => p.controller.expectsPaint });
  p.send(NO_SOUND);
  watch.expectFrames("notice");
  run(10_000);
  expect(p.emblem.phase).toBe("hidden");
  const reads = p.stats.reads;
  expect(reads).toBeGreaterThan(0);
  run(3000);
  expect(p.stats.reads).toBe(reads);
  expect(logged).toEqual([]);
});
