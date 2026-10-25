import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { noticeDurationMs } from "./notice";
import { COMPLETION_MS, formatProgress } from "./progress-caption";
import { WAITING_LINE_MS } from "./waiting-lines";
import {
  HOLD_MS,
  OverlayController,
  type AskButtons,
  type EmblemControl,
  type HoldState,
  type OverlayEvent,
  type OverlayView,
  type UiLanguage,
} from "./overlay";
import type { Mode, Phase } from "./emblem";

// ---- how long a notice stays ----

test("notice duration", () => {
  expect(noticeDurationMs("《了。》")).toBe(1500 + 150 * 4);
});

// ---- more about the duration ----

test("the duration counts characters, not UTF-16 units", () => {
  expect(noticeDurationMs("𠮷")).toBe(1500 + 150);
  expect(noticeDurationMs("")).toBe(1500);
});

test("a long line stays 12 s at most", () => {
  expect(noticeDurationMs("字".repeat(70))).toBe(12_000);
  expect(noticeDurationMs("字".repeat(71))).toBe(12_000);
  expect(noticeDurationMs("字".repeat(400))).toBe(12_000);
  expect(noticeDurationMs("字".repeat(69))).toBe(1500 + 150 * 69);
});

// ---- the controller: what the page does with each event ----

// An emblem that keeps the real one's times (appear 833 ms, vanish 467 ms) on the fake clock.
class FakeEmblem implements EmblemControl {
  level = 0;
  readonly log: string[] = [];
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
}

class FakeView implements OverlayView {
  percent: number | null = null;
  readonly percentages: Array<number | null> = [];
  showProgress(percent: number | null): void {
    this.percent = percent;
    this.percentages.push(percent);
    this.checkOnePlace();
  }
  /** The waiting line in the caption now, and every change of it (null: hidden), kept apart from `log`. */
  waiting: string | null = null;
  readonly waitings: Array<string | null> = [];
  readonly waitingLanguages: UiLanguage[] = [];
  showWaiting(text: string | null, language: UiLanguage): void {
    this.waiting = text;
    this.waitings.push(text);
    if (text !== null) this.waitingLanguages.push(language);
    this.checkOnePlace();
  }
  readonly log: string[] = [];
  readonly languages: UiLanguage[] = [];
  readonly asked: AskButtons[] = [];
  /** The subtitle line on screen now, if any. */
  private line: string | null = null;
  /**
   * The caption and the subtitle line stand in one place (overlay.css), so they must never be on
   * screen together: each time they were, what was on screen.
   */
  readonly overlaps: string[] = [];
  private checkOnePlace(): void {
    const caption = this.waiting ?? (this.percent === null ? null : String(this.percent));
    if (caption !== null && this.line !== null) this.overlaps.push(`${caption} / ${this.line}`);
  }
  get last(): string | undefined {
    return this.log[this.log.length - 1];
  }
  layout(px: number): void {
    this.log.push(`layout:${px}`);
  }
  showLine(text: string, language: UiLanguage): void {
    this.log.push(`line:${text}`);
    this.languages.push(language);
    this.line = text;
    this.checkOnePlace();
  }
  clearLine(): void {
    this.log.push("clear");
    this.line = null;
  }
  showAsk(buttons: AskButtons): void {
    this.asked.push(buttons);
    this.log.push(`ask:${buttons.yes}|${buttons.no}|${buttons.tier}`);
  }
  setHold(state: HoldState): void {
    this.log.push(`hold:${state}`);
  }
  setChoice(choice: "yes" | "no" | "always" | null): void {
    this.log.push(`choice:${choice}`);
  }
  hideAsk(): void {
    this.log.push("ask-hide");
  }
}

let emblem: FakeEmblem;
let view: FakeView;
let controller: OverlayController;
let confirm: ReturnType<typeof vi.fn<(id: string, approved: boolean, always?: boolean) => Promise<unknown>>>;
const send = (event: OverlayEvent) => controller.handle(event);

beforeEach(() => {
  vi.useFakeTimers();
  emblem = new FakeEmblem();
  view = new FakeView();
  confirm = vi.fn(async () => undefined);
  controller = new OverlayController(emblem, view, { confirm });
});
afterEach(() => {
  vi.useRealTimers();
  // Whatever the test did, the caption and a line never stood in their one place together.
  expect(view.overlaps).toEqual([]);
});

// The notice for a window that refuses the paste (ELEVATED in notices.rs).
const ZH = "《告。此視窗權限較高，無法貼上。文字已複製，請按 Ctrl+V。》";
const JA = "《告。権限の高いウィンドウには貼り付けできません。コピー済みです。Ctrl+V で貼り付けてください。》";
const EN = "《Notice. This window has higher privileges and refuses the paste. The text is copied. Press Ctrl+V.》";
const settings = (subtitles: string, language: string = "zh-TW", px = 150): OverlayEvent => ({ type: "settings", px, subtitles, language });

const lines = (): string[] => view.log.filter((line) => line.startsWith("line:"));

test("public turn progress replaces the previous subtitle without rotating or reviving a finished question", () => {
  send({ type: "show", mode: "listening", line: "Hello" });
  send({ type: "mode", mode: "thinking", quiet: true });
  send({ type: "step", text: "Checking the schedule.", language: "en" });
  expect(view.waiting).toBe("Checking the schedule.");
  send({ type: "step", text: "Found two meetings.", language: "en" });
  vi.advanceTimersByTime(10_000);
  expect(view.waiting).toBe("Found two meetings.");
  send({ type: "say", prefix: "Answer", text: "Two meetings." });
  expect(view.waiting).toBeNull();
  send({ type: "step", text: "Late event", language: "en" });
  expect(view.waiting).toBeNull();
});

test("the fake page notices a caption and a line that stand in their one place together", () => {
  view.showLine("《答。好的》", "zh-TW");
  view.showProgress(42);
  view.showWaiting("《告。解析中……》", "zh-TW");
  expect(view.overlaps).toEqual(["42 / 《答。好的》", "《告。解析中……》 / 《答。好的》"]);
  view.overlaps.splice(0); // seen; the check after each test is for the controller
});

test("learned words use the notice lifecycle", () => {
  const text = "《了。「Typeless」已加入詞典。》";
  send({ type: "learned", text });
  expect(emblem.log).toEqual(["appear", "set:steady"]);
  vi.advanceTimersByTime(833);
  expect(view.last).toBe(`line:${text}`);
  vi.advanceTimersByTime(noticeDurationMs(text) - 1);
  expect(emblem.log).not.toContain("vanish");
  vi.advanceTimersByTime(1);
  expect(emblem.last).toBe("vanish");
});

test("learned words that arrive during a dictation leave it alone and appear after its completion", () => {
  send({ type: "show", mode: "listening" });
  vi.advanceTimersByTime(1000);
  send({ type: "learned", text: "learned" });
  send({ type: "mode", mode: "thinking" });
  send({ type: "progress", stage: "transcribing", done: 1, total: 3, audio_seconds: 5 });
  vi.advanceTimersByTime(1500);
  expect(emblem.log).toEqual(["appear", "set:listening", "set:thinking"]);
  expect(view.percent).toBe(20);
  expect(lines()).toEqual([]);
  expect(controller.expectsPaint).toBe(true);
  send({ type: "complete" });
  expect(view.percent).toBe(100);
  expect(lines()).toEqual([]);
  vi.advanceTimersByTime(COMPLETION_MS);
  expect(view.percent).toBeNull();
  vi.advanceTimersByTime(1); // the words follow in a timer of their own
  expect(lines()).toEqual(["line:learned"]);
  expect(emblem.log).not.toContain("vanish");
  vi.advanceTimersByTime(noticeDurationMs("learned"));
  expect(emblem.last).toBe("vanish");
});

