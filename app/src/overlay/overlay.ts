// The overlay page: the emblem in the bottom-right corner and Raphael's subtitles.
//
// Rust (overlay.rs) sends events on a channel that this page opens once. The window itself is
// transparent, click-through and never hidden: "hidden" here means the page draws nothing and
// the animation loop (see emblem.ts) is stopped. A question (an `ask` event) is the one thing
// that takes the pointer: its two buttons can be clicked, and the app lets only them through.
//
// The controller below holds all the logic and takes the emblem and the DOM through small
// interfaces, so the tests run it in Node.

import { Channel, invoke } from "@tauri-apps/api/core";
import { replyLine } from "../shared/anime";
import { Emblem, canvasSize, loadGlyphTexture, type Mode, type Phase } from "./emblem";
import { noticeDurationMs } from "./notice";
import type { ProgressReport, Stage } from "./progress";
import { ProgressArc } from "./progress";
import { COMPLETION_MS, formatProgress, progressPercent } from "./progress-caption";
import { FrameWatch, describeError, guardHandler, subscribeWithRetry, type LogLevel, type PageLog } from "./diagnostics";

export { noticeDurationMs };

/** The language of every subtitle: the interface language of the app. */
export type UiLanguage = "zh-TW" | "en" | "ja";

const isLanguage = (value: unknown): value is UiLanguage => value === "zh-TW" || value === "en" || value === "ja";

/** Whether reports (notices, learned words) are written next to the emblem. Answers and questions always are. */
type SubtitleMode = "on" | "off";

/** T2 asks for a click; T3 (what cannot be undone) for a click that was first held. */
export type AskTier = "T2" | "T3";

/** The events overlay.rs sends (`OverlayEvent`). */
export type OverlayEvent =
  | { type: "show"; mode: Mode }
  | { type: "mode"; mode: Mode }
  | { type: "level"; value: number }
  | { type: "progress"; stage: Stage; done: number; total: number; audio_seconds: number }
  | { type: "notice"; text: string }
  | { type: "learned"; text: string }
  | { type: "say"; prefix: string; text: string }
  | { type: "ask"; id: string; tier: AskTier; prefix: string; text: string; yes: string; no: string; timeout_ms: number }
  | { type: "complete" }
  | { type: "hide" }
  | { type: "settings"; px: number; subtitles: string; language: string };

/** What the controller needs from the emblem. */
export interface EmblemControl {
  readonly phase: Phase;
  /** Seconds of the appear animation still to play. */
  readonly appearLeft: number;
  level: number;
  appear(): void;
  vanish(): void;
  set(mode: Mode): void;
  speak(seconds: number): void;
  size(px: number): void;
}

/** How far a pointer hold on the YES of a T3 question has come. */
export type HoldState = "idle" | "holding" | "armed";

/** What the two buttons of a question say, and how strict the question is. */
export interface AskButtons {
  yes: string;
  no: string;
  tier: AskTier;
  language: UiLanguage;
}

/** What the controller needs from the page. */
export interface OverlayView {
  /** The emblem is `px` wide now: move the subtitles to match. */
  layout(px: number): void;
  /** Shows one line of subtitles, in the language it is written in. */
  showLine(text: string, language: UiLanguage): void;
  clearLine(): void;
  /** Processing progress is separate from spoken subtitles; null hides it. */
  showProgress(percent: number | null): void;
  /** The two buttons come in under the line. */
  showAsk(buttons: AskButtons): void;
  /** The filling underline of the YES button of a T3 question. */
  setHold(state: HoldState): void;
  /** An answer was sent: both buttons stop taking clicks and the chosen one stays pressed; null takes it back. */
  setChoice(choice: "yes" | "no" | null): void;
  hideAsk(): void;
}

/** How long the pointer must stay down on the YES of a T3 question before it is armed. */
export const HOLD_MS = 600;
/** An armed button whose release never comes (the window went click-through again) is disarmed after this. */
const ARMED_MAX_MS = 5000;
/** The app answers a question that ran out itself; the page lets go of it this long after. */
const ASK_GRACE_MS = 1500;
/** Used when a question does not say how long it waits. */
const DEFAULT_ASK_TIMEOUT_MS = 30_000;

