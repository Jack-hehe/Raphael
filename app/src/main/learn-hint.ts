// Editors that hide their text from UI Automation, so the corrections made in them cannot be
// learned (the `LearnLimited` main event, sent at most once per editor in a run of Raphael). The
// dictionary page shows one hint per editor until it is dismissed; a dismissal is remembered in
// this browser's storage, and is only a convenience: without storage the hint comes back next run.

import type { MainEvent } from "./types";

const DISMISSED_KEY = "raphael.learnHint.dismissed";

const NAMES: Record<string, string> = {
  vscode: "VS Code",
  cursor: "Cursor",
  vscodium: "VSCodium",
  windsurf: "Windsurf",
  trae: "Trae",
  positron: "Positron",
};

/** The name to show for an editor the shell reported (its own identifier when unknown). */
export const editorName = (app: string): string => NAMES[app] ?? app;

function readDismissed(storage: Storage | undefined): Set<string> {
  try {
    const list: unknown = JSON.parse(storage?.getItem(DISMISSED_KEY) ?? "[]");
    return new Set(Array.isArray(list) ? list.filter((app): app is string => typeof app === "string") : []);
  } catch {
    return new Set();
  }
}

export class LearnHints {
  private readonly limited = new Set<string>();
  private readonly dismissed: Set<string>;
  private readonly listeners = new Set<() => void>();

  constructor(private readonly storage: Storage | undefined = globalThis.localStorage) {
    this.dismissed = readDismissed(storage);
  }

  handle(event: MainEvent): void {
    if (event.type !== "LearnLimited" || event.reason !== "accessibility_off" || this.limited.has(event.app)) return;
    this.limited.add(event.app);
    this.notify();
  }

  /** The editors to mention, in the order they were reported. */
  pending(): string[] {
    return [...this.limited].filter((app) => !this.dismissed.has(app));
  }

  dismiss(app: string): void {
    this.dismissed.add(app);
    try {
      this.storage?.setItem(DISMISSED_KEY, JSON.stringify([...this.dismissed]));
    } catch {
      // the hint only comes back in the next run
    }
    this.notify();
  }

  onChange(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => void this.listeners.delete(listener);
  }

  private notify(): void {
    this.listeners.forEach((listener) => listener());
  }
}