test("learned words that waited for a cancelled dictation take over the emblem instead of a vanish", () => {
  send({ type: "show", mode: "listening" });
  vi.advanceTimersByTime(1000);
  send({ type: "learned", text: "learned" });
  send({ type: "hide" });
  expect(emblem.log).toEqual(["appear", "set:listening", "set:steady"]);
  vi.advanceTimersByTime(0);
  expect(lines()).toEqual(["line:learned"]);
  vi.advanceTimersByTime(noticeDurationMs("learned"));
  expect(emblem.log.filter((entry) => entry === "vanish")).toHaveLength(1);
  expect(emblem.last).toBe("vanish");
});

test("a dictation that starts while learned words wait does not drop them", () => {
  send({ type: "show", mode: "listening" });
  send({ type: "learned", text: "learned" });
  send({ type: "complete" });
  vi.advanceTimersByTime(100);
  send({ type: "show", mode: "listening" }); // the next dictation takes the emblem back
  vi.advanceTimersByTime(COMPLETION_MS * 3);
  expect(lines()).toEqual([]);
  send({ type: "complete" });
  vi.advanceTimersByTime(COMPLETION_MS + 1);
  expect(lines()).toEqual(["line:learned"]);
});

test("a notice ends the dictation it interrupts, and the learned words that waited are still told", () => {
  send({ type: "show", mode: "listening" });
  vi.advanceTimersByTime(1000);
  send({ type: "learned", text: "learned" });
  send({ type: "notice", text: ZH });
  vi.advanceTimersByTime(60_000);
  expect(lines()).toHaveLength(2);
  expect(lines()).toContain("line:learned");
  expect(lines()).toContain(`line:${ZH}`);
  expect(emblem.last).toBe("vanish");
});

test("notices and learned words are told one after the other, in the order they came", () => {
  send({ type: "notice", text: ZH });
  send({ type: "learned", text: "learned" });
  send({ type: "notice", text: "ordinary" });
  vi.advanceTimersByTime(60_000);
  expect(lines()).toEqual([`line:${ZH}`, "line:learned", "line:ordinary"]);
  expect(emblem.log.filter((entry) => entry === "appear")).toHaveLength(1);
  expect(emblem.last).toBe("vanish");
});

test("a new show cancels learned words that wait for the appear animation", () => {
  send({ type: "learned", text: "learned" });
  send({ type: "show", mode: "listening" });
  vi.advanceTimersByTime(60_000);
  expect(emblem.log).toEqual(["appear", "set:steady", "set:listening"]);
  expect(lines()).toEqual([]);
});

test("hide cancels learned words that wait for the appear animation", () => {
  send({ type: "learned", text: "learned" });
  send({ type: "hide" });
  vi.advanceTimersByTime(60_000);
  expect(emblem.log).toEqual(["appear", "set:steady", "vanish"]);
  expect(controller.expectsPaint).toBe(false);
  expect(lines()).toEqual([]);
});

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
  expect(view.log.filter((l) => l.startsWith("line"))).toEqual([]);
});

const GREETING = "《告。主人，請下達指示。》";

test("a question is greeted beside the listening emblem until Raphael has heard it", () => {
  send({ type: "show", mode: "listening", line: GREETING });
  expect(lines()).toEqual([]);
  vi.advanceTimersByTime(833);
  expect(lines()).toEqual([`line:${GREETING}`]);
  expect(view.languages).toEqual(["zh-TW"]);
  // the ring still follows the microphone: Raphael does not speak the greeting
  expect(emblem.log).toEqual(["appear", "set:listening"]);
  vi.advanceTimersByTime(60_000);
  expect(view.last).toBe(`line:${GREETING}`);
  send({ type: "mode", mode: "thinking", quiet: true });
  expect(view.last).toBe("clear");
  send({ type: "say", prefix: "答", text: "好。" });
  vi.advanceTimersByTime(10);
  expect(lines()).toEqual([`line:${GREETING}`, "line:《答。好。》"]);
});

test("a question that took a dictation over is greeted on its emblem, which does not appear again", () => {
  send({ type: "show", mode: "listening" });
  vi.advanceTimersByTime(300);
  send({ type: "show", mode: "listening", line: GREETING });
  expect(emblem.log.filter((entry) => entry === "appear")).toHaveLength(1);
  vi.advanceTimersByTime(500);
  expect(lines()).toEqual([]);
  vi.advanceTimersByTime(100);
  expect(lines()).toEqual([`line:${GREETING}`]);
});

test("the greeting is written even with the subtitles off, as answers are", () => {
  send(settings("off"));
  send({ type: "show", mode: "listening", line: GREETING });
  vi.advanceTimersByTime(833);
  expect(lines()).toEqual([`line:${GREETING}`]);
});

test("a cancelled question takes its greeting away, and the next dictation has none", () => {
  send({ type: "show", mode: "listening", line: GREETING });
  vi.advanceTimersByTime(833);
  send({ type: "hide" });
  expect(view.last).toBe("clear");
  vi.advanceTimersByTime(1000);
  send({ type: "show", mode: "listening" });
  vi.advanceTimersByTime(60_000);
  expect(lines()).toEqual([`line:${GREETING}`]);
});

test("a greeting whose question ends before the emblem has appeared is never written", () => {
  send({ type: "show", mode: "listening", line: GREETING });
  vi.advanceTimersByTime(400);
  send({ type: "mode", mode: "thinking", quiet: true });
  vi.advanceTimersByTime(60_000);
  expect(lines()).toEqual([]);
});

test("a notice that ends the question replaces the greeting, which does not come back", () => {
  send({ type: "show", mode: "listening", line: GREETING });
  vi.advanceTimersByTime(833);
  send({ type: "notice", text: ZH });
  vi.advanceTimersByTime(1);
  expect(lines()).toEqual([`line:${GREETING}`, `line:${ZH}`]);
  vi.advanceTimersByTime(noticeDurationMs(ZH) - 2);
  expect(view.last).toBe(`line:${ZH}`);
});

test("a question being thought about shows no percentage, and its answer is spoken", () => {
  send({ type: "show", mode: "listening" });
  send({ type: "mode", mode: "thinking", quiet: true });
  vi.advanceTimersByTime(3000);
  expect(view.percentages.filter((percent) => percent !== null)).toEqual([]);
  send({ type: "say", prefix: "答", text: "好。" });
  vi.advanceTimersByTime(10);
  expect(view.percentages.filter((percent) => percent !== null)).toEqual([]);
});

test("processing reports its percentage beside the emblem", () => {
  send({ type: "show", mode: "listening" });
  send({ type: "mode", mode: "thinking" });
  send({ type: "progress", stage: "transcribing", done: 1, total: 3, audio_seconds: 5 });
  vi.advanceTimersByTime(1500);
  expect(view.percent).toBe(20);
  expect(formatProgress(view.percent!)).toBe("《 20 % 》");
  send({ type: "progress", stage: "cleaning", done: 3, total: 3, audio_seconds: 5 });
  vi.advanceTimersByTime(1000);
  expect(view.percent).toBeGreaterThanOrEqual(60);
  expect(view.percent).toBeLessThan(100);
  expect(lines()).toEqual([]);
});

