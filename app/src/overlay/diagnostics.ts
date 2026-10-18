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

/**
 * Wraps the page's event handler: every event but the loudness is logged at debug level, and an
 * event that throws is reported instead of being lost in the webview's console.
 */
export function guardHandler(
  handle: (event: OverlayEvent) => void,
  log: PageLog,
): (event: OverlayEvent) => void {
  return (event) => {
    if (event.type !== "level") log("debug", `event ${JSON.stringify(event)}`);
    try {
      handle(event);
    } catch (err) {
      log("error", `the "${event.type}" event failed: ${describeError(err)}`);
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

/** How long the emblem may go without an animation frame while it should be on screen. */
const FRAME_WAIT_MS = 1500;

/**
 * Notices when the webview gives the page no animation frames while the emblem should be
 * moving (a webview that thinks its window cannot be seen stops them, and the emblem stays
 * blank). Reports the first stall and the recovery, not every one in between.
 */
export class FrameWatch {
  private stalled = false;
  private timer: ReturnType<typeof setTimeout> | undefined;

  constructor(
    /** How many frames the emblem has stepped so far. */
    private readonly frames: () => number,
    /** `document.visibilityState`. */
    private readonly visibility: () => string,
    private readonly log: PageLog,
  ) {}

  /** The emblem should be animating now, because of `why` (an event type). */
  expectFrames(why: string): void {
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
}
