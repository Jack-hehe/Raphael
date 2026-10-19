import type { Window } from "@tauri-apps/api/window";

type ResizeDirection = Parameters<Window["startResizeDragging"]>[0];

/** The native operations used by the main window's custom frame. */
export interface WindowChromeWindow {
  minimize(): Promise<void>;
  toggleMaximize(): Promise<void>;
  close(): Promise<void>;
  startDragging(): Promise<void>;
  startResizeDragging(direction: ResizeDirection): Promise<void>;
  isMaximized(): Promise<boolean>;
  onResized(handler: () => void): Promise<() => void>;
}

const DIRECTIONS = new Set<ResizeDirection>([
  "North", "NorthEast", "East", "SouthEast", "South", "SouthWest", "West", "NorthWest",
]);
const INTERACTIVE = "button, a, input, select, textarea, [contenteditable], [role='button']";
/**
 * A drag sends a resize event for every step, but whether the window is maximized only has to be
 * asked once they have stopped coming for this long.
 */
export const RESIZE_SETTLE_MS = 100;

/** Bind real DOM events; native operations and state rendering are the only seams. */
export function installWindowChrome(
  target: EventTarget,
  window: WindowChromeWindow,
  render: (maximized: boolean) => void,
  report: (error: unknown) => void = (error) => console.warn("window chrome request failed", error),
): () => void {
  let active = true;
  let maximized = false;
  let generation = 0;
  let handledDoubleClick = false;
  let unlisten: (() => void) | undefined;
  /** What `render` last drew; undefined until the first answer. */
  let drawn: boolean | undefined;
  let settling: ReturnType<typeof setTimeout> | undefined;
  const run = (operation: () => Promise<unknown>): void => {
    void operation().catch((error) => { if (active) report(error); });
  };
  const refresh = async (): Promise<void> => {
    const request = ++generation;
    const state = await window.isMaximized();
    if (!active || request !== generation) return;
    maximized = state;
    // The controls only differ when the state does: a resize that left it alone has nothing to redraw.
    if (state === drawn) return;
    drawn = state;
    render(state);
  };
  const resized = (): void => {
    clearTimeout(settling);
    settling = setTimeout(() => run(refresh), RESIZE_SETTLE_MS);
  };
  const toggle = async (): Promise<void> => {
    await window.toggleMaximize();
    await refresh();
  };
  const element = (event: Event): Element | null => {
    const node = event.target;
    return node && "closest" in node && typeof node.closest === "function" ? node as Element : null;
  };
  const click = (event: Event): void => {
    const action = element(event)?.closest<HTMLElement>("[data-window-action]")?.dataset.windowAction;
    if (action === "minimize") run(() => window.minimize());
    if (action === "maximize") run(toggle);
    // close() preserves Rust's close-request handler, which hides the window to the tray.
    if (action === "close") run(() => window.close());
  };
  const press = (event: Event): void => {
    const mouse = event as MouseEvent;
    if (mouse.button !== 0) return;
    const node = element(event);
    const direction = node?.closest<HTMLElement>("[data-window-resize]")?.dataset.windowResize as ResizeDirection | undefined;
    if (direction && DIRECTIONS.has(direction)) {
      if (!maximized) {
        event.preventDefault();
        run(() => window.startResizeDragging(direction));
      }
      return;
    }
    if (!node?.closest("[data-window-drag]") || node.closest(INTERACTIVE)) return;
    event.preventDefault();
    handledDoubleClick = mouse.detail === 2;
    // Native dragging may consume dblclick, so also handle its second mouse press.
    if (handledDoubleClick) run(toggle);
    else run(() => window.startDragging());
  };
  const doubleClick = (event: Event): void => {
    const node = element(event);
    if ((event as MouseEvent).button !== 0 || !node?.closest("[data-window-drag]") || node.closest(INTERACTIVE)) return;
    event.preventDefault();
    if (!handledDoubleClick) run(toggle);
    handledDoubleClick = false;
  };
  target.addEventListener("click", click);
  target.addEventListener("mousedown", press);
  target.addEventListener("dblclick", doubleClick);
  run(refresh);
  run(async () => {
    const stop = await window.onResized(resized);
    if (active) unlisten = stop;
    else stop();
  });
  return () => {
    active = false;
    generation++;
    clearTimeout(settling);
    target.removeEventListener("click", click);
    target.removeEventListener("mousedown", press);
    target.removeEventListener("dblclick", doubleClick);
    unlisten?.();
  };
}