test("text readiness waits for delivery before announcing completion", () => {
  send({ type: "show", mode: "listening" });
  send({ type: "mode", mode: "thinking" });
  send({ type: "progress", stage: "done", done: 0, total: 0, audio_seconds: 0 });
  expect(view.percent).toBe(99);
  send({ type: "complete" });
  expect(view.percent).toBe(100);
  expect(formatProgress(view.percent!)).toBe("《 100 % 》");
  vi.advanceTimersByTime(COMPLETION_MS - 1);
  expect(emblem.last).not.toBe("vanish");
  vi.advanceTimersByTime(1);
  expect(emblem.last).toBe("vanish");
});

test("a completion timer cannot erase a new recording or its progress", () => {
  send({ type: "show", mode: "listening" });
  send({ type: "complete" });
  vi.advanceTimersByTime(100);
  send({ type: "show", mode: "listening" });
  send({ type: "mode", mode: "thinking" });
  send({ type: "progress", stage: "cleaning", done: 0, total: 0, audio_seconds: 8 });
  vi.advanceTimersByTime(3000);
  expect(view.percent).toBeGreaterThanOrEqual(60);
  expect(view.percent).toBeLessThan(100);
  expect(emblem.last).not.toBe("vanish");
});

test("cancel stops processing captions and ignores late reports and completion", () => {
  send({ type: "show", mode: "listening" });
  send({ type: "mode", mode: "thinking" });
  send({ type: "progress", stage: "cleaning", done: 0, total: 0, audio_seconds: 5 });
  send({ type: "hide" });
  const afterHide = view.log.length;
  send({ type: "progress", stage: "done", done: 0, total: 0, audio_seconds: 0 });
  send({ type: "complete" });
  vi.advanceTimersByTime(60_000);
  expect(view.log).toHaveLength(afterHide);
  expect(emblem.last).toBe("vanish");
  expect(controller.expectsPaint).toBe(false);
});

test("numeric progress stays visible with subtitles off and a notice takes over cleanly", () => {
  send(settings("off"));
  send({ type: "show", mode: "listening" });
  send({ type: "mode", mode: "thinking" });
  send({ type: "progress", stage: "cleaning", done: 0, total: 0, audio_seconds: 5 });
  vi.advanceTimersByTime(1000);
  expect(lines()).toEqual([]);
  expect(view.percent).toBeGreaterThanOrEqual(60);
  send(settings("on"));
  send({ type: "notice", text: ZH });
  vi.advanceTimersByTime(1);
  expect(view.last).toBe(`line:${ZH}`);
  expect(view.percent).toBeNull();
});

test("a notice on a hidden emblem: appear, the words after the animation, vanish after the duration", () => {
  send({ type: "notice", text: ZH });
  expect(emblem.log).toEqual(["appear", "set:steady"]);
  expect(view.log).toEqual([]);

  vi.advanceTimersByTime(832);
  expect(view.log).toEqual([]); // the anime appear animation still plays (0.833 s)

  vi.advanceTimersByTime(1);
  const duration = noticeDurationMs(ZH);
  expect(view.log).toEqual([`line:${ZH}`]);
  expect(emblem.last).toBe(`speak:${(duration / 1000).toFixed(3)}`);

  vi.advanceTimersByTime(duration - 1);
  expect(view.log).toEqual([`line:${ZH}`]); // the words stay for the whole duration
  expect(emblem.log).not.toContain("vanish");
  vi.advanceTimersByTime(1);
  expect(view.last).toBe("clear");
  expect(emblem.last).toBe("vanish");
});

test("a notice on a visible emblem shows its words at once", () => {
  emblem.phase = "thinking";
  send({ type: "notice", text: ZH });
  expect(emblem.log).toEqual(["set:steady"]);
  vi.advanceTimersByTime(0);
  expect(view.log).toEqual([`line:${ZH}`]);
});

test("a notice that arrives while the emblem is appearing waits for the animation to end", () => {
  send({ type: "show", mode: "listening" });
  vi.advanceTimersByTime(333); // half a second of the appear animation is left
  view.log.length = 0;
  send({ type: "notice", text: ZH });
  expect(emblem.log).toEqual(["appear", "set:listening", "set:steady"]); // no second appear
  vi.advanceTimersByTime(495);
  expect(view.log).toEqual([]);
  vi.advanceTimersByTime(10);
  expect(view.log).toEqual([`line:${ZH}`]);
});

test("a notice that arrives while another is showing waits its turn", () => {
  const first = { text: "《了。》" };
  const second = { text: "《否。》" };
  send({ type: "notice", ...first });
  vi.advanceTimersByTime(833);
  expect(view.log).toEqual(["line:《了。》"]);

  send({ type: "notice", ...second });
  vi.advanceTimersByTime(noticeDurationMs(first.text) - 1);
  expect(view.log).toEqual(["line:《了。》"]); // still the first one
  expect(emblem.log.filter((l) => l === "vanish")).toEqual([]);

  vi.advanceTimersByTime(1); // the first one is over...
  expect(view.last).toBe("clear");
  expect(emblem.log.filter((l) => l === "vanish")).toEqual([]); // ...but the emblem stays for the next
  vi.advanceTimersByTime(1); // (a timer made inside a timer waits at least 1 ms on the fake clock)
  expect(view.last).toBe("line:《否。》");

  vi.advanceTimersByTime(noticeDurationMs(second.text));
  expect(emblem.last).toBe("vanish");
  expect(emblem.log.filter((l) => l === "appear")).toHaveLength(1);
});

test("three notices are shown one after the other, in order", () => {
  for (const n of ["一", "二", "三"]) send({ type: "notice", text: n });
  vi.advanceTimersByTime(60_000);
  expect(lines()).toEqual(["line:一", "line:二", "line:三"]);
  expect(emblem.log.filter((l) => l === "vanish")).toHaveLength(1);
});

test("hide during a notice cancels it and the notices that wait", () => {
  send({ type: "notice", text: ZH });
  send({ type: "notice", text: "《了。》" });
  vi.advanceTimersByTime(1000);
  send({ type: "hide" });
  expect(view.last).toBe("clear");
  expect(emblem.last).toBe("vanish");

  const before = [...view.log];
  vi.advanceTimersByTime(60_000);
  expect(view.log).toEqual(before); // nothing comes back
});

test("show during a notice cancels it: dictation wins", () => {
  send({ type: "notice", text: ZH });
  vi.advanceTimersByTime(1000);
  expect(view.log).toEqual([`line:${ZH}`]);
  emblem.log.length = 0;

  send({ type: "show", mode: "listening" });
  expect(view.last).toBe("clear");
  expect(emblem.log).toEqual(["set:listening"]); // already visible
  vi.advanceTimersByTime(60_000);
  expect(emblem.log).toEqual(["set:listening"]); // the notice does not end with a vanish
});

// ---- the settings ----

test("settings resize the emblem and move the subtitles with it", () => {
  send(settings("on", "zh-TW", 194));
  expect(emblem.log).toEqual(["size:194"]);
  expect(view.log).toEqual(["layout:194"]);
});

test("settings with a size that makes no sense are ignored", () => {
  send(settings("on", "zh-TW", 0));
  send(settings("on", "zh-TW", Number.NaN));
  expect(emblem.log).toEqual([]);
  expect(view.log).toEqual([]);
});

