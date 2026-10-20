import { afterEach, beforeEach, expect, test, vi } from "vitest";
import {
  FrameWatch,
  SUBSCRIBE_RETRY_MS,
  describeError,
  guardHandler,
  subscribeWithRetry,
  type LogLevel,
} from "./diagnostics";
import type { OverlayEvent } from "./overlay";

let logged: Array<[LogLevel, string]>;
const log = (level: LogLevel, message: string) => {
  logged.push([level, message]);
};

beforeEach(() => {
  logged = [];
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
});

// ---- errors as text ----

test("an error is described with its name, message and the top of its stack", () => {
  const err = new TypeError("x is undefined");
  expect(describeError(err)).toContain("TypeError: x is undefined");
  expect(describeError("plain")).toBe("plain");
  expect(describeError({ code: 3 })).toBe('{"code":3}');
  expect(describeError(undefined)).toBe("undefined");
});

// ---- the event handler ----

test("an event that throws is reported and the next events are still handled", () => {
  const seen: string[] = [];
  const handler = guardHandler((event: OverlayEvent) => {
    if (event.type === "mode") throw new Error("boom");
    seen.push(event.type);
  }, log);
  handler({ type: "show", mode: "listening" });
  handler({ type: "mode", mode: "thinking" });
  handler({ type: "hide" });
  expect(seen).toEqual(["show", "hide"]);
  expect(logged.filter(([level]) => level === "error")).toEqual([
    ["error", expect.stringContaining('the "mode" event failed: Error: boom')],
  ]);
});

test("event diagnostics log only approved metadata and omit loudness and text", () => {
  const handler = guardHandler(() => {}, log);
  const show = { type: "show", mode: "listening", future_text: "private future field" } as const;
  const notice = { type: "notice", text: "Private notice text", future_text: "private notice field" } as const;
  const learned = { type: "learned", text: "Private learned word", future_text: "private learned field" } as const;
  const say = { type: "say", prefix: "Private prefix", text: "Private answer text", future_text: "private say field" } as const;
  const ask = {
    type: "ask", id: "private-id", tier: "T3", prefix: "Private prefix", text: "Private question", yes: "Private yes",
    no: "Private no", timeout_ms: 30_000, future_text: "private ask field",
  } as const;
  const progress = { type: "progress", stage: "transcribing", done: 2, total: 3, audio_seconds: 4.5, future_text: "private progress field" } as const;
  const settings = { type: "settings", px: 150, subtitles: "private settings field", language: "private language field" } as const;
  handler(show);
  handler({ type: "level", value: 0.5 });
  handler(notice);
  handler(learned);
  handler(say);
  handler(ask);
  handler(progress);
  handler(settings);
  expect(logged).toEqual([
    ["debug", 'event {"type":"show","mode":"listening"}'],
    ["debug", 'event {"type":"notice"}'],
    ["debug", 'event {"type":"learned"}'],
    ["debug", 'event {"type":"say"}'],
    ["debug", 'event {"type":"ask","tier":"T3"}'],
    ["debug", 'event {"type":"progress","stage":"transcribing","done":2,"total":3,"audio_seconds":4.5}'],
    ["debug", 'event {"type":"settings","px":150}'],
  ]);
});

test("a greeting and a quiet thought are logged as flags, without Raphael's words", () => {
  const handler = guardHandler(() => {}, log);
  handler({ type: "show", mode: "listening", line: "《告。主人，請下達指示。》" });
  handler({ type: "mode", mode: "thinking", quiet: true });
  handler({ type: "mode", mode: "thinking", quiet: true, lines: ["Private waiting line", "Another"] });
  handler({ type: "mode", mode: "thinking", quiet: false });
  expect(logged).toEqual([
    ["debug", 'event {"type":"show","mode":"listening","greeting":true}'],
    ["debug", 'event {"type":"mode","mode":"thinking","quiet":true}'],
    ["debug", 'event {"type":"mode","mode":"thinking","quiet":true}'],
    ["debug", 'event {"type":"mode","mode":"thinking"}'],
  ]);
});

test.each([
  { type: "notice", text: "Private text" },
  { type: "learned", text: "Private text" },
  { type: "say", prefix: "Private", text: "Private text" },
  { type: "ask", id: "private-id", tier: "T2", prefix: "Private", text: "Private text", yes: "Yes", no: "No", timeout_ms: 1000 },
] as const)("$type handler failures omit error details that may contain private text", (event) => {
  const error = new Error("Private learned word in error message");
  error.name = "Private error name";
  error.stack = "Private stack with learned text";
  const handler = guardHandler(() => { throw error; }, log);
  handler(event);
  expect(logged).toHaveLength(2);
  expect(logged[1]).toEqual(["error", `the "${event.type}" event failed: details omitted for text-bearing events`]);
});

// ---- subscribing ----

