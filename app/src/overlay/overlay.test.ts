import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { noticeDurationMs } from "./notice";
import { COMPLETION_MS, formatProgress } from "./progress-caption";
import {
  HOLD_MS,
  OverlayController,
  type AskButtons,
  type EmblemControl,
  type HoldState,
  type OverlayEvent,
  type OverlayView,
  type UiLanguage,
  noticeDurationMs as exportedFromOverlay,
} from "./overlay";
import type { Mode, Phase } from "./emblem";

// ---- the brief's test ----

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

test("overlay.ts exports the same function and can be imported without a browser", () => {
  expect(exportedFromOverlay).toBe(noticeDurationMs);
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
  }
  readonly log: string[] = [];
  readonly languages: UiLanguage[] = [];
  readonly asked: AskButtons[] = [];
  get last(): string | undefined {
    return this.log[this.log.length - 1];
  }
  layout(px: number): void {
    this.log.push(`layout:${px}`);
  }
  showLine(text: string, language: UiLanguage): void {
    this.log.push(`line:${text}`);
    this.languages.push(language);
  }
  clearLine(): void {
    this.log.push("clear");
  }
  showAsk(buttons: AskButtons): void {
    this.asked.push(buttons);
    this.log.push(`ask:${buttons.yes}|${buttons.no}|${buttons.tier}`);
  }
  setHold(state: HoldState): void {
    this.log.push(`hold:${state}`);
  }
  setChoice(choice: "yes" | "no" | null): void {
    this.log.push(`choice:${choice}`);
  }
  hideAsk(): void {
    this.log.push("ask-hide");
  }
}

let emblem: FakeEmblem;
let view: FakeView;
let controller: OverlayController;
let confirm: ReturnType<typeof vi.fn<(id: string, approved: boolean) => Promise<unknown>>>;
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
});

const ZH = "《告。這個視窗不接受貼上。》";
const JA = "《告。このウィンドウには貼り付けできません。》";
const EN = "《Notice. This window does not take a paste.》";
const settings = (subtitles: string, language: string = "zh-TW", px = 150): OverlayEvent => ({ type: "settings", px, subtitles, language });

const lines = (): string[] => view.log.filter((line) => line.startsWith("line:"));

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