test.each(["on", "both", "zh", "ja", "klingon"])("subtitles %s: reports are written", (subtitles) => {
  send(settings(subtitles));
  view.log.length = 0;
  send({ type: "notice", text: ZH });
  vi.advanceTimersByTime(833);
  expect(view.log).toEqual([`line:${ZH}`]);
});

test("subtitles off: no words from reports, but the emblem still appears and vanishes", () => {
  send(settings("off"));
  view.log.length = 0;
  send({ type: "notice", text: ZH });
  send({ type: "learned", text: "learned" });
  vi.advanceTimersByTime(60_000);
  expect(view.log.filter((l) => l.startsWith("line"))).toEqual([]);
  expect(emblem.log).toContain("appear");
  expect(emblem.last).toBe("vanish");
});

test("the line is written in the language of the settings, and an unknown language changes nothing", () => {
  send(settings("on", "ja"));
  send({ type: "notice", text: JA });
  vi.advanceTimersByTime(60_000);
  send(settings("on", "klingon"));
  send({ type: "notice", text: JA });
  vi.advanceTimersByTime(60_000);
  send(settings("on", "en"));
  send({ type: "notice", text: EN });
  vi.advanceTimersByTime(60_000);
  expect(view.languages).toEqual(["ja", "ja", "en"]);
});

// ---- Raphael speaks ----

test("say writes 《prefix。text》 after the appear animation, for as long as a notice of that length", () => {
  send({ type: "say", prefix: "答", text: "台北現在 24 度，多雲。" });
  const line = "《答。台北現在 24 度，多雲。》";
  expect(emblem.log).toEqual(["appear", "set:steady"]);
  vi.advanceTimersByTime(832);
  expect(view.log).toEqual([]);
  vi.advanceTimersByTime(1);
  expect(view.log).toEqual([`line:${line}`]);
  const duration = noticeDurationMs(line);
  expect(duration).toBe(1500 + 150 * [...line].length);
  expect(emblem.last).toBe(`speak:${(duration / 1000).toFixed(3)}`);
  vi.advanceTimersByTime(duration - 1);
  expect(emblem.log).not.toContain("vanish");
  vi.advanceTimersByTime(1);
  expect(view.last).toBe("clear");
  expect(emblem.last).toBe("vanish");
});

test("a long answer stays 12 s and no longer", () => {
  const text = "字".repeat(80);
  send({ type: "say", prefix: "答", text });
  vi.advanceTimersByTime(833);
  expect(lines()).toEqual([`line:《答。${text}》`]);
  vi.advanceTimersByTime(11_999);
  expect(emblem.log).not.toContain("vanish");
  vi.advanceTimersByTime(1);
  expect(emblem.last).toBe("vanish");
});

test("an English answer reads 《Answer. …》 and is set in English", () => {
  send(settings("on", "en"));
  send({ type: "say", prefix: "Answer", text: "It is 24 degrees." });
  vi.advanceTimersByTime(833);
  expect(lines()).toEqual(["line:《Answer. It is 24 degrees.》"]);
  expect(view.languages).toEqual(["en"]);
});

test("an answer is written whatever the subtitles say; the reports are what that setting silences", () => {
  send(settings("off"));
  send({ type: "say", prefix: "答", text: "好的" });
  vi.advanceTimersByTime(60_000);
  expect(lines()).toEqual(["line:《答。好的》"]);
});

test("an answer ends the thinking that was shown, with its progress", () => {
  send({ type: "show", mode: "listening" });
  send({ type: "mode", mode: "thinking" });
  vi.advanceTimersByTime(1000);
  expect(view.percent).not.toBeNull();
  send({ type: "say", prefix: "答", text: "好的" });
  expect(view.percent).toBeNull();
  vi.advanceTimersByTime(1);
  expect(lines()).toEqual(["line:《答。好的》"]);
  vi.advanceTimersByTime(60_000);
  expect(emblem.last).toBe("vanish");
  expect(controller.expectsPaint).toBe(false);
});

test("an answer comes before the learned words that wait, behind the lines that came earlier", () => {
  send({ type: "show", mode: "listening" });
  send({ type: "learned", text: "learned" });
  send({ type: "say", prefix: "答", text: "一" });
  send({ type: "say", prefix: "答", text: "二" });
  vi.advanceTimersByTime(60_000);
  expect(lines()).toEqual(["line:《答。一》", "line:《答。二》", "line:learned"]);
});

test("a dictation takes the emblem from an answer", () => {
  send({ type: "say", prefix: "答", text: "好的" });
  vi.advanceTimersByTime(1000);
  send({ type: "show", mode: "listening" });
  expect(view.last).toBe("clear");
  vi.advanceTimersByTime(60_000);
  expect(emblem.log).not.toContain("vanish");
});

test("events without words show nothing, and do not wedge the page", () => {
  send({ type: "notice" } as unknown as OverlayEvent); // the older pair of lines, or nothing
  send({ type: "notice", zh: ZH, ja: JA } as unknown as OverlayEvent);
  send({ type: "learned", text: "" });
  send({ type: "say", prefix: "", text: "" });
  send({ type: "say" } as unknown as OverlayEvent);
  vi.advanceTimersByTime(60_000);
  expect(view.log).toEqual([]);
  expect(emblem.log).toEqual([]);
  expect(controller.expectsPaint).toBe(false);
  send({ type: "notice", text: ZH }); // the next one still shows
  vi.advanceTimersByTime(833);
  expect(lines()).toEqual([`line:${ZH}`]);
});

test("an answer with a prefix and no text is still told: 《了。》 is a whole reply", () => {
  send({ type: "say", prefix: "了", text: "" });
  vi.advanceTimersByTime(833);
  expect(lines()).toEqual(["line:《了。》"]);
});

// ---- a question ----

const QUESTION = "《問。要把這段文字複製到剪貼簿嗎？》";
type Ask = Extract<OverlayEvent, { type: "ask" }>;
const ask = (more: Partial<Ask> = {}): OverlayEvent => ({
  type: "ask",
  id: "c1",
  tier: "T2",
  prefix: "問",
  text: "要把這段文字複製到剪貼簿嗎？",
  yes: "是",
  no: "否",
  timeout_ms: 30_000,
  ...more,
});
/** A question with its buttons on screen, and the logs emptied. */
const asked = (more: Partial<Ask> = {}) => {
  send(ask(more));
  vi.advanceTimersByTime(833);
  view.log.length = 0;
  emblem.log.length = 0;
};

test("a question shows its line and its two buttons once the emblem has appeared", () => {
  send(ask());
  expect(emblem.log).toEqual(["appear", "set:steady"]);
  expect(view.log).toEqual(["clear"]); // whatever was on screen goes
  expect(controller.expectsPaint).toBe(true);
  vi.advanceTimersByTime(832);
  expect(view.log).toEqual(["clear"]);
  vi.advanceTimersByTime(1);
  expect(view.log).toEqual(["clear", `line:${QUESTION}`, "ask:是|否|T2"]);
  expect(view.asked[0]).toEqual({ yes: "是", no: "否", tier: "T2", language: "zh-TW" });
  expect(emblem.last).toMatch(/^speak:/);
});

test("the question and its buttons are in the language of the settings", () => {
  send(settings("on", "en"));
  send(ask({ prefix: "Question", text: "Copy this to the clipboard?", yes: "Yes", no: "No" }));
  vi.advanceTimersByTime(833);
  expect(lines()).toEqual(["line:《Question. Copy this to the clipboard?》"]);
  expect(view.asked[0]).toEqual({ yes: "Yes", no: "No", tier: "T2", language: "en" });
});

