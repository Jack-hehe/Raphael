import "./character.css";

/** The in-app artwork (app/public/brand/in-app-icon.png), shown in the sidebar and the About page. */
export function brandIcon(className = ""): string {
  return `<img class="app-icon ${className}" src="/brand/in-app-icon.png" alt="" width="40" height="40" draggable="false">`;
}
