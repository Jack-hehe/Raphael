import publicIcon from "../../src-tauri/icons/128x128.png";
import "./character.css";

/** In-app art is independent of the desktop icon, with a bundled public fallback. */
export function brandIcon(className = ""): string {
  return `<img class="app-icon ${className}" data-brand-icon src="/private/in-app-icon.png" alt="" width="40" height="40" draggable="false">`;
}

/** Bind immediately after inserting the markup, before an unavailable private image can fail. */
export function hydrateBrandIcons(root: ParentNode): void {
  root.querySelectorAll<HTMLImageElement>("[data-brand-icon]").forEach((img) => {
    img.addEventListener("error", () => { img.src = publicIcon; }, { once: true });
  });
}