test("a question is written although the subtitles are off", () => {
  send(settings("off"));
  view.log.length = 0;
  send(ask());
  vi.advanceTimersByTime(833);
  expect(lines()).toEqual([`line:${QUESTION}`]);
  expect(view.log).toContain("ask:是|否|T2");
});

test("a question on a visible emblem shows at once", () => {
  emblem.phase = "thinking";
  send(ask());
  expect(emblem.log).toEqual(["set:steady"]);
  vi.advanceTimersByTime(0);
  expect(view.log).toEqual(["clear", `line:${QUESTION}`, "ask:是|否|T2"]);
});

test("a question ends the thinking that was shown, with its progress; a stray complete leaves it be", () => {
  send({ type: "show", mode: "listening" });
  send({ type: "mode", mode: "thinking" });
  vi.advanceTimersByTime(1000);
  expect(view.percent).not.toBeNull();
  send(ask());
  expect(view.percent).toBeNull();
  vi.advanceTimersByTime(1);
  expect(view.log).toContain("ask:是|否|T2");
  send({ type: "complete" });
  vi.advanceTimersByTime(COMPLETION_MS * 2);
  expect(view.log).not.toContain("ask-hide");
});

test("YES of a T2 question is a click, answered once", () => {
  asked();
  controller.clickAsk("yes");
  expect(confirm).toHaveBeenCalledExactlyOnceWith("c1", true);
  expect(view.log).toEqual(["choice:yes"]);
  controller.clickAsk("yes");
  controller.clickAsk("no");
  controller.holdStart();
  controller.holdEnd(true);
  expect(confirm).toHaveBeenCalledTimes(1);
});

test("NO is a click too, and it is the same for T3", () => {
  asked({ tier: "T3" });
  controller.clickAsk("no");
  expect(confirm).toHaveBeenCalledExactlyOnceWith("c1", false);
  expect(view.log).toEqual(["choice:no"]);
});

test("a click before the buttons are on screen is not an answer", () => {
  send(ask());
  controller.clickAsk("yes");
  controller.clickAsk("no");
  expect(confirm).not.toHaveBeenCalled();
});

test("only a T3 question begins a hold: the page captures the pointer for that and for nothing else", () => {
  expect(controller.holdStart()).toBe(false); // no question
  send(ask({ tier: "T3" }));
  expect(controller.holdStart()).toBe(false); // its buttons are not there yet: the emblem is still appearing
  vi.advanceTimersByTime(833);
  expect(controller.holdStart()).toBe(true);
  controller.holdCancel();
  send(ask({ id: "c2" })); // T2
  vi.advanceTimersByTime(1);
  expect(controller.holdStart()).toBe(false); // its YES is a click, which one takes back by moving off
  controller.clickAsk("no");
  expect(controller.holdStart()).toBe(false); // answered
});

test("a click without a question does nothing", () => {
  controller.clickAsk("yes");
  controller.holdStart();
  controller.holdEnd(true);
  controller.holdCancel();
  expect(confirm).not.toHaveBeenCalled();
  expect(view.log).toEqual([]);
});

test("the YES of a T3 question is not answered by a click", () => {
  asked({ tier: "T3" });
  controller.clickAsk("yes");
  expect(confirm).not.toHaveBeenCalled();
});

test("T3: a hold shorter than the arming time answers nothing", () => {
  asked({ tier: "T3" });
  controller.holdStart();
  expect(view.log).toEqual(["hold:holding"]);
  vi.advanceTimersByTime(HOLD_MS - 1);
  controller.holdEnd(true);
  expect(view.log).toEqual(["hold:holding", "hold:idle"]);
  vi.advanceTimersByTime(5000);
  expect(confirm).not.toHaveBeenCalled();
  expect(view.log).toEqual(["hold:holding", "hold:idle"]);
});

test("T3: held for the arming time, the button arms, and letting go on it answers YES", () => {
  expect(HOLD_MS).toBe(600);
  asked({ tier: "T3" });
  controller.holdStart();
  vi.advanceTimersByTime(HOLD_MS - 1);
  expect(view.log).toEqual(["hold:holding"]);
  vi.advanceTimersByTime(1);
  expect(view.log).toEqual(["hold:holding", "hold:armed"]);
  expect(confirm).not.toHaveBeenCalled(); // armed is not answered: letting go is
  controller.holdEnd(true);
  expect(confirm).toHaveBeenCalledExactlyOnceWith("c1", true);
  expect(view.log).toEqual(["hold:holding", "hold:armed", "choice:yes"]);
});

test("T3: armed, but let go away from the button, answers nothing and disarms", () => {
  asked({ tier: "T3" });
  controller.holdStart();
  vi.advanceTimersByTime(HOLD_MS);
  controller.holdEnd(false);
  expect(view.last).toBe("hold:idle");
  expect(confirm).not.toHaveBeenCalled();
  controller.holdEnd(true); // the release of a later press does not find an armed button
  expect(confirm).not.toHaveBeenCalled();
});

test("T3: a pointer the system took away drops the hold", () => {
  asked({ tier: "T3" });
  controller.holdStart();
  vi.advanceTimersByTime(300);
  controller.holdCancel();
  vi.advanceTimersByTime(HOLD_MS);
  expect(view.log).toEqual(["hold:holding", "hold:idle"]);
  controller.holdEnd(true);
  expect(confirm).not.toHaveBeenCalled();
});

test("T3: a release that never comes does not leave the button armed", () => {
  asked({ tier: "T3" });
  controller.holdStart();
  vi.advanceTimersByTime(HOLD_MS);
  expect(view.last).toBe("hold:armed");
  vi.advanceTimersByTime(5000);
  expect(view.last).toBe("hold:idle");
  controller.holdEnd(true);
  expect(confirm).not.toHaveBeenCalled();
});

test("T3: pressing again starts the hold again", () => {
  asked({ tier: "T3" });
  controller.holdStart();
  vi.advanceTimersByTime(400);
  controller.holdStart();
  vi.advanceTimersByTime(400); // 800 ms since the first press, 400 since the second
  controller.holdEnd(true);
  expect(confirm).not.toHaveBeenCalled();
});

test("T3: NO clicked while YES is being held ends the hold and the YES stays dead", () => {
  asked({ tier: "T3" });
  controller.holdStart();
  vi.advanceTimersByTime(300);
  controller.clickAsk("no");
  expect(confirm).toHaveBeenCalledExactlyOnceWith("c1", false);
  expect(view.log).toEqual(["hold:holding", "hold:idle", "choice:no"]);
  vi.advanceTimersByTime(HOLD_MS);
  expect(view.log).toEqual(["hold:holding", "hold:idle", "choice:no"]); // nothing arms later
  controller.holdEnd(true);
  expect(confirm).toHaveBeenCalledTimes(1);
});

test("a question whose tier is not plainly T2 is asked the strict way", () => {
  asked({ tier: "T9" as Ask["tier"] });
  controller.clickAsk("yes");
  expect(confirm).not.toHaveBeenCalled();
  controller.holdStart();
  vi.advanceTimersByTime(HOLD_MS);
  controller.holdEnd(true);
  expect(confirm).toHaveBeenCalledExactlyOnceWith("c1", true);
});