interface Notice {
  text: string;
  /**
   * Learned words never interrupt a dictation, and a later show, complete or hide keeps them. Raphael's
   * own answers (`say`) are written whatever the subtitles setting says.
   */
  kind: "notice" | "learned" | "say";
}

/** A question that is on screen, or about to be. */
interface Ask {
  id: string;
  tier: AskTier;
  /** 《問。…》, as it is written. */
  line: string;
  yes: string;
  no: string;
  timeoutMs: number;
  /** The words and the buttons are on screen (the appear animation has played). */
  shown: boolean;
  hold: HoldState;
  /** The answer that was sent while the app has not yet replaced the question. */
  chosen: "yes" | "no" | null;
}

export interface ControllerOptions {
  /** Sends the person's answer to the app (the `overlay_confirm` command). */
  confirm?: (id: string, approved: boolean) => Promise<unknown>;
  /** Where a problem is reported (the app's log). It never gets the words on screen. */
  log?: PageLog;
}

export class OverlayController {
  private subtitles: SubtitleMode = "on";
  private language: UiLanguage = "zh-TW";
  /** Lines that wait for the one on screen. */
  private readonly queue: Notice[] = [];
  /** A line is on screen, or about to be (or a question is). */
  private busy = false;
  /** A show owns the emblem until a hide or notice takes over, regardless of renderer state. */
  private activeShow = false;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private ask: Ask | null = null;
  private askTimer: ReturnType<typeof setTimeout> | undefined;
  private holdTimer: ReturnType<typeof setTimeout> | undefined;
  private readonly progress = new ProgressArc();
  private progressTimer: ReturnType<typeof setInterval> | undefined;
  private progressClock = 0;
  private lastProgress: number | null = null;

  constructor(
    private readonly emblem: EmblemControl,
    private readonly view: OverlayView,
    private readonly options: ControllerOptions = {},
  ) {}

  /** Visibility intent for diagnostics, independent of a possibly broken renderer phase. */
  get expectsPaint(): boolean {
    return this.activeShow || this.busy;
  }

  handle(event: OverlayEvent): void {
    switch (event.type) {
      case "show":
        // Dictation (and anything else that shows the emblem) takes over from a notice, and
        // starts without the last dictation's progress.
        this.cancelNotices();
        this.activeShow = true;
        this.appearIfHidden();
        this.setMode(event.mode);
        break;
      case "mode":
        this.setMode(event.mode);
        break;
      case "level":
        this.emblem.level = event.value;
        break;
      case "progress":
        // How far the dictation has come is a percentage beside the whole ring.
        this.updateProgress({
          stage: event.stage,
          done: event.done,
          total: event.total,
          audioSeconds: event.audio_seconds,
        });
        break;
      case "complete":
        if (!this.activeShow) break;
        this.activeShow = false;
        this.cancelNotices();
        this.busy = true;
        this.appearIfHidden();
        this.emblem.set("steady");
        this.view.showProgress(100);
        this.lastProgress = 100;
        this.timer = setTimeout(() => {
          this.view.showProgress(null);
          this.lastProgress = null;
          this.next();
        }, COMPLETION_MS);
        break;
      case "learned":
        // A dictation or a question on screen owns the emblem: the words wait for its end.
        this.queue.push({ text: event.text, kind: "learned" });
        if (!this.activeShow && !this.busy) this.next();
        break;
      case "notice":
        this.speak({ text: event.text, kind: "notice" });
        break;
      case "say":
        // Raphael's answer comes before the learned words that wait: it is what was asked for.
        this.speak({ text: replyLine(event.prefix, event.text), kind: "say" }, true);
        break;
      case "ask":
        this.startAsk(event);
        break;
      case "hide":
        this.activeShow = false;
        this.cancelNotices();
        // Learned words that waited for the dictation take the emblem as it is, instead of a vanish.
        if (this.queue.length > 0) this.next();
        else this.emblem.vanish();
        break;
      case "settings":
        this.subtitles = event.subtitles === "off" ? "off" : "on";
        if (isLanguage(event.language)) this.language = event.language;
        if (Number.isFinite(event.px) && event.px > 0) {
          this.emblem.size(event.px);
          this.view.layout(event.px);
        }
        break;
    }
  }

