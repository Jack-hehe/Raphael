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

/** The person's picture, or null while Raphael is his emblem. */
export const currentAvatar = (): CustomAvatar | null => custom;

/** Changes the picture everywhere: every listener draws again. */
export function setAvatar(next: CustomAvatar | null): void {
  custom = next;
  for (const listener of listeners) listener();
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

/** The style that cuts the picture as `crop` says, inside a square box (see `AvatarCrop`). */
export function cropStyle(crop: AvatarCrop): string {
  const x = `${Math.round(crop.x * 1000) / 10}%`;
  const y = `${Math.round(crop.y * 1000) / 10}%`;
  return `object-position:${x} ${y};transform-origin:${x} ${y};transform:scale(${Math.round(crop.zoom * 1000) / 1000})`;
}

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
 */
export function avatarHTML(o: AvatarOptions): string {
  const chosen = o.avatar === undefined ? custom : o.avatar;
  const cls = `av${chosen ? " av-custom" : ""}${o.cls ? " " + o.cls : ""}`;
  const box = `style="--av:${Math.round(o.size)}px"`;
  if (chosen) {
    return `<span class="${cls}" ${box} aria-hidden="true"><img src="${esc(chosen.url)}" alt="" draggable="false" style="${cropStyle(chosen.crop)}"></span>`;
  }
  const kind = o.loop ? "loop" : o.size > 40 ? "128" : "64";
  return (
    `<span class="${cls}" ${box} aria-hidden="true">` +
    `<img class="em-l" src="/brand/app-emblem-light-${kind}.webp" alt="" draggable="false">` +
    `<img class="em-d" src="/brand/app-emblem-dark-${kind}.webp" alt="" draggable="false">` +
    `</span>`
  );
}