test("a hold on the YES of a T2 question is no answer: its YES is a click", () => {
  asked();
  controller.holdStart();
  vi.advanceTimersByTime(HOLD_MS);
  controller.holdEnd(true);
  expect(confirm).not.toHaveBeenCalled();
  expect(view.log).toEqual([]);
});

test("an answer that could not be sent gives the question back for another try", async () => {
  confirm.mockRejectedValueOnce(new Error("no"));
  asked();
  controller.clickAsk("yes");
  expect(view.log).toEqual(["choice:yes"]);
  await vi.advanceTimersByTimeAsync(0);
  expect(view.log).toEqual(["choice:yes", "choice:null"]);
  controller.clickAsk("yes");
  expect(confirm).toHaveBeenCalledTimes(2);
});

test("an answer that could not be sent is reported, without the words on screen", async () => {
  const log = vi.fn();
  controller = new OverlayController(emblem, view, { confirm: () => Promise.reject(new Error("denied")), log });
  asked();
  controller.clickAsk("no");
  await vi.advanceTimersByTimeAsync(0);
  expect(log).toHaveBeenCalledOnce();
  expect(log.mock.calls[0][0]).toBe("warn");
  expect(log.mock.calls[0][1]).toContain("denied");
  expect(log.mock.calls[0][1]).not.toContain("剪貼簿");
});

test("an answer that could not be sent, and was overtaken by the next event, does not touch the new screen", async () => {
  let fail!: (reason: Error) => void;
  confirm.mockReturnValueOnce(new Promise((_, reject) => (fail = reject)));
  asked();
  controller.clickAsk("yes");
  send({ type: "say", prefix: "答", text: "好的" });
  vi.advanceTimersByTime(1);
  view.log.length = 0;
  fail(new Error("late"));
  await vi.advanceTimersByTimeAsync(0);
  expect(view.log).toEqual([]);
});

test("a question without an id is not shown", () => {
  const log = vi.fn();
  controller = new OverlayController(emblem, view, { confirm, log });
  send(ask({ id: "" }));
  vi.advanceTimersByTime(60_000);
  expect(view.log).toEqual([]);
  expect(emblem.log).toEqual([]);
  expect(log).toHaveBeenCalledOnce();
});

test("a question without words for its buttons gets a tick and a cross", () => {
  send(ask({ yes: "", no: "" }));
  vi.advanceTimersByTime(833);
  expect(view.log).toContain("ask:✓|✕|T2");
});

test.each([
  ["an answer", { type: "say", prefix: "答", text: "好的" } as OverlayEvent],
  ["a notice", { type: "notice", text: "《告。完成》" } as OverlayEvent],
  ["a dictation", { type: "show", mode: "listening" } as OverlayEvent],
  ["a hide", { type: "hide" } as OverlayEvent],
  ["another question", ask({ id: "c2", text: "另一個問題" })],
])("%s replaces the question: its buttons go and nothing is answered", (_name, event) => {
  asked();
  send(event);
  expect(view.log).toContain("ask-hide");
  controller.clickAsk("yes");
  controller.clickAsk("no");
  expect(confirm).not.toHaveBeenCalled();
  vi.advanceTimersByTime(60_000);
  expect(confirm).not.toHaveBeenCalled();
});

test("the question that replaced a question has its own id", () => {
  asked();
  send(ask({ id: "c2", text: "另一個問題" }));
  vi.advanceTimersByTime(1);
  view.log.length = 0;
  controller.clickAsk("yes");
  expect(confirm).toHaveBeenCalledExactlyOnceWith("c2", true);
});

test("a question that is replaced before its buttons came never shows them", () => {
  send(ask());
  vi.advanceTimersByTime(400);
  send({ type: "hide" });
  vi.advanceTimersByTime(60_000);
  expect(view.log.filter((entry) => entry.startsWith("ask:"))).toEqual([]);
  expect(view.log.filter((entry) => entry.startsWith("line:"))).toEqual([]);
  expect(emblem.last).toBe("vanish");
});

test("learned words wait for a question and are told after it", () => {
  asked();
  send({ type: "learned", text: "learned" });
  vi.advanceTimersByTime(10_000);
  expect(lines()).toEqual([]);
  expect(view.log).not.toContain("ask-hide");
  send({ type: "hide" });
  vi.advanceTimersByTime(1);
  expect(view.log).toContain("ask-hide");
  expect(lines()).toEqual(["line:learned"]);
});

test("the answer to a question comes before the learned words that waited", () => {
  asked();
  send({ type: "learned", text: "learned" });
  controller.clickAsk("yes");
  send({ type: "say", prefix: "了", text: "已複製" });
  vi.advanceTimersByTime(60_000);
  expect(lines()).toEqual(["line:《了。已複製》", "line:learned"]);
});

test("a question nobody replaced is let go when its time and a little more are up", () => {
  asked({ timeout_ms: 5000 }); // 833 ms went by while the emblem appeared
  vi.advanceTimersByTime(5000 + 1500 - 833 - 1);
  expect(view.log).toEqual([]);
  vi.advanceTimersByTime(1);
  expect(view.log).toEqual(["ask-hide", "clear"]);
  expect(emblem.last).toBe("vanish");
  expect(controller.expectsPaint).toBe(false);
  controller.clickAsk("yes");
  expect(confirm).not.toHaveBeenCalled();
});

test("a question that does not say how long it waits waits 30 s", () => {
  asked({ timeout_ms: Number.NaN });
  vi.advanceTimersByTime(30_000 + 1500 - 833 - 1);
  expect(view.log).toEqual([]);
  vi.advanceTimersByTime(1);
  expect(view.log).toContain("ask-hide");
});

test("a question that ran out gives way to the learned words that waited", () => {
  asked({ timeout_ms: 1000 });
  send({ type: "learned", text: "learned" });
  vi.advanceTimersByTime(2500);
  expect(view.log).toContain("ask-hide");
  expect(emblem.log).not.toContain("vanish");
  vi.advanceTimersByTime(1);
  expect(lines()).toEqual(["line:learned"]);
});

test("the answer of the app after the page let go is written as usual", () => {
  asked({ timeout_ms: 1000 });
  vi.advanceTimersByTime(2500);
  send({ type: "say", prefix: "否", text: "已逾時" });
  vi.advanceTimersByTime(833);
  expect(lines()).toEqual(["line:《否。已逾時》"]);
});

test("the timer of a question that was replaced does not let go of the one that came after it", () => {
  asked({ timeout_ms: 1000 });
  send(ask({ id: "c2", timeout_ms: 60_000 }));
  vi.advanceTimersByTime(5000);
  expect(view.log.filter((entry) => entry === "ask-hide")).toEqual(["ask-hide"]); // only the replaced one
  expect(view.log.filter((entry) => entry.startsWith("ask:"))).toEqual(["ask:是|否|T2"]);
});

test("other events leave a question alone", () => {
  asked();
  send({ type: "level", value: 0.5 });
  send({ type: "progress", stage: "done", done: 0, total: 0, audio_seconds: 0 });
  send({ type: "complete" });
  send(settings("off", "ja"));
  vi.advanceTimersByTime(10_000);
  expect(view.log).toEqual(["layout:150"]);
  controller.clickAsk("yes");
  expect(confirm).toHaveBeenCalledExactlyOnceWith("c1", true);
});

// ---- the waiting lines: a question being thought about ----