  // ---------- the buttons of a question ----------

  /** The question on screen, if it takes an answer now. */
  private open(): Ask | null {
    const ask = this.ask;
    return ask && ask.shown && ask.chosen === null ? ask : null;
  }

  /** A click on a button. The YES of a T3 question is answered by `holdEnd`, never by a click. */
  clickAsk(button: "yes" | "no"): void {
    const ask = this.open();
    if (!ask || (button === "yes" && ask.tier === "T3")) return;
    this.choose(ask, button === "yes");
  }

  /** The pointer went down on the YES button: for a T3 question the hold begins (true). */
  holdStart(): boolean {
    const ask = this.open();
    if (!ask || ask.tier !== "T3") return false;
    this.resetHold(ask);
    ask.hold = "holding";
    this.view.setHold("holding");
    this.holdTimer = setTimeout(() => {
      ask.hold = "armed";
      this.view.setHold("armed");
      // A release that never comes must not leave the button armed for good.
      this.holdTimer = setTimeout(() => this.resetHold(ask), ARMED_MAX_MS);
    }, HOLD_MS);
    return true;
  }

  /** The pointer came up. On the button, after a full hold, the answer is YES; otherwise the hold is dropped. */
  holdEnd(onButton: boolean): void {
    const ask = this.open();
    if (!ask || ask.tier !== "T3") return;
    if (ask.hold === "armed" && onButton) {
      clearTimeout(this.holdTimer);
      this.holdTimer = undefined;
      this.choose(ask, true);
    } else {
      this.resetHold(ask);
    }
  }

  /** The pointer was taken away (the system cancelled it): nothing was confirmed. */
  holdCancel(): void {
    const ask = this.open();
    if (ask) this.resetHold(ask);
  }

  private resetHold(ask: Ask): void {
    clearTimeout(this.holdTimer);
    this.holdTimer = undefined;
    if (ask.hold !== "idle") {
      ask.hold = "idle";
      this.view.setHold("idle");
    }
  }

  /** Sends the answer. The buttons stay (pressed, and dead) until the app replaces the question. */
  private choose(ask: Ask, approved: boolean): void {
    // A hold that was still going (NO was clicked meanwhile) must not arm a button that is dead now.
    clearTimeout(this.holdTimer);
    this.holdTimer = undefined;
    if (!approved && ask.hold !== "idle") {
      ask.hold = "idle";
      this.view.setHold("idle");
    }
    ask.chosen = approved ? "yes" : "no";
    this.view.setChoice(ask.chosen);
    let sent: Promise<unknown>;
    try {
      sent = Promise.resolve(this.options.confirm?.(ask.id, approved));
    } catch (err) {
      sent = Promise.reject(err);
    }
    sent.catch((err) => {
      this.options.log?.("warn", `could not send the answer to a question: ${describeError(err)}`);
      // The question is still there for another try, unless something replaced it meanwhile.
      if (this.ask === ask) {
        ask.chosen = null;
        this.resetHold(ask);
        this.view.setChoice(null);
      }
    });
  }

