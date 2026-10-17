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

export { noticeDurationMs };

/** Which subtitle lines are shown: both, only Chinese, only Japanese, or none. */
type SubtitleMode = "both" | "zh" | "ja" | "off";

/** The events overlay.rs sends (`OverlayEvent`). */
export type OverlayEvent =
  | { type: "show"; mode: Mode }
  | { type: "mode"; mode: Mode }
  | { type: "level"; value: number }
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
  private timer: ReturnType<typeof setTimeout> | undefined;

  constructor(
    private readonly emblem: EmblemControl,
    private readonly view: OverlayView,
  ) {}

  handle(event: OverlayEvent): void {
    switch (event.type) {
      case "show":
        // Dictation (and anything else that shows the emblem) takes over from a notice.
        this.cancelNotices();
        this.appearIfHidden();
        this.emblem.set(event.mode);
        break;
      case "mode":
        this.emblem.set(event.mode);
        break;
      case "level":
        this.emblem.level = event.value;
        break;
      case "notice":
        this.queue.push({ zh: event.zh, ja: event.ja });
        if (!this.busy) this.next();
        break;
      case "hide":
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

async function start(): Promise<void> {
  const canvas = document.getElementById("emblem");
  const box = document.getElementById("subtitles");
  const zh = box?.querySelector<HTMLElement>(".sub-zh");
  const ja = box?.querySelector<HTMLElement>(".sub-ja");
  if (!(canvas instanceof HTMLCanvasElement) || !box || !zh || !ja) {
    throw new Error("overlay.html is missing the emblem canvas or the subtitles");
  }

  const view = new DomView(document.documentElement, box, zh, ja);
  view.layout(DEFAULT_PX);
  const controller = new OverlayController(new Emblem(canvas, DEFAULT_PX), view);

  // The texture comes first, so that no event meets a half-ready emblem. Without the file the
  // load fails and the plain vector ring is drawn.
  await Promise.race([
    loadGlyphTexture(GLYPH_URL),
    new Promise((resolve) => setTimeout(resolve, TEXTURE_WAIT_MS)),
  ]);

  // Subscribe once. Events that came before this point wait on the Rust side.
  const channel = new Channel<OverlayEvent>((event) => controller.handle(event));
  await invoke("overlay_subscribe", { channel });
}

// Only in the overlay page (the tests import this file in Node).
if (typeof document !== "undefined" && document.getElementById("emblem")) {
  start().catch((err) => console.error("overlay: could not start", err));
}