// The app sends the phrase book of the interface language with the quiet thought; the page only rotates it.
const BOOKS: Record<UiLanguage, string[]> = {
  "zh-TW": ["《告。解析中……》", "《告。並列演算中……》", "《告。檢索最適解……》", "《告。思考加速中……》", "《告。整理情報中……》"],
  ja: ["《告。解析を実行中……》", "《告。並列演算を実行中……》", "《告。最適解を検索中……》", "《告。思考を加速中……》"],
  en: ["《Notice. Analyzing…》", "《Notice. Computing in parallel…》", "《Notice. Seeking the optimum…》", "《Notice. Accelerating thought…》"],
};
const BOOK = BOOKS["zh-TW"];

/** A question that was heard: greeted, then released into quiet thinking with the phrase book. */
const thinkQuietly = (...rest: [lines?: unknown]) => {
  const lines = rest.length > 0 ? rest[0] : BOOK; // `undefined` is a book too: the app sent none
  send({ type: "show", mode: "listening", line: GREETING });
  vi.advanceTimersByTime(833);
  send({ type: "mode", mode: "thinking", quiet: true, lines } as OverlayEvent);
};
/** The lines shown so far, hiding included. */
const shown = (): string[] => view.waitings.filter((text): text is string => text !== null);
/** A `random` that walks through 0 to 1 by a step that is no divisor of a phrase book. */
const walking = (): (() => number) => {
  let n = 0;
  return () => (n++ * 0.137) % 1;
};

test("quiet thinking drops the greeting and starts a line at once, in the caption's place", () => {
  thinkQuietly();
  expect(view.last).toBe("clear");
  expect(shown()).toHaveLength(1);
  expect(BOOK).toContain(view.waiting);
  expect(view.waitingLanguages).toEqual(["zh-TW"]);
  // a waiting line is no subtitle and no percentage, and the emblem does not speak it
  expect(lines()).toEqual([`line:${GREETING}`]);
  expect(view.percentages.filter((percent) => percent !== null)).toEqual([]);
  expect(emblem.log).not.toContainEqual(expect.stringMatching(/^speak:/));
});

test("the line changes every 1.5 s, never to the one it was", () => {
  controller = new OverlayController(emblem, view, { confirm, random: () => 0 });
  thinkQuietly();
  expect(shown()).toHaveLength(1);
  vi.advanceTimersByTime(WAITING_LINE_MS - 1);
  expect(shown()).toHaveLength(1);
  vi.advanceTimersByTime(1);
  expect(shown()).toHaveLength(2);
  vi.advanceTimersByTime(WAITING_LINE_MS * 100);
  const all = shown();
  expect(all).toHaveLength(102);
  all.slice(1).forEach((text, i) => expect(text).not.toBe(all[i]));
  // a `random` that always says the same still never repeats a line
  expect(new Set(all).size).toBe(2);
});

test("the lines rotate through the whole phrase book, at random", () => {
  controller = new OverlayController(emblem, view, { confirm, random: walking() });
  thinkQuietly();
  vi.advanceTimersByTime(WAITING_LINE_MS * 300);
  const all = shown();
  all.slice(1).forEach((text, i) => expect(text).not.toBe(all[i]));
  expect(new Set(all)).toEqual(new Set(BOOK));
});

test("the default source of randomness never repeats a line either", () => {
  thinkQuietly();
  vi.advanceTimersByTime(WAITING_LINE_MS * 500);
  const all = shown();
  all.slice(1).forEach((text, i) => expect(text).not.toBe(all[i]));
  expect(new Set(all).size).toBe(BOOK.length);
});

test.each(["zh-TW", "ja", "en"] as const)("only the lines the app sent are shown, in the language of the settings: %s", (language) => {
  controller = new OverlayController(emblem, view, { confirm, random: walking() });
  send(settings("on", language));
  thinkQuietly(BOOKS[language]);
  vi.advanceTimersByTime(WAITING_LINE_MS * 100);
  expect(new Set(shown())).toEqual(new Set(BOOKS[language]));
  expect(new Set(view.waitingLanguages)).toEqual(new Set([language]));
});

test("a line is shown whatever the subtitles say, as the percentage is", () => {
  send(settings("off"));
  thinkQuietly();
  vi.advanceTimersByTime(WAITING_LINE_MS * 3);
  expect(shown()).toHaveLength(4);
  expect(view.waiting).not.toBeNull();
});

test("a change of language is followed by the lines that go on", () => {
  thinkQuietly();
  vi.advanceTimersByTime(WAITING_LINE_MS);
  send(settings("on", "ja"));
  vi.advanceTimersByTime(WAITING_LINE_MS);
  expect(view.waitingLanguages).toEqual(["zh-TW", "zh-TW", "ja"]);
});

test("a second quiet thought while the lines go on neither restarts nor doubles them, nor changes the book", () => {
  thinkQuietly();
  vi.advanceTimersByTime(700);
  send({ type: "mode", mode: "thinking", quiet: true, lines: BOOKS.en });
  expect(shown()).toHaveLength(1);
  vi.advanceTimersByTime(WAITING_LINE_MS - 700);
  expect(shown()).toHaveLength(2);
  vi.advanceTimersByTime(WAITING_LINE_MS * 10);
  expect(shown()).toHaveLength(12);
  for (const text of shown()) expect(BOOK).toContain(text);
});

test.each([
  ["an app that sends no lines", undefined],
  ["an empty book", []],
  ["a book that is no list", "《告。解析中……》"],
  ["a list of nothing that is text", ["", 5, null, {}]],
])("%s leaves the thinking quiet", (_, lines) => {
  thinkQuietly(lines);
  vi.advanceTimersByTime(60_000);
  expect(view.waitings).toEqual([]);
  expect(view.percentages.filter((percent) => percent !== null)).toEqual([]);
  expect(view.last).toBe("clear");
});

test("what in the book is not a line is left out", () => {
  thinkQuietly(["", 5, "《告。解析中……》", null]);
  vi.advanceTimersByTime(WAITING_LINE_MS * 5);
  // a book of one line has no other line to change to
  expect(shown()).toEqual(Array(6).fill("《告。解析中……》"));
});

test("quiet thinking that no show began has no line", () => {
  send({ type: "mode", mode: "thinking", quiet: true, lines: BOOK });
  vi.advanceTimersByTime(60_000);
  expect(view.waitings).toEqual([]);
});

test("a dictation shows no waiting line, only its percentage", () => {
  send({ type: "show", mode: "listening" });
  vi.advanceTimersByTime(1000);
  send({ type: "mode", mode: "thinking" });
  send({ type: "progress", stage: "cleaning", done: 0, total: 0, audio_seconds: 5 });
  vi.advanceTimersByTime(60_000);
  expect(view.waitings).toEqual([]);
  expect(view.percent).not.toBeNull();
  send({ type: "progress", stage: "done", done: 0, total: 0, audio_seconds: 0 });
  send({ type: "complete" });
  vi.advanceTimersByTime(60_000);
  expect(view.waitings).toEqual([]);
});

test("a dictation whose thinking brings lines still shows only its percentage", () => {
  send({ type: "show", mode: "listening" });
  send({ type: "mode", mode: "thinking", lines: BOOK });
  vi.advanceTimersByTime(60_000);
  expect(view.waitings).toEqual([]);
  expect(view.percent).not.toBeNull();
});