  private startAsk(event: Extract<OverlayEvent, { type: "ask" }>): void {
    if (typeof event.id !== "string" || event.id === "") {
      this.options.log?.("warn", "a question without an id was ignored");
      return;
    }
    this.activeShow = false;
    // The words, buttons and progress of before go; learned words that wait stay.
    this.cancelNotices();
    const ask: Ask = {
      id: event.id,
      // What is not plainly a T2 question is asked the strict way.
      tier: event.tier === "T2" ? "T2" : "T3",
      line: replyLine(event.prefix, event.text),
      yes: event.yes || "✓",
      no: event.no || "✕",
      timeoutMs: Number.isFinite(event.timeout_ms) && event.timeout_ms > 0 ? event.timeout_ms : DEFAULT_ASK_TIMEOUT_MS,
      shown: false,
      hold: "idle",
      chosen: null,
    };
    this.ask = ask;
    this.busy = true;
    this.appearIfHidden();
    this.emblem.set("steady");
    // The words and the buttons come when the appear animation has played (at once if already visible).
    this.timer = setTimeout(() => {
      ask.shown = true;
      this.view.showLine(ask.line, this.language);
      this.view.showAsk({ yes: ask.yes, no: ask.no, tier: ask.tier, language: this.language });
      this.emblem.speak(noticeDurationMs(ask.line) / 1000);
    }, this.emblem.appearLeft * 1000);
    // The app answers a question that was not answered in time. If it does not, the page lets go.
    this.askTimer = setTimeout(() => this.expire(ask), ask.timeoutMs + ASK_GRACE_MS);
  }

  private expire(ask: Ask): void {
    if (this.ask !== ask) return;
    this.cancelNotices();
    if (this.queue.length > 0) this.next();
    else this.emblem.vanish();
  }

  /** The question goes: its words, its buttons and the timers that belong to it. */
  private dropAsk(): void {
    clearTimeout(this.askTimer);
    this.askTimer = undefined;
    clearTimeout(this.holdTimer);
    this.holdTimer = undefined;
    if (this.ask) {
      this.ask = null;
      this.view.hideAsk();
    }
  }

  // ---------- the emblem and the words ----------

  private appearIfHidden(): void {
    if (this.emblem.phase === "hidden" || this.emblem.phase === "vanish") this.emblem.appear();
  }

  private setMode(mode: Mode): void {
    this.emblem.set(mode);
    if (this.activeShow && mode === "thinking" && !this.progress.active) {
      this.progress.report({ stage: "transcribing", done: 0, total: 1, audioSeconds: 0 }, false);
      this.renderProgress();
      this.startProgressTimer();
    }
  }

  private updateProgress(report: ProgressReport): void {
    if (!this.activeShow) return;
    this.progress.report(report, false);
    this.renderProgress();
    this.startProgressTimer();
  }

  private startProgressTimer(): void {
    if (this.progressTimer !== undefined) return;
    this.progressClock = Date.now();
    this.progressTimer = setInterval(() => {
      const now = Date.now();
      this.progress.advance(Math.max(0, now - this.progressClock) / 1000, false);
      this.progressClock = now;
      this.renderProgress();
    }, 100);
  }

  private renderProgress(): void {
    const stage = this.progress.currentStage;
    if (!stage || !this.activeShow) return;
    const percent = progressPercent(stage, this.progress.fraction);
    if (percent === this.lastProgress) return;
    this.lastProgress = percent;
    this.view.showProgress(percent);
  }

  private stopProgress(): void {
    clearInterval(this.progressTimer);
    this.progressTimer = undefined;
    this.progress.reset();
    this.lastProgress = null;
    this.view.showProgress(null);
  }

  /** Raphael speaks: the dictation's progress has said what it had to, and a question is answered. */
  private speak(notice: Notice, first = false): void {
    this.activeShow = false;
    if (this.progress.active) this.stopProgress();
    if (this.ask) this.cancelNotices();
    // Before the learned words that wait, but behind the lines of Raphael's that came earlier.
    const at = first ? this.queue.findIndex((queued) => queued.kind === "learned") : -1;
    if (at >= 0) this.queue.splice(at, 0, notice);
    else this.queue.push(notice);
    if (!this.busy) this.next();
  }

  /** Shows the next line, or lets the emblem vanish when none is left. */
  private next(): void {
    const notice = this.queue.shift();
    if (!notice) {
      this.busy = false;
      this.emblem.vanish();
      return;
    }
    this.busy = true;
    this.appearIfHidden();
    this.emblem.set("steady");
    // The words come when the appear animation has played (at once if already visible).
    const lead = this.emblem.appearLeft * 1000;
    const duration = noticeDurationMs(notice.text);
    this.timer = setTimeout(() => {
      this.showLine(notice);
      this.emblem.speak(duration / 1000);
      this.timer = setTimeout(() => {
        this.view.clearLine();
        this.next();
      }, duration);
    }, lead);
  }

