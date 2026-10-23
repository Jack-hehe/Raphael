// Changing Raphael's picture, from his details panel on the 拉斐爾 page (member-details.ts), the
// same place as every member's: 「從電腦選…」 opens the file dialog of Windows from the app
// (`avatar_pick`: PNG, JPG, WebP or GIF; the page never learns where the file was), then a small
// dialog where the picture is dragged and zoomed inside the circle that will show it; 完成 keeps a
// copy of it in the app's local folder with that cut (`avatar_save`). 還原 deletes the copy, and
// Raphael is his emblem again (`avatar_clear`). A moving picture keeps moving: the copy is the file
// itself, and the cut is a crop drawn over it (`AvatarCrop`), not a new image.

import { t } from "./i18n";

import * as api from "./api";
import { type AvatarCrop, ZOOM_MAX, cleanCrop, cropStyle, loadAvatar } from "./avatar";
import { describeError } from "./format";
import { btn, esc, toaster } from "./ui";

/** The circle of the crop dialog, in px. */
const CIRCLE_PX = 220;

/**
 * Where a drag of `dx`, `dy` px moves the crop of a picture `w` × `h` in a circle of `side` px:
 * the point under the pointer follows it. (With the picture covering the circle and enlarged
 * `zoom` times about the point at `x`, `y`, a screen point moves by `side - zoom * (over + side)`
 * for each whole unit of `x`, where `over` is how much wider than the circle the picture is.)
 */
export function dragCrop(crop: AvatarCrop, dx: number, dy: number, w: number, h: number, side: number): AvatarCrop {
  if (w <= 0 || h <= 0) return crop;
  const cover = side / Math.min(w, h);
  const overX = w * cover - side;
  const overY = h * cover - side;
  const perX = side - crop.zoom * (overX + side);
  const perY = side - crop.zoom * (overY + side);
  return cleanCrop({
    x: perX === 0 ? crop.x : crop.x + dx / perX,
    y: perY === 0 ? crop.y : crop.y + dy / perY,
    zoom: crop.zoom,
  });
}

/** The crop dialog for the picture `url`: resolves with the cut chosen, or null when it is left. */
export function cropDialog(url: string): Promise<AvatarCrop | null> {
  return new Promise((resolve) => {
    let crop: AvatarCrop = cleanCrop(null);
    const scrim = document.createElement("div");
    scrim.className = "av-crop-scrim";
    scrim.innerHTML =
      `<div class="av-crop" role="dialog" aria-modal="true" aria-labelledby="av-crop-title">` +
      `<h2 id="av-crop-title">${esc(t("settings.avatar"))}</h2>` +
      `<div class="av-crop-view" style="--side:${CIRCLE_PX}px"><img src="${esc(url)}" alt="" draggable="false"></div>` +
      `<input class="av-crop-zoom" type="range" min="1" max="${ZOOM_MAX}" step="0.01" value="1" aria-label="${esc(t("settings.avatarZoom"))}">` +
      `<div class="av-crop-act">${btn(esc(t("common.cancel")), "", ' data-act="crop-cancel"')}${btn(esc(t("settings.avatarDone")), "pri", ' data-act="crop-done"')}</div>` +
      `</div>`;
    const view = scrim.querySelector<HTMLElement>(".av-crop-view")!;
    const img = view.querySelector("img")!;
    const zoom = scrim.querySelector<HTMLInputElement>(".av-crop-zoom")!;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const draw = (): void => img.setAttribute("style", cropStyle(crop));
    const close = (result: AvatarCrop | null): void => {
      scrim.remove();
      document.removeEventListener("keydown", onKey, true);
      opener?.focus();
      resolve(result);
    };
    const onKey = (e: KeyboardEvent): void => {
      if (e.key !== "Escape") return;
      e.preventDefault();
      e.stopPropagation();
      close(null);
    };
    let from: { x: number; y: number } | null = null;
    view.addEventListener("pointerdown", (e) => {
      view.setPointerCapture(e.pointerId);
      from = { x: e.clientX, y: e.clientY };
    });
    view.addEventListener("pointermove", (e) => {
      if (!from) return;
      crop = dragCrop(crop, e.clientX - from.x, e.clientY - from.y, img.naturalWidth, img.naturalHeight, CIRCLE_PX);
      from = { x: e.clientX, y: e.clientY };
      draw();
    });
    const drop = (): void => void (from = null);
    view.addEventListener("pointerup", drop);
    view.addEventListener("pointercancel", drop);
    view.addEventListener(
      "wheel",
      (e) => {
        e.preventDefault();
        crop = cleanCrop({ ...crop, zoom: crop.zoom * (e.deltaY < 0 ? 1.08 : 1 / 1.08) });
        zoom.value = String(crop.zoom);
        draw();
      },
      { passive: false },
    );
    zoom.addEventListener("input", () => {
      crop = cleanCrop({ ...crop, zoom: Number(zoom.value) });
      draw();
    });
    scrim.addEventListener("click", (e) => {
      const act = (e.target as Element).closest<HTMLElement>("[data-act]")?.dataset.act;
      if (act === "crop-cancel" || e.target === scrim) close(null);
      else if (act === "crop-done") close(crop);
    });
    document.addEventListener("keydown", onKey, true);
    draw();
    document.body.append(scrim);
    scrim.querySelector<HTMLElement>('[data-act="crop-done"]')!.focus();
  });
}

/** 從電腦選…: the file dialog, the crop dialog, then the copy kept. */
export async function changeRaphaelAvatar(): Promise<void> {
  let url: string | null;
  try {
    url = await api.avatarPick();
  } catch (err) {
    toaster.info(describeError(err));
    return;
  }
  if (url === null) return; // the dialog was closed
  const crop = await cropDialog(url);
  if (crop === null) return;
  try {
    await api.avatarSave(crop);
  } catch (err) {
    toaster.info(describeError(err));
    return;
  }
  await loadAvatar(api.avatarGet);
}

/** 還原: the copy is deleted and Raphael is his emblem. */
export async function resetRaphaelAvatar(): Promise<void> {
  try {
    await api.avatarClear();
  } catch (err) {
    toaster.info(describeError(err));
    return;
  }
  await loadAvatar(api.avatarGet);
}
