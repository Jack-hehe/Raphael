// Raphael's face in the main window. By default it is his emblem, the still pictures of the
// bottom-right corner (app/public/brand/app-emblem-{light,dark}-{64,128}.webp, one for each theme,
// and the turning loop app-emblem-{light,dark}-loop.webp while he thinks or listens). The person may
// choose a picture of their own instead (Settings › 拉斐爾 › 頭像): the app keeps a copy in its local
// folder and hands the page its bytes as a data URL (`avatar_get`), never a path. The picture is
// shown in a circle, cut as the person chose in the crop dialog (`AvatarCrop`).
//
// Every place that shows Raphael asks this module for the HTML: the turns of the conversation, the
// sidebar's 拉斐爾, the composer's voice button, the approval card, the voice-mode flyer and the
// settings row. They draw again when `onAvatarChange` says the picture changed.

import { pictureStyle } from "../overlay/emblem";
import { esc } from "./ui";
import "./avatar.css";

/**
 * Which part of the picture shows in the circle. The picture covers the circle (its shorter side
 * fills it) and is then enlarged `zoom` times; the point at `x`, `y` (fractions of the picture's
 * width and height) stays at the same fractions of the circle. The overlay draws the same cut on
 * its canvas (overlay/emblem.ts, `avatarRect`).
 */
export interface AvatarCrop {
  x: number;
  y: number;
  zoom: number;
}

/** A picture of the person's own, as `avatar_get` gives it. */
export interface CustomAvatar {
  /** `data:image/<png|jpeg|webp|gif>;base64,...` */
  url: string;
  crop: AvatarCrop;
}

/** The most the picture may be enlarged in the crop dialog. */
export const ZOOM_MAX = 4;

/** The only addresses a picture may have: a data URL of one of the four kinds the app accepts. */
const DATA_URL = /^data:image\/(png|jpeg|webp|gif);base64,[A-Za-z0-9+/]+=*$/;

const clamp = (value: number, low: number, high: number): number => Math.min(high, Math.max(low, value));

/** A crop with every number in range; anything that is not a number is the middle, unenlarged. */
export function cleanCrop(crop: Partial<AvatarCrop> | null | undefined): AvatarCrop {
  const num = (value: unknown, fallback: number): number => (typeof value === "number" && Number.isFinite(value) ? value : fallback);
  return { x: clamp(num(crop?.x, 0.5), 0, 1), y: clamp(num(crop?.y, 0.5), 0, 1), zoom: clamp(num(crop?.zoom, 1), 1, ZOOM_MAX) };
}

/** What `avatar_get` answered, if it is a picture this page may show; null for the emblem. */
export function readAvatar(value: unknown): CustomAvatar | null {
  if (!value || typeof value !== "object") return null;
  const { url, crop } = value as { url?: unknown; crop?: Partial<AvatarCrop> };
  if (typeof url !== "string" || !DATA_URL.test(url)) return null;
  return { url, crop: cleanCrop(crop) };
}

let custom: CustomAvatar | null = null;
const listeners = new Set<() => void>();
const notify = (): void => {
  for (const listener of listeners) listener();
};

// ---------- reduced motion ----------
//
// Under `prefers-reduced-motion: reduce` nothing of Raphael moves: the turning loop is the still
// of the same size, and a picture of the person's own that may move (a GIF or a WebP, which CSS
// cannot stop) is shown as its first frame, drawn once onto a canvas. The preference is followed
// while the window is open: every place that shows him draws again when it changes.

const motionQuery = typeof matchMedia === "function" ? matchMedia("(prefers-reduced-motion: reduce)") : null;
let reduced = motionQuery?.matches ?? false;
motionQuery?.addEventListener?.("change", (e) => {
  if (e.matches === reduced) return;
  reduced = e.matches;
  notify();
});

/** The kinds of picture that may move. */
const MAY_MOVE = /^data:image\/(gif|webp);/;
/** The still is made at most this many px on its shorter side: more than the largest face on the densest screen. */
const STILL_MAX_PX = 512;