test("a subscription that works at once is logged once", async () => {
  const open = vi.fn().mockResolvedValue(undefined);
  await expect(subscribeWithRetry(open, log)).resolves.toBe(true);
  expect(open).toHaveBeenCalledTimes(1);
  expect(logged).toEqual([["info", "subscribed to the overlay events"]]);
});

test("a subscription that fails is tried again, and every failure is reported", async () => {
  const open = vi
    .fn()
    .mockRejectedValueOnce("the overlay is not running")
    .mockRejectedValueOnce("the overlay is not running")
    .mockResolvedValue(undefined);
  const done = subscribeWithRetry(open, log);
  await vi.runAllTimersAsync();
  await expect(done).resolves.toBe(true);
  expect(open).toHaveBeenCalledTimes(3);
  expect(logged).toEqual([
    ["warn", "could not subscribe to the overlay events (attempt 1): the overlay is not running"],
    ["warn", "could not subscribe to the overlay events (attempt 2): the overlay is not running"],
    ["info", "subscribed to the overlay events after 3 attempts"],
  ]);
});

test("the page gives up after the last retry and says so", async () => {
  const open = vi.fn().mockRejectedValue(new Error("nope"));
  const done = subscribeWithRetry(open, log);
  await vi.runAllTimersAsync();
  await expect(done).resolves.toBe(false);
  expect(open).toHaveBeenCalledTimes(SUBSCRIBE_RETRY_MS.length + 1);
  expect(logged[logged.length - 1]).toEqual([
    "error",
    expect.stringContaining("gave up subscribing to the overlay events"),
  ]);
});

// ---- the animation loop ----

test("frames that do not come while the emblem should be on screen are reported once", () => {
  let frames = 0;
  const watch = new FrameWatch(() => frames, () => "visible", log);
  watch.expectFrames("show");
  vi.advanceTimersByTime(2000);
  expect(logged).toEqual([
    ["warn", expect.stringContaining('no animation frame in 1500 ms after "show" (document visible)')],
  ]);
  watch.expectFrames("show");
  vi.advanceTimersByTime(2000);
  expect(logged).toHaveLength(1);
  // They come back: the next stall is worth a report again.
  watch.expectFrames("show");
  vi.advanceTimersByTime(500);
  frames = 5;
  vi.advanceTimersByTime(1500);
  expect(logged).toHaveLength(2);
  expect(logged[1]).toEqual(["info", expect.stringContaining("animation frames run again")]);
});

test("frames that come say nothing", () => {
  let frames = 0;
  const watch = new FrameWatch(() => frames, () => "visible", log);
  watch.expectFrames("notice");
  frames = 40;
  vi.advanceTimersByTime(2000);
  expect(logged).toEqual([]);
});

function paintWatch() {
  const pixels = new Uint8ClampedArray(16);
  let expected = true;
  let frames = 0;
  let reads = 0;
  const canvas = {
    width: 2,
    height: 2,
    getContext: () => ({
      getImageData: () => {
        reads++;
        return { data: pixels };
      },
    }),
  } as unknown as HTMLCanvasElement;
  const watch = new FrameWatch(() => frames++, () => "visible", log, {
    canvas,
    expected: () => expected,
  });
  return { watch, pixels, hide: () => { expected = false; }, reads: () => reads };
}

test("running frames with an all-transparent emblem warn after 1.5 seconds, once per blank spell", () => {
  const p = paintWatch();
  p.watch.expectFrames("show");
  vi.advanceTimersByTime(1499);
  expect(logged).toEqual([]);
  vi.advanceTimersByTime(501);
  expect(logged).toEqual([["warn", expect.stringContaining("all-transparent")]]);
  vi.advanceTimersByTime(3000);
  expect(logged).toHaveLength(1);
  expect(p.reads()).toBeLessThanOrEqual(11); // at most two pixel readbacks per second
  p.pixels[7] = 255;
  vi.advanceTimersByTime(500);
  expect(logged[logged.length - 1]).toEqual(["info", expect.stringContaining("visible pixels again")]);
  p.pixels[7] = 0;
  vi.advanceTimersByTime(2500);
  expect(logged.filter(([level]) => level === "warn")).toHaveLength(2);
});

test("repeated shows cannot postpone the all-transparent warning", () => {
  const p = paintWatch();
  p.watch.expectFrames("show");
  for (let i = 0; i < 8; i++) {
    vi.advanceTimersByTime(250);
    p.watch.expectFrames("show");
  }
  expect(logged).toEqual([["warn", expect.stringContaining("all-transparent")]]);
});

test("a painted emblem stays quiet and an intentional hide stops pixel readbacks", () => {
  const p = paintWatch();
  p.pixels[15] = 1; // even a faint pixel anywhere on the canvas counts
  p.watch.expectFrames("notice");
  vi.advanceTimersByTime(3000);
  expect(logged).toEqual([]);
  p.hide();
  vi.advanceTimersByTime(500);
  const reads = p.reads();
  vi.advanceTimersByTime(5000);
  expect(p.reads()).toBe(reads);
  expect(logged).toEqual([]);
});
