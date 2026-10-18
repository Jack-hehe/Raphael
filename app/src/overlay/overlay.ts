// The overlay page: the emblem in the bottom-right corner and Raphael's subtitles.
//
// Rust (overlay.rs) sends events on a channel that this page opens once. The window itself is
// transparent, click-through and never hidden: "hidden" here means the page draws nothing and
// the animation loop (see emblem.ts) is stopped.
//
// The controller below holds all the logic and takes the emblem and the DOM through small
// interfaces, so the tests run it in Node.

import { Channel, invoke } from "@tauri-apps/api/core";
import { Emblem, canvasSize, loadGlyphTexture, type Mode, type Phase } from "./emblem";
import { noticeDurationMs } from "./notice";
import type { ProgressReport, Stage } from "./progress";
import { FrameWatch, describeError, guardHandler, subscribeWithRetry, type LogLevel } from "./diagnostics";

export { noticeDurationMs };

/** Which subtitle lines are shown: both, only Chinese, only Japanese, or none. */
type SubtitleMode = "both" | "zh" | "ja" | "off";

/** The events overlay.rs sends (`OverlayEvent`). */
export type OverlayEvent =
  | { type: "show"; mode: Mode }
  | { type: "mode"; mode: Mode }
  | { type: "level"; value: number }
  | { type: "progress"; stage: Stage; done: number; total: number; audio_seconds: number }
  | { type: "notice"; zh: string; ja: string }
  | { type: "hide" }
  | { type: "settings"; px: number; subtitles: string };

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
  /** How far the dictation has come since the key was released (the glyph ring draws itself in). */
  progress(report: ProgressReport): void;
  /** The dictation is over: the whole glyph ring comes back. */
  clearProgress(): void;
}

/** What the controller needs from the page. */
export interface OverlayView {
  /** The emblem is `px` wide now: move the subtitles to match. */
  layout(px: number): void;
  /** Shows the lines; null leaves that line out. */
  showLines(zh: string | null, ja: string | null): void;
  clearLines(): void;
}

interface Notice {
  zh: string;
  ja: string;
}

function parseSubtitles(value: string): SubtitleMode {
  return value === "zh" || value === "ja" || value === "off" ? value : "both";
}

export class OverlayController {
  private subtitles: SubtitleMode = "both";
  /** Notices that wait for the one on screen. */
  private readonly queue: Notice[] = [];
  /** A notice is on screen, or about to be. */
  private busy = false;
  /** A show owns the emblem until a hide or notice takes over, regardless of renderer state. */
  private activeShow = false;
  private timer: ReturnType<typeof setTimeout> | undefined;

  constructor(
    private readonly emblem: EmblemControl,
    private readonly view: OverlayView,
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
        this.emblem.clearProgress();
        this.appearIfHidden();
        this.emblem.set(event.mode);
        break;
      case "mode":
        this.emblem.set(event.mode);
        break;
      case "level":
        this.emblem.level = event.value;
        break;
      case "progress":
        // No words: the glyph ring says it (dictation shows no subtitles).
        this.emblem.progress({
          stage: event.stage,
          done: event.done,
          total: event.total,
          audioSeconds: event.audio_seconds,
        });
        break;
      case "notice":
        // Raphael speaks: the dictation's progress has said what it had to.
        this.activeShow = false;
        this.emblem.clearProgress();
        this.queue.push({ zh: event.zh, ja: event.ja });
        if (!this.busy) this.next();
        break;
      case "hide":
        this.activeShow = false;
        this.cancelNotices();
        this.emblem.vanish();
        break;
      case "settings":
        this.subtitles = parseSubtitles(event.subtitles);
        if (Number.isFinite(event.px) && event.px > 0) {
          this.emblem.size(event.px);
          this.view.layout(event.px);
        }
        break;
    }
  }

  private appearIfHidden(): void {
    if (this.emblem.phase === "hidden" || this.emblem.phase === "vanish") this.emblem.appear();
  }

  /** Shows the next notice, or lets the emblem vanish when none is left. */
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
    // The words come when the appear animation has played (at once if the emblem was already there).
    const lead = this.emblem.appearLeft * 1000;
    const duration = noticeDurationMs(notice.zh);
    this.timer = setTimeout(() => {
      this.showLines(notice);
      this.emblem.speak(duration / 1000);
      this.timer = setTimeout(() => {
        this.view.clearLines();
        this.next();
      }, duration);
    }, lead);
  }

  private showLines({ zh, ja }: Notice): void {
    if (this.subtitles === "off") return;
    // Japanese only falls back to the Chinese line when there is no Japanese.
    const wantZh = (this.subtitles !== "ja" || ja === "") && zh !== "";
    const wantJa = this.subtitles !== "zh" && ja !== "";
    this.view.showLines(wantZh ? zh : null, wantJa ? ja : null);
  }

  private cancelNotices(): void {
    clearTimeout(this.timer);
    this.timer = undefined;
    this.queue.length = 0;
    this.busy = false;
    this.view.clearLines();
  }
}

/** The page's elements. */
class DomView implements OverlayView {
  constructor(
    /** Carries the emblem size for the style sheet (`--e`, `--c`). */
    private readonly root: HTMLElement,
    private readonly box: HTMLElement,
    private readonly zh: HTMLElement,
    private readonly ja: HTMLElement,
  ) {}

  layout(px: number): void {
    this.root.style.setProperty("--e", `${px}px`);
    this.root.style.setProperty("--c", `${canvasSize(px)}px`);
  }

  showLines(zh: string | null, ja: string | null): void {
    this.zh.textContent = zh ?? "";
    this.zh.hidden = zh === null;
    this.ja.textContent = ja ?? "";
    this.ja.hidden = ja === null;
    void this.box.offsetWidth; // so that the fade-in starts from the cleared state
    this.box.classList.add("on");
  }

  clearLines(): void {
    this.box.classList.remove("on");
    this.zh.textContent = "";
    this.ja.textContent = "";
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

async function start(): Promise<void> {
  reportUncaught();
  pageLog("info", `loaded (document ${document.visibilityState}, ${window.innerWidth}x${window.innerHeight} at ${window.devicePixelRatio})`);

  const canvas = document.getElementById("emblem");
  const box = document.getElementById("subtitles");
  const zh = box?.querySelector<HTMLElement>(".sub-zh");
  const ja = box?.querySelector<HTMLElement>(".sub-ja");
  if (!(canvas instanceof HTMLCanvasElement) || !box || !zh || !ja) {
    throw new Error("overlay.html is missing the emblem canvas or the subtitles");
  }

  const view = new DomView(document.documentElement, box, zh, ja);
  view.layout(DEFAULT_PX);
  const emblem = new Emblem(canvas, DEFAULT_PX);
  const controller = new OverlayController(emblem, view);
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
    if (event.type === "show" || event.type === "notice") frames.expectFrames(event.type);
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
