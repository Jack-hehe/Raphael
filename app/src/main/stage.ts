// The stage on the home page: a small copy of the emblem on the desktop and Raphael's report as its
// subtitles. Opening the page plays the appear animation, and the report follows.

import { Emblem } from "../overlay/emblem";
import { esc, line } from "./ui";
import type { Report } from "./report";

/** The emblem's diameter on the stage, in px. */
const EMBLEM_PX = 128;
/** The report comes this long after the emblem started to appear (the animation takes 0.833 s). */
const REPORT_DELAY_MS = 900;
/** The white ring moves for this long while the report is read. */
const SPEAK_S = 2.8;

const prefersReducedMotion = (): boolean =>
  typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;

export class Stage {
  private readonly emblem: Emblem;
  private timer: ReturnType<typeof setTimeout> | undefined;
  /** When `play` was called, while the appear animation has not yet been followed by the report. */
  private playedAt = 0;
  private waiting = false;
  private report: Report | null = null;
  /** The report on screen, as text, so that an unchanged one is not drawn again (it would fade in again). */
  private shown = "";

  /**
   * `canvas` is where the emblem is drawn, `subtitles` where the report goes. `onAction` hears the
   * button next to the report.
   */
  constructor(
    canvas: HTMLCanvasElement,
    private readonly subtitles: HTMLElement,
    private readonly onAction: (target: "settings" | "retry") => void,
  ) {
    this.emblem = new Emblem(canvas, EMBLEM_PX, { base: 0.05 });
    subtitles.addEventListener("click", (e) => {
      const button = (e.target as Element).closest<HTMLElement>("[data-target]");
      if (button) this.onAction(button.dataset.target as "settings" | "retry");
    });
  }

  /** The emblem appears again, and the report is not there until it has appeared. */
  play(): void {
    clearTimeout(this.timer);
    this.playedAt = performance.now();
    this.waiting = true;
    this.subtitles.innerHTML = "";
    this.shown = "";
    // the report of the last time is old; the next `show` brings the new one
    this.report = null;
    this.emblem.set("steady");
    this.emblem.appear();
  }

  /**
   * Shows the report. If the emblem is still appearing, the report waits for it; otherwise it
   * replaces the one on screen at once.
   */
  show(report: Report): void {
    this.report = report;
    if (this.waiting) this.schedule();
    else if (JSON.stringify(report) !== this.shown) this.render();
  }

  private schedule(): void {
    clearTimeout(this.timer);
    const wait = prefersReducedMotion() ? 0 : Math.max(0, REPORT_DELAY_MS - (performance.now() - this.playedAt));
    this.timer = setTimeout(() => {
      this.waiting = false;
      this.render();
      if (!prefersReducedMotion()) this.emblem.speak(SPEAK_S);
    }, wait);
  }

  private render(): void {
    const report = this.report;
    if (!report) return;
    this.shown = JSON.stringify(report);
    this.subtitles.innerHTML =
      line(report.text) +
      (report.action
        ? `<div class="act"><button type="button" class="btn pri" data-target="${report.action.target}">${esc(report.action.label)}</button></div>`
        : "");
    // so that the fade-in starts from the cleared state
    void this.subtitles.offsetWidth;
    this.subtitles.querySelector(".line")?.classList.add("on");
  }

  dispose(): void {
    clearTimeout(this.timer);
    this.emblem.dispose();
  }
}