  private showLine({ text, kind }: Notice): void {
    if (kind !== "say" && this.subtitles === "off") return;
    if (text !== "") this.view.showLine(text, this.language);
  }

  /** Stops what is on screen and drops the reports that wait; learned words stay queued. */
  private cancelNotices(): void {
    this.dropAsk();
    this.stopProgress();
    clearTimeout(this.timer);
    this.timer = undefined;
    for (let i = this.queue.length - 1; i >= 0; i--) {
      if (this.queue[i].kind !== "learned") this.queue.splice(i, 1);
    }
    this.busy = false;
    this.view.clearLine();
  }
}

/** The page's elements. */
class DomView implements OverlayView {
  constructor(
    /** Carries the emblem size for the style sheet (`--e`, `--c`). */
    private readonly root: HTMLElement,
    private readonly box: HTMLElement,
    private readonly line: HTMLElement,
    private readonly progress: HTMLElement,
    private readonly question: HTMLElement,
    private readonly yes: HTMLButtonElement,
    private readonly no: HTMLButtonElement,
  ) {}

  layout(px: number): void {
    this.root.style.setProperty("--e", `${px}px`);
    this.root.style.setProperty("--c", `${canvasSize(px)}px`);
  }

  showLine(text: string, language: UiLanguage): void {
    this.line.textContent = text;
    this.line.lang = language;
    void this.box.offsetWidth; // so that the fade-in starts from the cleared state
    this.box.classList.add("on");
  }

  clearLine(): void {
    this.box.classList.remove("on");
    this.line.textContent = "";
  }

  showProgress(percent: number | null): void {
    this.progress.hidden = percent === null;
    this.progress.textContent = percent === null ? "" : formatProgress(percent);
  }

  showAsk({ yes, no, tier, language }: AskButtons): void {
    this.yes.querySelector(".ask-label")!.textContent = yes;
    this.no.textContent = no;
    this.question.lang = language;
    this.question.dataset.tier = tier;
    this.setHold("idle");
    this.setChoice(null);
    this.question.hidden = false;
    this.box.classList.add("asking");
    void this.question.offsetWidth;
    this.question.classList.add("on");
  }

  setHold(state: HoldState): void {
    this.yes.dataset.hold = state;
  }

  setChoice(choice: "yes" | "no" | null): void {
    this.question.dataset.choice = choice ?? "";
    this.yes.disabled = this.no.disabled = choice !== null;
  }

  hideAsk(): void {
    this.question.classList.remove("on");
    this.question.hidden = true;
    this.box.classList.remove("asking");
    this.setHold("idle");
    this.setChoice(null);
  }
}

/** The glyph ring texture. It is private (copied there by scripts/copy-private.mjs). */
const GLYPH_URL = "/private/glyph-ring.png";
/** Until the first Settings event arrives. */
const DEFAULT_PX = 150;
/** The texture is a local file; if it takes longer than this, start without it. */
const TEXTURE_WAIT_MS = 2000;

/**
 * Writes `message` to the app's log (raphael.log, as "overlay page: ..."). Never throws and never
 * reports its own failure, so that it cannot loop.
 */
function pageLog(level: LogLevel, message: string): void {
  invoke("overlay_log", { level, message }).catch(() => {});
}

/** Uncaught errors anywhere in the page go to the app's log too. */
function reportUncaught(): void {
  window.addEventListener("error", (event) => {
    const where = event.filename ? ` at ${event.filename}:${event.lineno}:${event.colno}` : "";
    pageLog("error", `uncaught error${where}: ${describeError(event.error ?? event.message)}`);
  });
  window.addEventListener("unhandledrejection", (event) => {
    pageLog("error", `unhandled rejection: ${describeError(event.reason)}`);
  });
  // A webview that thinks its window cannot be seen stops the animation frames.
  document.addEventListener("visibilitychange", () => {
    pageLog("info", `document ${document.visibilityState}`);
  });
}

