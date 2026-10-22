// The voice mode of the 拉斐爾 page. The emblem of the composer (or the person's own picture) flies
// up to just above the composer, in the middle, and grows to the size it has on the empty page; it
// turns there (the loop picture) while Raphael listens, over the conversation, which stays as it is
// behind it: nothing fades, dims, glows or bobs, and no words are written (the line of the empty page
// steps aside while the stage is `is-voice`). When the recording ends
// it flies back into the composer. On the empty page it flies into the emblem in the middle, which
// is the same picture at the same size.
//
// The movement is the design page's: 520 ms out with cubic-bezier(.2,.8,.2,1), 380 ms back with
// cubic-bezier(.4,0,.2,1). With reduced motion the flyer is simply there, and simply gone.

/** The flyer's diameter when it has landed: the emblem of the empty page. */
export const FLYER_PX = 132;
/** The gap between the flyer and the top of the composer, in px. */
export const FLYER_GAP_PX = 10;
const OUT = { duration: 520, easing: "cubic-bezier(.2,.8,.2,1)" } as const;
const BACK = { duration: 380, easing: "cubic-bezier(.4,0,.2,1)" } as const;

/** A place of the flyer: its centre and its scale (1 is `FLYER_PX`), in the stage's coordinates. */
export interface FlyerSpot {
  x: number;
  y: number;
  s: number;
}

type Box = Pick<DOMRect, "left" | "top" | "width" | "height">;

/**
 * Where the flyer starts and where it lands, from the boxes on screen: it starts over the voice
 * button (`button`) at the button's size, and lands centred over `target` when there is one (the
 * emblem of the empty page), else centred across the stage just above the composer (`composer`).
 */
export function flightPath(stage: Box, button: Box, composer: Box, target?: Box | null): { from: FlyerSpot; to: FlyerSpot } {
  const from = { x: button.left - stage.left + button.width / 2, y: button.top - stage.top + button.height / 2, s: button.width / FLYER_PX };
  const to = target
    ? { x: target.left - stage.left + target.width / 2, y: target.top - stage.top + target.height / 2, s: target.width / FLYER_PX }
    : { x: stage.width / 2, y: composer.top - stage.top - FLYER_PX / 2 - FLYER_GAP_PX, s: 1 };
  return { from, to };
}

/** The transform that puts the flyer (a `FLYER_PX` box at the stage's top left) at `spot`. */
export const spotTransform = (spot: FlyerSpot): string =>
  `translate(${Math.round((spot.x - FLYER_PX / 2) * 10) / 10}px, ${Math.round((spot.y - FLYER_PX / 2) * 10) / 10}px) scale(${Math.round(spot.s * 1000) / 1000})`;

export interface VoiceModeParts {
  /** The positioned box the flyer flies in. */
  stage: HTMLElement;
  /** The composer's voice button, hidden while its picture is up. */
  button: () => HTMLElement | null;
  /** The composer, whose top the flyer lands above. */
  composer: () => HTMLElement | null;
  /** The emblem of the empty page, when it is on screen: the flyer lands on it and it hides meanwhile. */
  target: () => HTMLElement | null;
  /** The flyer's picture: the loop of the emblem, or the person's picture in a circle. */
  face: () => string;
  /** A click on the flyer: it ends the recording, as the key would. */
  onClick: () => void;
}

const reduced = (): boolean => typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;

/** The flyer of one page: `up` sends it out (once), `down` brings it back. */
export class VoiceMode {
  private flyer: HTMLElement | null = null;
  private hidden: HTMLElement[] = [];

  constructor(private readonly parts: VoiceModeParts) {}

  /** Whether the picture is up (or on its way). */
  get isUp(): boolean {
    return this.flyer !== null;
  }

  /** The picture flies out of the composer. Nothing happens while it is up already or there is no composer. */
  up(): void {
    const button = this.parts.button();
    const composer = this.parts.composer();
    if (this.flyer || !button || !composer || button.getClientRects().length === 0) return;
    const target = this.parts.target();
    const { from, to } = flightPath(this.parts.stage.getBoundingClientRect(), button.getBoundingClientRect(), composer.getBoundingClientRect(), target?.getBoundingClientRect());
    const flyer = document.createElement("div");
    flyer.className = "ra-flyer";
    flyer.innerHTML = this.parts.face();
    flyer.style.transform = spotTransform(to);
    flyer.addEventListener("click", () => this.parts.onClick());
    this.parts.stage.append(flyer);
    this.parts.stage.classList.add("is-voice");
    this.flyer = flyer;
    this.hidden = [button, ...(target ? [target] : [])];
    for (const el of this.hidden) el.style.visibility = "hidden";
    if (!reduced()) flyer.animate([{ transform: spotTransform(from) }, { transform: spotTransform(to) }], { ...OUT, fill: "backwards" });
  }

  /** The picture flies back into the composer and is gone. */
  down(): void {
    const flyer = this.flyer;
    if (!flyer) return;
    this.flyer = null;
    const hidden = this.hidden;
    this.hidden = [];
    const done = (): void => {
      flyer.remove();
      for (const el of hidden) el.style.visibility = "";
      if (!this.flyer) this.parts.stage.classList.remove("is-voice");
    };
    const button = this.parts.button();
    const composer = this.parts.composer();
    if (reduced() || !button || !composer || !button.isConnected) {
      done();
      return;
    }
    const { from } = flightPath(this.parts.stage.getBoundingClientRect(), button.getBoundingClientRect(), composer.getBoundingClientRect(), null);
    // Back on the empty page it lands where it was; elsewhere it goes into the button.
    const target = hidden.find((el) => el !== button);
    if (target) {
      done();
      return;
    }
    const anim = flyer.animate([{ transform: flyer.style.transform }, { transform: spotTransform(from) }], { ...BACK, fill: "forwards" });
    anim.finished.then(done, done);
  }

  /** The picture is gone at once (the page is left). */
  drop(): void {
    const flyer = this.flyer;
    this.flyer = null;
    flyer?.remove();
    for (const el of this.hidden) el.style.visibility = "";
    this.hidden = [];
    this.parts.stage.classList.remove("is-voice");
  }

  /**
   * The loudness of the question being recorded, 0 to 1: the thin ring around the person's
   * picture brightens with it (the emblem itself does not move with the voice here).
   */
  level(value: number): void {
    if (this.flyer) this.flyer.style.setProperty("--lvl", String(Math.max(0, Math.min(1, value)).toFixed(2)));
  }

  /** The picture changed while it is up: the flyer shows the new one. */
  redraw(): void {
    if (this.flyer) this.flyer.innerHTML = this.parts.face();
  }
}
