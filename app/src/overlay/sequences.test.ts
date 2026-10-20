// The page's controller with the real emblem and a simulated browser (animation frames and
// timers on one fake clock): the event sequences that overlay.rs sends during dictations and
// notices must always end with the emblem drawn while it should be on screen.

import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { OverlayEvent, OverlayView } from "./overlay";

type EmblemModule = typeof import("./emblem");
type OverlayModule = typeof import("./overlay");

class NullView implements OverlayView {
  percent: number | null = null;
  showProgress(percent: number | null): void {
    this.percent = percent;
    this.caption = percent === null ? null : String(percent);
  }
  /** The caption beside the emblem, which the percentage and the waiting line share. */
  caption: string | null = null;
  showWaiting(text: string | null): void {
    this.caption = text;
  }
  lines: string | null = null;
  /** The buttons of a question that is on screen: "yes|no", or null. */
  buttons: string | null = null;
  layout(): void {}
  showLine(text: string): void {
    this.lines = text;
  }
  clearLine(): void {
    this.lines = null;
  }
  showAsk({ yes, no }: { yes: string; no: string }): void {
    this.buttons = `${yes}|${no}`;
  }
  setHold(): void {}
  setChoice(): void {}
  hideAsk(): void {
    this.buttons = null;
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

const SETTINGS: OverlayEvent = { type: "settings", px: 150, subtitles: "on", language: "zh-TW" };
const NO_SOUND: OverlayEvent = { type: "notice", text: "《否。沒有收到聲音，請檢查麥克風。》" };

test("learned words wait through live dictation and completion before appearing", () => {
  const p = page();
  p.send({ type: "show", mode: "listening" });
  run(1000);
  p.send({ type: "learned", text: "learned" });
  run(1000);
  expect(p.emblem.phase).toBe("listening");
  expect(p.view.lines).toBeNull();
  p.send(
    { type: "mode", mode: "thinking" },
    { type: "progress", stage: "transcribing", done: 1, total: 3, audio_seconds: 5 },
  );
  run(1500);
  expect(p.emblem.phase).toBe("thinking");
  expect(p.view.percent).toBe(20);
  expect(p.view.lines).toBeNull();
  expect(p.drawsNow()).toBe(true);
  p.send({ type: "complete" });
  expect(p.view.percent).toBe(100);
  expect(p.view.lines).toBeNull();
  run(1000);
  expect(p.view.percent).toBeNull();
  expect(p.view.lines).toBe("learned");
  expect(p.drawsNow()).toBe(true);
  run(10_000);
  expect(p.emblem.phase).toBe("hidden");
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

test("a dictation with progress: the whole ring all the way through, the percentage beside it, then the emblem vanishes", () => {
  const p = page();
  p.send(SETTINGS);
  for (let round = 0; round < 2; round++) {
    p.send({ type: "show", mode: "listening" });
    run(1200);
    expect(p.emblem.boost, `round ${round}: a new dictation starts quiet`).toBe(0);
    p.send({ type: "level", value: 0.6 });
    run(100);
    expect(p.emblem.boost).toBeGreaterThan(0);
    p.send({ type: "level", value: 0 }); // the speech ends
    p.send({ type: "mode", mode: "thinking" }, progress("transcribing", 1, 3));
    expect(p.emblem.boost).toBe(0);
    expect(p.drawsNow()).toBe(true); // 100 ms: the ring is still there
    run(1500);
    expect(p.view.percent).toBe(20);
    p.send(progress("transcribing", 3, 3), progress("cleaning", 3, 3));
    run(800);
    expect(p.view.percent).toBeGreaterThan(60);
    expect(p.view.percent).toBeLessThan(95);
    p.send(progress("done"));
    expect(p.view.percent).toBe(99);
    expect(p.drawsNow()).toBe(true);
    p.send({ type: "hide" });
    run(1000);
    expect(p.view.percent).toBeNull();
    expect(p.emblem.phase).toBe("hidden");
    expect(p.drawsNow()).toBe(false);
    expect(p.view.lines).toBeNull(); // no words at any point
  }
});

test("progress then a notice (the text was only copied): the percentage goes, the notice shows", () => {
  const p = page();
  p.send({ type: "show", mode: "listening" });
  run(1000);
  p.send({ type: "mode", mode: "thinking" }, progress("transcribing", 0, 1), progress("cleaning", 1, 1));
  run(500);
  p.send(progress("done"), NO_SOUND);
  run(400);
  expect(p.view.percent).toBeNull();
  expect(p.view.lines).not.toBeNull();
  run(10_000);
  expect(p.emblem.phase).toBe("hidden");
  p.send({ type: "show", mode: "listening" });
  expect(p.drawsNow()).toBe(true);
  expect(p.emblem.boost).toBe(0);
});

test("a quick tap: the reply comes during the appear animation", () => {
  const p = page();
  p.send({ type: "show", mode: "listening" });
  run(200);
  p.send({ type: "mode", mode: "thinking" }, progress("transcribing", 0, 1), progress("done"));
  run(50);
  expect(p.emblem.phase).toBe("appear");
  expect(p.view.percent).toBe(99);
  p.send({ type: "hide" });
  run(1000);
  expect(p.emblem.phase).toBe("hidden");
  p.send({ type: "show", mode: "listening" });
  expect(p.drawsNow()).toBe(true);
  expect(p.view.percent).toBeNull();
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

test("two shows without a hide start over and keep the real emblem drawing", () => {
  const p = page();
  p.send({ type: "show", mode: "listening" });
  run(1200);
  p.send({ type: "level", value: 0.7 }, { type: "mode", mode: "thinking" }, progress("transcribing", 1, 3));
  run(500);
  expect(p.view.percent).not.toBeNull();
  p.send({ type: "show", mode: "listening" }, { type: "show", mode: "listening" });
  run(2000);
  expect(p.emblem.phase).toBe("listening");
  expect(p.view.percent).toBeNull();
  expect(p.stats.ink).toBe(true);
});

test("a notice timer cannot hide the next recording after show takes over", () => {
  const p = page();
  p.send(NO_SOUND);
  run(1200); // The notice's final hide is still pending.
  p.send({ type: "show", mode: "listening" });
  run(10_000);
  expect(p.emblem.phase).toBe("listening");
  expect(p.emblem.boost).toBe(0);
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

// ---- Raphael answers and asks ----

const ANSWER: OverlayEvent = { type: "say", prefix: "答", text: "台北現在 24 度，多雲。" };
const QUESTION: OverlayEvent = {
  type: "ask", id: "c1", tier: "T2", prefix: "問", text: "要複製到剪貼簿嗎？", yes: "是", no: "否", timeout_ms: 30_000,
};

test("an agent turn: listening, thinking, the answer in words, and the emblem goes after them", () => {
  const p = page();
  p.send(SETTINGS, { type: "show", mode: "listening" });
  run(1000);
  p.send({ type: "mode", mode: "thinking" });
  run(1500);
  expect(p.view.percent).not.toBeNull();
  p.send(ANSWER);
  expect(p.view.percent).toBeNull();
  expect(p.drawsNow()).toBe(true);
  run(1000);
  expect(p.view.lines).toBe("《答。台北現在 24 度，多雲。》");
  run(10_000);
  expect(p.emblem.phase).toBe("hidden");
  expect(p.view.lines).toBeNull();
  expect(p.drawsNow()).toBe(false);
});

test("a question keeps the emblem drawn while it waits; the answer to it replaces it and the emblem goes", () => {
  const p = page();
  p.send(SETTINGS, { type: "show", mode: "listening" });
  run(1000);
  p.send({ type: "mode", mode: "thinking" });
  run(500);
  p.send(QUESTION);
  run(1500);
  expect(p.view.buttons).toBe("是|否");
  expect(p.view.lines).toBe("《問。要複製到剪貼簿嗎？》");
  expect(p.view.percent).toBeNull();
  run(20_000);
  expect(p.view.buttons).toBe("是|否");
  expect(p.emblem.phase).not.toBe("hidden");
  expect(p.drawsNow()).toBe(true);
  p.controller.clickAsk("yes");
  p.send({ type: "say", prefix: "了", text: "已複製。" });
  expect(p.view.buttons).toBeNull();
  run(1000);
  expect(p.view.lines).toBe("《了。已複製。》");
  run(10_000);
  expect(p.emblem.phase).toBe("hidden");
  expect(p.view.lines).toBeNull();
});

test("a question nobody answered is let go, and the next dictation starts clean", () => {
  const p = page();
  p.send(SETTINGS, { ...QUESTION, timeout_ms: 5000 });
  run(5000);
  expect(p.view.buttons).toBe("是|否");
  run(3000);
  expect(p.view.buttons).toBeNull();
  expect(p.view.lines).toBeNull();
  run(1000);
  expect(p.emblem.phase).toBe("hidden");
  p.send({ type: "show", mode: "listening" });
  expect(p.drawsNow()).toBe(true);
  expect(p.view.buttons).toBeNull();
});

test("Esc during a question (the app hides the emblem) takes the buttons away at once", () => {
  const p = page();
  p.send(SETTINGS, QUESTION);
  run(1500);
  p.send({ type: "hide" });
  expect(p.view.buttons).toBeNull();
  expect(p.view.lines).toBeNull();
  run(1000);
  expect(p.emblem.phase).toBe("hidden");
});

const WAITING = ["解析中……", "並列演算中……", "檢索最適解……"];

test("a question: greeted, thought about with changing lines, and the answer takes them away", () => {
  const p = page();
  p.send(SETTINGS, { type: "show", mode: "listening", line: "《告。今天有什麼事可以幫忙嗎？》" });
  run(1500);
  expect(p.view.lines).toBe("《告。今天有什麼事可以幫忙嗎？》");
  expect(p.view.caption).toBeNull();
  p.send({ type: "mode", mode: "thinking", quiet: true, lines: WAITING });
  expect(p.view.lines).toBeNull();
  expect(p.view.caption).not.toBeNull();
  const seen = new Set<string | null>();
  for (let i = 0; i < 20; i++) {
    run(1500);
    seen.add(p.view.caption);
    expect(p.drawsNow()).toBe(true);
  }
  expect(seen.size).toBeGreaterThan(1);
  for (const caption of seen) expect(WAITING).toContain(caption);
  expect(p.view.percent).toBeNull();
  p.send(ANSWER);
  expect(p.view.caption).toBeNull();
  run(1000);
  expect(p.view.lines).toBe("《答。台北現在 24 度，多雲。》");
  expect(p.view.caption).toBeNull();
  run(10_000);
  expect(p.emblem.phase).toBe("hidden");
  expect(p.view.caption).toBeNull();
});

test("Esc while a question is thought about takes the line away with the emblem", () => {
  const p = page();
  p.send(SETTINGS, { type: "show", mode: "listening", line: "《告。今天有什麼事可以幫忙嗎？》" });
  run(1000);
  p.send({ type: "mode", mode: "thinking", quiet: true, lines: WAITING });
  run(4000);
  expect(p.view.caption).not.toBeNull();
  p.send({ type: "hide" });
  expect(p.view.caption).toBeNull();
  run(10_000);
  expect(p.view.caption).toBeNull();
  expect(p.emblem.phase).toBe("hidden");
});
