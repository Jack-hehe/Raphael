// What the overlay page reports to the app's log (through the `overlay_log` command), so that an
// emblem that does not show can be tracked down from raphael.log: an event the page could not
// handle, a subscription that failed, an animation loop that the webview stopped, an uncaught
// error. The page itself has no console anyone can see.
//
// Kept apart from `overlay.ts` and free of browser objects so that the tests run it in Node.

import type { OverlayEvent } from "./overlay";

/** How a page message is logged (overlay.rs maps it to the log level). */
export type LogLevel = "error" | "warn" | "info" | "debug";
export type PageLog = (level: LogLevel, message: string) => void;

/** An error as one line of text, with the top of its stack when there is one. */
export function describeError(err: unknown): string {
  if (err instanceof Error) {
    const head = `${err.name}: ${err.message}`;
    const stack = (err.stack ?? "")
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => line.startsWith("at "))
      .slice(0, 4)
      .join(" | ");
    return stack ? `${head} (${stack})` : head;
  }
  if (typeof err === "string") return err;
  if (err === undefined) return "undefined";
  try {
    return JSON.stringify(err);
  } catch {
    return String(err);
  }
}

/** Keep text and future payload fields out of the ordinary application log. */
function eventMetadata(event: OverlayEvent): object {
  switch (event.type) {
    case "show":
      // Whether Raphael greets (a question, not a dictation), never what he says.
      return { type: event.type, mode: event.mode, ...(event.line ? { greeting: true } : {}) };
    case "mode":
      return { type: event.type, mode: event.mode, ...(event.quiet === true ? { quiet: true } : {}) };
    case "progress":
      return { type: event.type, stage: event.stage, done: event.done, total: event.total, audio_seconds: event.audio_seconds };
    case "settings":
      return { type: event.type, px: event.px };
    case "ask":
      return { type: event.type, tier: event.tier };
    default:
      return { type: event.type };
  }
}

/**
 * Wraps the page's event handler: every event but the loudness logs safe metadata at debug level,
 * and an event that throws is reported instead of being lost in the webview's console.
 */
export function guardHandler(
  handle: (event: OverlayEvent) => void,
  log: PageLog,
): (event: OverlayEvent) => void {
  return (event) => {
    if (event.type !== "level") log("debug", `event ${JSON.stringify(eventMetadata(event))}`);
    try {
      handle(event);
    } catch (err) {
      const details = event.type === "notice" || event.type === "learned" || event.type === "say" || event.type === "ask"
        ? "details omitted for text-bearing events"
        : describeError(err);
      log("error", `the "${event.type}" event failed: ${details}`);
    }
  };
}

/** The waits between subscription attempts, in milliseconds (about half a minute in all). */
export const SUBSCRIBE_RETRY_MS: readonly number[] = [250, 500, 1000, 2000, 2000, 3000, 5000, 5000, 5000, 5000];

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/**
 * Opens the page's event channel with `open` (the `overlay_subscribe` command). A failure is
 * reported and tried again: without the subscription the page never hears an event and the
 * emblem never shows. Subscribing again is harmless, because overlay.rs keeps one channel per
 * page. Resolves to whether it worked.
 */
export async function subscribeWithRetry(
  open: () => Promise<unknown>,
  log: PageLog,
  wait: (ms: number) => Promise<void> = sleep,
): Promise<boolean> {
  for (let attempt = 1; ; attempt++) {
    try {
      await open();
      log(
        "info",
        attempt === 1 ? "subscribed to the overlay events" : `subscribed to the overlay events after ${attempt} attempts`,
      );
      return true;
    } catch (err) {
      const delay = SUBSCRIBE_RETRY_MS[attempt - 1];
      if (delay === undefined) {
        log("error", `gave up subscribing to the overlay events after ${attempt} attempts: ${describeError(err)}`);
        return false;
      }
      log("warn", `could not subscribe to the overlay events (attempt ${attempt}): ${describeError(err)}`);
      await wait(delay);
    }
  }
}

/** A frame of the emblem's entrance that takes longer than this is a visible hitch: two frames at
 * 60 Hz, and a little more. */
export const SLOW_FRAME_MS = 40;
/** How long the entrance is timed: the appear animation lasts 0.833 s. */
export const ENTRANCE_WATCH_MS = 1000;
/** A timing that has had no frame for this long is given up, so that the next entrance is timed. */
const ENTRANCE_GIVE_UP_MS = 3000;

/**
 * Times the frames of the emblem's entrance, because it sometimes stutters and nothing said why:
 * how long the first frame took to come after the emblem was asked to appear, and every later
 * frame that took longer than SLOW_FRAME_MS. Writes one line when something was slow and nothing
 * otherwise; never anything about what is said. A webview that gives no frames at all is
 * FrameWatch's to report.
 */
export class EntranceTimer {
  private startedAt: number | undefined;