/** Whether a pointer event ended on `button` (a captured pointer reports the button even when it left it). */
function onButton(button: HTMLElement, event: PointerEvent): boolean {
  const r = button.getBoundingClientRect();
  return event.clientX >= r.left && event.clientX <= r.right && event.clientY >= r.top && event.clientY <= r.bottom;
}

/** The pointer on the buttons of a question, handed to the controller. */
function wireQuestion(controller: OverlayController, yes: HTMLButtonElement, no: HTMLButtonElement): void {
  yes.style.setProperty("--hold-ms", `${HOLD_MS}ms`);
  yes.addEventListener("click", () => controller.clickAsk("yes"));
  no.addEventListener("click", () => controller.clickAsk("no"));
  yes.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || !controller.holdStart()) return;
    // A hold goes on if the pointer strays; where it ends is decided on release. (A click is not
    // captured: pressing, moving off and letting go is how one changes one's mind.)
    try {
      yes.setPointerCapture(event.pointerId);
    } catch {
      // not capturable: the release still arrives while the pointer is over the button
    }
  });
  yes.addEventListener("pointerup", (event) => controller.holdEnd(onButton(yes, event)));
  yes.addEventListener("pointercancel", () => controller.holdCancel());
}

async function start(): Promise<void> {
  reportUncaught();
  pageLog("info", `loaded (document ${document.visibilityState}, ${window.innerWidth}x${window.innerHeight} at ${window.devicePixelRatio})`);

  const canvas = document.getElementById("emblem");
  const box = document.getElementById("subtitles");
  const line = box?.querySelector<HTMLElement>(".sub-line");
  const progress = document.getElementById("progress");
  const question = document.getElementById("ask");
  const yes = document.getElementById("ask-yes");
  const no = document.getElementById("ask-no");
  if (
    !(canvas instanceof HTMLCanvasElement) || !box || !line || !progress || !question ||
    !(yes instanceof HTMLButtonElement) || !(no instanceof HTMLButtonElement)
  ) {
    throw new Error("overlay.html is missing the emblem canvas, subtitles, progress, or question");
  }

  // The window takes the mouse while a question is up: no menu of the browser (Back, Refresh...) over it.
  document.addEventListener("contextmenu", (event) => event.preventDefault());

  const view = new DomView(document.documentElement, box, line, progress, question, yes, no);
  view.layout(DEFAULT_PX);
  const emblem = new Emblem(canvas, DEFAULT_PX, { surface: "transparent" });
  const controller = new OverlayController(emblem, view, {
    confirm: (id, approved) => invoke("overlay_confirm", { id, approved }),
    log: pageLog,
  });
  wireQuestion(controller, yes, no);
  const frames = new FrameWatch(() => emblem.frames, () => document.visibilityState, pageLog, {
    canvas,
    expected: () => controller.expectsPaint,
  });

  // The texture comes first, so that no event meets a half-ready emblem. Without the file the
  // load fails and the plain vector ring is drawn.
  await Promise.race([
    loadGlyphTexture(GLYPH_URL),
    new Promise((resolve) => setTimeout(resolve, TEXTURE_WAIT_MS)),
  ]);

  // Subscribe once (again if that fails). Events that came before this point wait on the Rust
  // side. An event that fails is reported and the channel goes on.
  const handle = guardHandler((event) => {
    controller.handle(event);
    if (
      event.type === "show" || event.type === "notice" || event.type === "learned" || event.type === "complete" ||
      event.type === "say" || event.type === "ask"
    ) {
      frames.expectFrames(event.type);
    }
  }, pageLog);
  await subscribeWithRetry(() => {
    const channel = new Channel<OverlayEvent>(handle);
    return invoke("overlay_subscribe", { channel });
  }, pageLog);
}

// Only in the overlay page (the tests import this file in Node).
if (typeof document !== "undefined" && document.getElementById("emblem")) {
  start().catch((err) => {
    console.error("overlay: could not start", err);
    pageLog("error", `could not start: ${describeError(err)}`);
  });
}