test("a dictation after a question starts without the lines of the question", () => {
  thinkQuietly();
  vi.advanceTimersByTime(WAITING_LINE_MS * 2);
  send({ type: "hide" });
  const before = view.waitings.length;
  send({ type: "show", mode: "listening" });
  send({ type: "mode", mode: "thinking" });
  vi.advanceTimersByTime(60_000);
  expect(view.waitings).toHaveLength(before);
  expect(view.waiting).toBeNull();
  expect(view.percent).not.toBeNull();
});

const ENDS_THE_WAITING: Array<[string, OverlayEvent]> = [
  ["an answer", { type: "say", prefix: "答", text: "好的" }],
  ["a question to the person", ask()],
  ["a notice", { type: "notice", text: ZH }],
  ["a hide (Esc)", { type: "hide" }],
  ["a new recording", { type: "show", mode: "listening" }],
  ["a new question", { type: "show", mode: "listening", line: GREETING }],
  ["the emblem listening again", { type: "mode", mode: "listening" }],
  ["a thought that is not quiet", { type: "mode", mode: "thinking" }],
  ["a percentage", { type: "progress", stage: "cleaning", done: 0, total: 0, audio_seconds: 5 }],
  ["a completion", { type: "complete" }],
];

test.each(ENDS_THE_WAITING)("%s ends the waiting lines for good", (_, event) => {
  thinkQuietly();
  vi.advanceTimersByTime(WAITING_LINE_MS * 3);
  expect(view.waiting).not.toBeNull();
  send(event);
  expect(view.waiting).toBeNull();
  const changes = view.waitings.length;
  vi.advanceTimersByTime(60_000);
  expect(view.waitings).toHaveLength(changes);
});

test("learned words wait for the question and do not stop the lines", () => {
  thinkQuietly();
  vi.advanceTimersByTime(WAITING_LINE_MS);
  send({ type: "learned", text: "learned" });
  vi.advanceTimersByTime(WAITING_LINE_MS * 5);
  expect(shown()).toHaveLength(7);
  expect(view.waiting).not.toBeNull();
  expect(lines()).toEqual([`line:${GREETING}`]);
  send({ type: "say", prefix: "答", text: "好的" });
  expect(view.waiting).toBeNull();
  vi.advanceTimersByTime(60_000);
  expect(lines()).toEqual([`line:${GREETING}`, "line:《答。好的》", "line:learned"]);
});

test("levels and settings leave the lines going", () => {
  thinkQuietly();
  send({ type: "level", value: 0.5 });
  send(settings("on", "zh-TW", 194));
  vi.advanceTimersByTime(WAITING_LINE_MS * 2);
  expect(shown()).toHaveLength(3);
});

// ---- a question with a third answer ----

test("a question with the third answer shows three buttons, and is never held", () => {
  send(ask({ tier: "T3", yes: "允許一次", no: "不要", always: "一律允許" }));
  vi.advanceTimersByTime(833);
  expect(view.asked).toEqual([{ yes: "允許一次", no: "不要", always: "一律允許", tier: "T2", language: "zh-TW" }]);
  expect(controller.holdStart()).toBe(false);
});

test("a question without the third answer has no always label", () => {
  send(ask());
  vi.advanceTimersByTime(833);
  expect(view.asked[0]).not.toHaveProperty("always");
});

test("the third answer says YES with always, once", () => {
  asked({ always: "一律允許" });
  controller.clickAsk("always");
  expect(confirm).toHaveBeenCalledExactlyOnceWith("c1", true, true);
  expect(view.log).toEqual(["choice:always"]);
  controller.clickAsk("yes");
  controller.clickAsk("no");
  expect(confirm).toHaveBeenCalledTimes(1);
});

test("YES and NO of a question with the third answer are sent without always", () => {
  asked({ always: "一律允許" });
  controller.clickAsk("yes");
  expect(confirm).toHaveBeenCalledExactlyOnceWith("c1", true);
  send(ask({ id: "c2", always: "一律允許" }));
  vi.advanceTimersByTime(833);
  controller.clickAsk("no");
  expect(confirm).toHaveBeenLastCalledWith("c2", false);
});

test("a click on always is ignored when the question has no such answer", () => {
  asked();
  controller.clickAsk("always");
  expect(confirm).not.toHaveBeenCalled();
  expect(view.log).toEqual([]);
});

test("a third answer that could not be sent can be given again", async () => {
  confirm.mockRejectedValueOnce(new Error("no"));
  asked({ always: "一律允許" });
  controller.clickAsk("always");
  await vi.advanceTimersByTimeAsync(0);
  expect(view.log).toEqual(["choice:always", "choice:null"]);
  controller.clickAsk("always");
  expect(confirm).toHaveBeenCalledTimes(2);
});

// ---- Raphael operates an app ----

const OPERATE = "《告。操作中：點擊「七」》";

test("operate shows the emblem in the operating mode with the line in the caption's place", () => {
  send({ type: "operate", line: OPERATE });
  expect(emblem.log).toEqual(["appear", "set:operating"]);
  expect(view.waiting).toBe(OPERATE);
  expect(view.waitingLanguages).toEqual(["zh-TW"]);
  expect(controller.expectsPaint).toBe(true);
  // static: the line does not rotate, and nothing else is written
  vi.advanceTimersByTime(WAITING_LINE_MS * 5);
  expect(view.waitings).toEqual([OPERATE]);
  expect(view.overlaps).toEqual([]);
});

test("operate is written whatever the subtitles setting says", () => {
  send(settings("off", "ja", 150));
  send({ type: "operate", line: OPERATE });
  expect(view.waiting).toBe(OPERATE);
  expect(view.waitingLanguages).toEqual(["ja"]);
});

test("another operate replaces the line; an empty one is ignored", () => {
  send({ type: "operate", line: OPERATE });
  send({ type: "operate", line: "《告。操作中：輸入文字》" });
  expect(view.waiting).toBe("《告。操作中：輸入文字》");
  send({ type: "operate", line: "" });
  expect(view.waiting).toBe("《告。操作中：輸入文字》");
});

test("operate during a question takes it from the screen and sends no answer", () => {
  asked({ always: "一律允許" });
  send({ type: "operate", line: OPERATE });
  expect(view.log).toContain("ask-hide");
  expect(confirm).not.toHaveBeenCalled();
  controller.clickAsk("always");
  controller.clickAsk("yes");
  expect(confirm).not.toHaveBeenCalled();
  expect(view.waiting).toBe(OPERATE);
  expect(emblem.log).toContain("set:operating");
});

test("a following say replaces the operate line", () => {
  send({ type: "operate", line: OPERATE });
  send({ type: "say", prefix: "答", text: "好了" });
  expect(view.waiting).toBeNull();
  vi.advanceTimersByTime(833);
  expect(view.log).toContain("line:《答。好了》");
});

test("hide, show and a question each take the operate line away", () => {
  send({ type: "operate", line: OPERATE });
  send({ type: "hide" });
  expect(view.waiting).toBeNull();

  send({ type: "operate", line: OPERATE });
  send({ type: "show", mode: "listening" });
  expect(view.waiting).toBeNull();
  send({ type: "hide" });

  send({ type: "operate", line: OPERATE });
  send(ask());
  expect(view.waiting).toBeNull();
});

test("operate drops the notices that waited but keeps the learned words", () => {
  send({ type: "learned", text: "learned" });
  vi.advanceTimersByTime(833);
  send({ type: "notice", text: "a notice" });
  send({ type: "operate", line: OPERATE });
  vi.advanceTimersByTime(60_000);
  expect(view.log).not.toContain("line:a notice");
});
