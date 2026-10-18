import publicIcon from "../../src-tauri/icons/128x128.png";
import { esc } from "./ui";
import "./character.css";

/** In-app art is independent of the desktop icon, with a bundled public fallback. */
export function brandIcon(className = ""): string {
  return `<img class="app-icon ${className}" data-brand-icon src="/private/in-app-icon.png" alt="" width="40" height="40" draggable="false">`;
}

/** Raphael's ring/core emblem is independent of the personal application icon. */
export function characterEmblem(className = ""): string {
  return `<span class="character-emblem ${esc(className)}" aria-hidden="true">` +
    `<svg viewBox="-60 -60 120 120" fill="none" stroke="currentColor">` +
    `<circle r="42" stroke-width="1" stroke-dasharray="2 5"/><circle r="30" stroke-width="2"/>` +
    `<path d="M0-54V-38M0 38V54M-54 0H-38M38 0H54M-38-38L-27-27M27 27L38 38M-38 38L-27 27M27-27L38-38"/>` +
    `<circle r="3" fill="currentColor" stroke="none"/></svg></span>`;
}

/** Bind immediately after inserting the markup, before an unavailable private image can fail. */
export function hydrateBrandIcons(root: ParentNode): void {
  root.querySelectorAll<HTMLImageElement>("[data-brand-icon]").forEach((img) => {
    img.addEventListener("error", () => { img.src = publicIcon; }, { once: true });
  });
}
