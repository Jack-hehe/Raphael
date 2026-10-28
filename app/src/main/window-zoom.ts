// Ctrl + plus / minus / zero zoom the main window by fixed steps. WebView2's own zoom keys allow
// 25%–500%, which the owner found far too much (2026-10-25: 「正負 3 就可以」), so the app handles the
// keys itself and remembers the level.

/** Three steps either way of 100%. */
export const ZOOM_LEVELS = [0.7, 0.8, 0.9, 1, 1.1, 1.2, 1.3] as const;
export type ZoomAction = "in" | "out" | "reset";

const STORAGE_KEY = "raphael.zoom";

interface ZoomStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

function nearestIndex(factor: number): number {
  let best = ZOOM_LEVELS.indexOf(1);
  ZOOM_LEVELS.forEach((level, index) => {
    if (Math.abs(level - factor) < Math.abs(ZOOM_LEVELS[best]! - factor)) best = index;
  });
  return best;
}

export function nextZoom(current: number, action: ZoomAction): number {
  if (action === "reset") return 1;
  const index = nearestIndex(current) + (action === "in" ? 1 : -1);
  return ZOOM_LEVELS[Math.min(ZOOM_LEVELS.length - 1, Math.max(0, index))]!;
}

export function zoomAction(event: KeyboardEvent): ZoomAction | null {
  if (!(event.ctrlKey || event.metaKey) || event.altKey) return null;
  switch (event.code) {
    case "Equal": case "NumpadAdd": return "in";
    case "Minus": case "NumpadSubtract": return "out";
    case "Digit0": case "Numpad0": return "reset";
    default: return null;
  }
}

function saved(storage: ZoomStorage | undefined): number {
  try {
    const value = Number(storage?.getItem(STORAGE_KEY));
    return Number.isFinite(value) && value > 0 ? ZOOM_LEVELS[nearestIndex(value)]! : 1;
  } catch {
    return 1;
  }
}

/** Applies the saved level, then handles the zoom keys on `target` with `setZoom` (the webview's). */
export function installWindowZoom(
  target: EventTarget,
  setZoom: (factor: number) => Promise<void>,
  storage: ZoomStorage | undefined = globalThis.localStorage,
): () => void {
  let level = saved(storage);
  const apply = () => setZoom(level).catch((error) => console.warn("window zoom failed", error));
  void apply();
  const onKey = (event: Event) => {
    const action = zoomAction(event as KeyboardEvent);
    if (!action) return;
    event.preventDefault();
    level = nextZoom(level, action);
    try {
      storage?.setItem(STORAGE_KEY, String(level));
    } catch {
      // A blocked storage only loses the remembered level.
    }
    void apply();
  };
  target.addEventListener("keydown", onKey, { capture: true });
  return () => target.removeEventListener("keydown", onKey, { capture: true });
}
