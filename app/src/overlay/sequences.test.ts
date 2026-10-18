// The page's controller with the real emblem and a simulated browser (animation frames and
// timers on one fake clock): the event sequences that overlay.rs sends during dictations and
// notices must always end with the emblem drawn while it should be on screen.

import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { OverlayEvent, OverlayView } from "./overlay";

type EmblemModule = typeof import("./emblem");
type OverlayModule = typeof import("./overlay");

class NullView implements OverlayView {
  lines: string | null = null;
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
  const stats = { drawn: 0 };
  let marked = false;
  const ctx = new Proxy({} as Record<string, unknown>, {
    get(target, prop: string) {
      if (prop in target) return target[prop];
      if (prop === "createRadialGradient") return () => ({ addColorStop: () => {} });
      return () => {
        if (prop === "clearRect") marked = false;
        else if (!marked && (prop === "stroke" || prop === "fill" || prop === "drawImage")) {
          marked = true;
          stats.drawn++;
        }
      };
    },
    set(target, prop: string, value) {
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
  return { emblem, view, send, drawsNow };
}

const SETTINGS: OverlayEvent = { type: "settings", px: 150, subtitles: "both" };
const NO_SOUND: OverlayEvent = { type: "notice", zh: "《否。沒有收到聲音，請檢查麥克風。》", ja: "《否。音声が届いていません。》" };

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

test("a dictation with progress: no glyph ring while listening, it draws in through the stages, then the emblem vanishes", () => {
  const p = page();
  p.send(SETTINGS);
  for (let round = 0; round < 2; round++) {
    p.send({ type: "show", mode: "listening" });
    run(1200);
    expect(p.emblem.revealShown, `round ${round}: a new dictation has no glyph ring`).toEqual({ fraction: 0, rest: 0, flash: 0, echo: 0 });
    p.send({ type: "mode", mode: "thinking" }, progress("transcribing", 1, 3));
    expect(p.drawsNow()).toBe(true);
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
