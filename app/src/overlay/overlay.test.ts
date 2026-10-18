import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { noticeDurationMs } from "./notice";
import {
  OverlayController,
  type EmblemControl,
  type OverlayEvent,
  type OverlayView,
  noticeDurationMs as exportedFromOverlay,
} from "./overlay";
import type { Mode, Phase } from "./emblem";
import type { ProgressReport } from "./progress";

// ---- the brief's test ----

test("notice duration", () => {
  expect(noticeDurationMs("《了。》")).toBe(1500 + 150 * 4);
});

// ---- more about the duration ----

test("the duration counts characters, not UTF-16 units", () => {
  expect(noticeDurationMs("𠮷")).toBe(1500 + 150);
  expect(noticeDurationMs("")).toBe(1500);
});

test("overlay.ts exports the same function and can be imported without a browser", () => {
  expect(exportedFromOverlay).toBe(noticeDurationMs);
});

// ---- the controller: what the page does with each event ----

// An emblem that keeps the real one's times (appear 833 ms, vanish 467 ms) on the fake clock.
class FakeEmblem implements EmblemControl {
  level = 0;
  readonly log: string[] = [];
  /** The progress calls, kept apart from `log`: most tests are not about them. */
  readonly progressLog: string[] = [];
  get last(): string | undefined {
    return this.log[this.log.length - 1];
  }
  private current: Phase = "hidden";
  private mode: Mode = "steady";
  private since = 0;

  get phase(): Phase {
    const elapsed = Date.now() - this.since;
    if (this.current === "appear" && elapsed >= 833) this.current = this.mode;
    if (this.current === "vanish" && elapsed >= 467) this.current = "hidden";
    return this.current;
  }
  set phase(phase: Phase) {
    this.current = phase;
    this.since = Date.now();
  }
  get appearLeft(): number {
    return this.phase === "appear" ? Math.max(0, 0.833 - (Date.now() - this.since) / 1000) : 0;
  }
  appear(): void {
    this.log.push("appear");
    this.phase = "appear";
  }
  vanish(): void {
    this.log.push("vanish");
    this.phase = "vanish";
  }
  set(mode: Mode): void {
    this.log.push(`set:${mode}`);
    this.mode = mode;
    const phase = this.phase;
    if (phase !== "hidden" && phase !== "vanish" && phase !== "appear") this.current = mode;
  }
  speak(seconds: number): void {
    this.log.push(`speak:${seconds.toFixed(3)}`);
  }
  size(px: number): void {
    this.log.push(`size:${px}`);
  }
  progress(r: ProgressReport): void {
    this.progressLog.push(`progress:${r.stage}:${r.done}/${r.total}:${r.audioSeconds}`);
  }
  clearProgress(): void {
    this.progressLog.push("clear");
  }
}

class FakeView implements OverlayView {
  readonly log: string[] = [];
  get last(): string | undefined {
    return this.log[this.log.length - 1];
  }
  layout(px: number): void {
    this.log.push(`layout:${px}`);
  }
  showLines(zh: string | null, ja: string | null): void {
    this.log.push(`lines:${zh ?? "-"}|${ja ?? "-"}`);
  }
  clearLines(): void {
    this.log.push("clear");
  }
}

let emblem: FakeEmblem;
let view: FakeView;
let controller: OverlayController;
const send = (event: OverlayEvent) => controller.handle(event);

beforeEach(() => {
  vi.useFakeTimers();
  emblem = new FakeEmblem();
  view = new FakeView();
  controller = new OverlayController(emblem, view);
});
afterEach(() => {
  vi.useRealTimers();
});

const ZH = "《告。這個視窗不接受貼上。》";
const JA = "《告。このウィンドウには貼り付けできません。》";

test("show makes the emblem appear and then sets the mode", () => {
  send({ type: "show", mode: "listening" });
  expect(emblem.log).toEqual(["appear", "set:listening"]);
});

test("show on a visible emblem only sets the mode", () => {
  emblem.phase = "listening";
  send({ type: "show", mode: "thinking" });
  expect(emblem.log).toEqual(["set:thinking"]);
});

test("show while the emblem is vanishing makes it appear again", () => {
  emblem.phase = "vanish";
  send({ type: "show", mode: "listening" });
  expect(emblem.log).toEqual(["appear", "set:listening"]);
});