  constructor(
    private readonly log: PageLog,
    /** `document.visibilityState`. */
    private readonly visibility: () => string,
    private readonly now: () => number = () => performance.now(),
    private readonly nextFrame: (callback: (ms: number) => void) => void = (callback) => {
      requestAnimationFrame(callback);
    },
  ) {}

  /** The emblem has just been asked to appear. */
  start(): void {
    const askedAt = this.now();
    // One entrance at a time; a timing whose frames never came does not block the next one.
    if (this.startedAt !== undefined && askedAt - this.startedAt < ENTRANCE_GIVE_UP_MS) return;
    this.startedAt = askedAt;
    let previous: number | undefined;
    let firstAfter = 0;
    const slow: number[] = [];
    const tick = (ms: number): void => {
      if (previous === undefined) firstAfter = Math.max(0, ms - askedAt);
      else if (ms - previous > SLOW_FRAME_MS) slow.push(Math.round(ms - previous));
      previous = ms;
      if (ms - askedAt < ENTRANCE_WATCH_MS) {
        this.nextFrame(tick);
        return;
      }
      this.startedAt = undefined;
      if (firstAfter > SLOW_FRAME_MS || slow.length > 0) {
        this.log(
          "info",
          `emblem entrance: first frame after ${Math.round(firstAfter)} ms, ${slow.length} slow frame(s)` +
            (slow.length > 0 ? ` [${slow.join(", ")}] ms` : "") +
            ` (document ${this.visibility()})`,
        );
      }
    };
    this.nextFrame(tick);
  }
}

/** How long the emblem may go without an animation frame while it should be on screen. */
const FRAME_WAIT_MS = 1500;
/** Pixel readback is deliberately much slower than the animation loop. */
const PAINT_CHECK_MS = 500;

interface PaintSource {
  canvas: HTMLCanvasElement;
  /** False for an intentional hide or vanish, including when a notice expires. */
  expected: () => boolean;
}

/**
 * Notices when the webview gives the page no animation frames while the emblem should be
 * moving (a webview that thinks its window cannot be seen stops them, and the emblem stays
 * blank). Reports the first stall and the recovery, not every one in between.
 */
export class FrameWatch {
  private stalled = false;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private paintTimer: ReturnType<typeof setTimeout> | undefined;
  private blankSince: number | undefined;
  private blankWarned = false;

  constructor(
    /** How many frames the emblem has stepped so far. */
    private readonly frames: () => number,
    /** `document.visibilityState`. */
    private readonly visibility: () => string,
    private readonly log: PageLog,
    private readonly paint?: PaintSource,
  ) {}

  /** The emblem should be animating now, because of `why` (an event type). */
  expectFrames(why: string): void {
    if (this.paint && this.paintTimer === undefined) {
      this.blankSince = performance.now();
      this.paintTimer = setTimeout(() => this.checkPaint(), PAINT_CHECK_MS);
    }
    clearTimeout(this.timer);
    const before = this.frames();
    this.timer = setTimeout(() => {
      const ran = this.frames() > before;
      if (!ran && !this.stalled) {
        this.stalled = true;
        this.log(
          "warn",
          `no animation frame in ${FRAME_WAIT_MS} ms after "${why}" (document ${this.visibility()}): ` +
            "the webview is not drawing the emblem",
        );
      } else if (ran && this.stalled) {
        this.stalled = false;
        this.log("info", `animation frames run again (document ${this.visibility()})`);
      }
    }, FRAME_WAIT_MS);
  }

  /** Frames can run and still clear the canvas to transparency; inspect actual alpha too. */
  private checkPaint(): void {
    this.paintTimer = undefined;
    if (!this.paint || !this.paint.expected()) {
      this.blankSince = undefined;
      this.blankWarned = false;
      return;
    }
    const { canvas } = this.paint;
    let ink = false;
    try {
      const ctx = canvas.getContext("2d");
      if (ctx && canvas.width > 0 && canvas.height > 0) {
        const { data } = ctx.getImageData(0, 0, canvas.width, canvas.height);
        for (let i = 3; i < data.length; i += 4) {
          if (data[i] !== 0) {
            ink = true;
            break;
          }
        }
      }
    } catch (err) {
      this.log("warn", `could not check emblem pixels: ${describeError(err)}`);
      return; // Do not repeat a failing readback every half second.
    }
    if (ink) {
      if (this.blankWarned) this.log("info", "the emblem has visible pixels again");
      this.blankSince = undefined;
      this.blankWarned = false;
    } else {
      this.blankSince ??= performance.now();
      if (!this.blankWarned && performance.now() - this.blankSince >= FRAME_WAIT_MS) {
        this.blankWarned = true;
        this.log("warn", `the emblem canvas is all-transparent for at least ${FRAME_WAIT_MS} ms while it should be visible (document ${this.visibility()})`);
      }
    }
    this.paintTimer = setTimeout(() => this.checkPaint(), PAINT_CHECK_MS);
  }
}
