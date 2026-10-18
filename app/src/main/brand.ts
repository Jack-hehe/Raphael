import publicIcon from "../../src-tauri/icons/128x128.png";
import { esc } from "./ui";
import "./character.css";

/** Private art stays in Vite's ignored public directory, with a bundled public fallback. */
export function brandIcon(className = ""): string {
  return `<img class="app-icon ${className}" data-brand-icon src="/private/app-icon.png" alt="" width="32" height="32" draggable="false">`;
}

/** A character portrait, with a ring/core vector available before loading and on failure. */
export function characterPortrait(className = ""): string {
  return `<span class="character-portrait ${esc(className)}" aria-hidden="true">` +
    `<svg class="character-fallback" viewBox="-60 -60 120 120" fill="none" stroke="currentColor">` +
    `<circle r="42" stroke-width="1" stroke-dasharray="2 5"/><circle r="30" stroke-width="2"/>` +
    `<path d="M0-54V-38M0 38V54M-54 0H-38M38 0H54M-38-38L-27-27M27 27L38 38M-38 38L-27 27M27-27L38-38"/>` +
    `<circle r="3" fill="currentColor" stroke="none"/></svg>` +
    `<img data-character-avatar src="/private/avatar.png" alt="" width="160" height="160" draggable="false"></span>`;
}

/** Bind immediately after inserting the markup, before an unavailable private image can fail. */
export function hydrateBrandIcons(root: ParentNode): void {
  root.querySelectorAll<HTMLImageElement>("[data-brand-icon]").forEach((img) => {
    img.addEventListener("error", () => { img.src = publicIcon; }, { once: true });
  });
  root.querySelectorAll<HTMLImageElement>("[data-character-avatar]").forEach((img) => {
    const show = (): void => { img.parentElement?.classList.add("has-portrait"); };
    const fallback = (): void => {
      img.hidden = true;
      img.parentElement?.classList.remove("has-portrait");
    };
    img.addEventListener("load", show, { once: true });
    img.addEventListener("error", fallback, { once: true });
    // Cached images may have finished before the markup was hydrated.
    if (img.complete) {
      if (img.naturalWidth > 0) show();
      else fallback();
    }
  });
}