test("mode and level go straight to the emblem", () => {
  emblem.phase = "listening";
  send({ type: "mode", mode: "thinking" });
  send({ type: "level", value: 0.4 });
  expect(emblem.log).toEqual(["set:thinking"]);
  expect(emblem.level).toBe(0.4);
});

test("hide plays the vanish animation", () => {
  emblem.phase = "listening";
  send({ type: "hide" });
  expect(emblem.log).toEqual(["vanish"]);
});

test("dictation never shows subtitles", () => {
  send({ type: "show", mode: "listening" });
  send({ type: "mode", mode: "thinking" });
  send({ type: "hide" });
  vi.advanceTimersByTime(60_000);
  expect(view.log.filter((l) => l.startsWith("lines"))).toEqual([]);
});

test("a notice on a hidden emblem: appear, the words after the animation, vanish after the duration", () => {
  send({ type: "notice", zh: ZH, ja: JA });
  expect(emblem.log).toEqual(["appear", "set:steady"]);
  expect(view.log).toEqual([]);

  vi.advanceTimersByTime(832);
  expect(view.log).toEqual([]); // the anime appear animation still plays (0.833 s)

  vi.advanceTimersByTime(1);
  const duration = noticeDurationMs(ZH);
  expect(view.log).toEqual([`lines:${ZH}|${JA}`]);
  expect(emblem.last).toBe(`speak:${(duration / 1000).toFixed(3)}`);

  vi.advanceTimersByTime(duration - 1);
  expect(view.log).toEqual([`lines:${ZH}|${JA}`]); // the words stay for the whole duration
  expect(emblem.log).not.toContain("vanish");
  vi.advanceTimersByTime(1);
  expect(view.last).toBe("clear");
  expect(emblem.last).toBe("vanish");
});

test("a notice on a visible emblem shows its words at once", () => {
  emblem.phase = "thinking";
  send({ type: "notice", zh: ZH, ja: JA });
  expect(emblem.log).toEqual(["set:steady"]);
  vi.advanceTimersByTime(0);
  expect(view.log).toEqual([`lines:${ZH}|${JA}`]);
});

test("a notice that arrives while the emblem is appearing waits for the animation to end", () => {
  send({ type: "show", mode: "listening" });
  vi.advanceTimersByTime(333); // half a second of the appear animation is left
  view.log.length = 0;
  send({ type: "notice", zh: ZH, ja: JA });
  expect(emblem.log).toEqual(["appear", "set:listening", "set:steady"]); // no second appear
  vi.advanceTimersByTime(495);
  expect(view.log).toEqual([]);
  vi.advanceTimersByTime(10);
  expect(view.log).toEqual([`lines:${ZH}|${JA}`]);
});

test("a notice that arrives while another is showing waits its turn", () => {
  const first = { zh: "《了。》", ja: "《了。》" };
  const second = { zh: "《否。》", ja: "《否。》" };
  send({ type: "notice", ...first });
  vi.advanceTimersByTime(833);
  expect(view.log).toEqual(["lines:《了。》|《了。》"]);

  send({ type: "notice", ...second });
  vi.advanceTimersByTime(noticeDurationMs(first.zh) - 1);
  expect(view.log).toEqual(["lines:《了。》|《了。》"]); // still the first one
  expect(emblem.log.filter((l) => l === "vanish")).toEqual([]);

  vi.advanceTimersByTime(1); // the first one is over...
  expect(view.last).toBe("clear");
  expect(emblem.log.filter((l) => l === "vanish")).toEqual([]); // ...but the emblem stays for the next
  vi.advanceTimersByTime(1); // (a timer made inside a timer waits at least 1 ms on the fake clock)
  expect(view.last).toBe("lines:《否。》|《否。》");

  vi.advanceTimersByTime(noticeDurationMs(second.zh));
  expect(emblem.last).toBe("vanish");
  expect(emblem.log.filter((l) => l === "appear")).toHaveLength(1);
});

test("three notices are shown one after the other, in order", () => {
  for (const n of ["一", "二", "三"]) send({ type: "notice", zh: n, ja: n });
  vi.advanceTimersByTime(60_000);
  expect(view.log.filter((l) => l.startsWith("lines"))).toEqual(["lines:一|一", "lines:二|二", "lines:三|三"]);
  expect(emblem.log.filter((l) => l === "vanish")).toHaveLength(1);
});