/** The first frame of the person's picture that may move, once made: null while it is being made. */
let still: { url: string; frame: string | null } | null = null;

/** Draws the first frame of `url` onto a canvas; the picture itself when that cannot be done. */
async function makeStill(url: string): Promise<string> {
  try {
    const image = new Image();
    image.src = url;
    await image.decode();
    const w = image.naturalWidth;
    const h = image.naturalHeight;
    if (!w || !h) return url;
    const k = Math.min(1, STILL_MAX_PX / Math.min(w, h));
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(w * k));
    canvas.height = Math.max(1, Math.round(h * k));
    const ctx = canvas.getContext("2d");
    if (!ctx) return url;
    // A canvas paints the first frame of a moving picture (the HTML standard's drawImage).
    ctx.drawImage(image as CanvasImageSource, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL("image/png");
  } catch {
    // It cannot be decoded either way: the <img> of the picture will show nothing moving.
    return url;
  }
}

/**
 * The address to show the person's picture with: itself, or under reduced motion the still of one
 * that may move (null until it is made; every listener draws again then).
 */
function shownUrl(url: string): string | null {
  if (!reduced || !MAY_MOVE.test(url)) return url;
  if (still?.url === url) return still.frame;
  const mine = { url, frame: null as string | null };
  still = mine;
  void makeStill(url).then((frame) => {
    if (still !== mine) return;
    mine.frame = frame;
    notify();
  });
  return null;
}

/** The person's picture, or null while Raphael is his emblem. */
export const currentAvatar = (): CustomAvatar | null => custom;

/** Changes the picture everywhere: every listener draws again. */
export function setAvatar(next: CustomAvatar | null): void {
  custom = next;
  notify();
}

/** Hears the picture change. Returns how to stop hearing. */
export function onAvatarChange(listener: () => void): () => void {
  listeners.add(listener);
  return () => void listeners.delete(listener);
}

/** Reads the picture from the app; without one, or when it cannot be read, Raphael is his emblem. */
export async function loadAvatar(get: () => Promise<unknown>): Promise<void> {
  try {
    setAvatar(readAvatar(await get()));
  } catch {
    setAvatar(null);
  }
}

/** The style that cuts the picture as `crop` says, inside a square box (see `AvatarCrop`); the overlay's moving picture uses the same. */
export const cropStyle = (crop: AvatarCrop): string => pictureStyle(crop);

export interface AvatarOptions {
  /** The diameter in CSS pixels. */
  size: number;
  /** The turning loop of the emblem instead of the still: while Raphael thinks, listens or there is nothing yet. */
  loop?: boolean;
  /** More classes for the outer element. */
  cls?: string;
  /** The picture of the person's own, if any (by default the one in force now). */
  avatar?: CustomAvatar | null;
}

/**
 * Raphael's face at `size` px: the emblem for the theme (two pictures, the stylesheet shows the
 * one of the theme), or the person's picture in a circle. Decorative: whoever shows it names it.
 * Under reduced motion nothing in it moves (see above): the loop is the still, a moving picture
 * its first frame, and the circle is empty for the moment that frame takes to be made.
 */
export function avatarHTML(o: AvatarOptions): string {
  const chosen = o.avatar === undefined ? custom : o.avatar;
  const cls = `av${chosen ? " av-custom" : ""}${o.cls ? " " + o.cls : ""}`;
  const box = `style="--av:${Math.round(o.size)}px"`;
  if (chosen) {
    const url = shownUrl(chosen.url);
    const img = url === null ? "" : `<img src="${esc(url)}" alt="" draggable="false" style="${cropStyle(chosen.crop)}">`;
    return `<span class="${cls}" ${box} aria-hidden="true">${img}</span>`;
  }
  const kind = o.loop && !reduced ? "loop" : o.size > 40 ? "128" : "64";
  return (
    `<span class="${cls}" ${box} aria-hidden="true">` +
    `<img class="em-l" src="/brand/app-emblem-light-${kind}.webp" alt="" draggable="false">` +
    `<img class="em-d" src="/brand/app-emblem-dark-${kind}.webp" alt="" draggable="false">` +
    `</span>`
  );
}