test("hide during a notice cancels it and the notices that wait", () => {
  send({ type: "notice", zh: ZH, ja: JA });
  send({ type: "notice", zh: "《了。》", ja: "《了。》" });
  vi.advanceTimersByTime(1000);
  send({ type: "hide" });
  expect(view.last).toBe("clear");
  expect(emblem.last).toBe("vanish");

  const before = [...view.log];
  vi.advanceTimersByTime(60_000);
  expect(view.log).toEqual(before); // nothing comes back
});

test("show during a notice cancels it: dictation wins", () => {
  send({ type: "notice", zh: ZH, ja: JA });
  vi.advanceTimersByTime(1000);
  expect(view.log).toEqual([`lines:${ZH}|${JA}`]);
  emblem.log.length = 0;

  send({ type: "show", mode: "listening" });
  expect(view.last).toBe("clear");
  expect(emblem.log).toEqual(["set:listening"]); // already visible
  vi.advanceTimersByTime(60_000);
  expect(emblem.log).toEqual(["set:listening"]); // the notice does not end with a vanish
});

test("settings resize the emblem and move the subtitles with it", () => {
  send({ type: "settings", px: 194, subtitles: "both" });
  expect(emblem.log).toEqual(["size:194"]);
  expect(view.log).toEqual(["layout:194"]);
});

test("settings with a size that makes no sense are ignored", () => {
  send({ type: "settings", px: 0, subtitles: "both" });
  send({ type: "settings", px: Number.NaN, subtitles: "both" });
  expect(emblem.log).toEqual([]);
  expect(view.log).toEqual([]);
});

test.each([
  ["both", `lines:${ZH}|${JA}`],
  ["zh", `lines:${ZH}|-`],
  ["ja", `lines:-|${JA}`],
])("subtitles %s", (subtitles, expected) => {
  send({ type: "settings", px: 150, subtitles });
  view.log.length = 0;
  send({ type: "notice", zh: ZH, ja: JA });
  vi.advanceTimersByTime(833);
  expect(view.log).toEqual([expected]);
});

test("subtitles off: no words, but the emblem still appears and vanishes", () => {
  send({ type: "settings", px: 150, subtitles: "off" });
  view.log.length = 0;
  send({ type: "notice", zh: ZH, ja: JA });
  vi.advanceTimersByTime(833 + noticeDurationMs(ZH));
  expect(view.log.filter((l) => l.startsWith("lines"))).toEqual([]);
  expect(emblem.log).toContain("appear");
  expect(emblem.last).toBe("vanish");
});

test("an unknown subtitle setting shows both lines", () => {
  send({ type: "settings", px: 150, subtitles: "klingon" });
  view.log.length = 0;
  send({ type: "notice", zh: ZH, ja: JA });
  vi.advanceTimersByTime(833);
  expect(view.log).toEqual([`lines:${ZH}|${JA}`]);
});

test("Japanese only falls back to the Chinese line when there is no Japanese", () => {
  send({ type: "settings", px: 150, subtitles: "ja" });
  view.log.length = 0;
  send({ type: "notice", zh: ZH, ja: "" });
  vi.advanceTimersByTime(833);
  expect(view.log).toEqual([`lines:${ZH}|-`]);
});

// ---- the progress after the key is released ----

test("progress goes to the emblem, without words", () => {
  send({ type: "show", mode: "listening" });
  send({ type: "mode", mode: "thinking" });
  send({ type: "progress", stage: "transcribing", done: 1, total: 3, audio_seconds: 4.5 });
  send({ type: "progress", stage: "cleaning", done: 3, total: 3, audio_seconds: 4.5 });
  send({ type: "progress", stage: "done", done: 0, total: 0, audio_seconds: 0 });
  send({ type: "hide" });
  vi.advanceTimersByTime(60_000);
  expect(emblem.progressLog).toEqual([
    "clear", // the show starts without the last dictation's progress
    "progress:transcribing:1/3:4.5",
    "progress:cleaning:3/3:4.5",
    "progress:done:0/0:0",
  ]);
  expect(view.log.filter((l) => l.startsWith("lines"))).toEqual([]);
  expect(emblem.last).toBe("vanish");
});

test("a notice after the progress brings the whole ring back", () => {
  emblem.phase = "thinking";
  send({ type: "progress", stage: "done", done: 0, total: 0, audio_seconds: 0 });
  send({ type: "notice", zh: ZH, ja: JA });
  expect(emblem.progressLog).toEqual(["progress:done:0/0:0", "clear"]);
});
