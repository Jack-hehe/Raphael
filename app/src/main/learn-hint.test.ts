import { describe, expect, it } from "vitest";

import { LearnHints, editorName } from "./learn-hint";

class MemoryStorage {
  private readonly items = new Map<string, string>();
  getItem(key: string): string | null {
    return this.items.get(key) ?? null;
  }
  setItem(key: string, value: string): void {
    this.items.set(key, value);
  }
}

const limited = (app: string) => ({ type: "LearnLimited" as const, app, reason: "accessibility_off" });

describe("LearnHints", () => {
  it("lists each limited editor once, in the order reported", () => {
    const hints = new LearnHints(new MemoryStorage() as unknown as Storage);
    let changes = 0;
    hints.onChange(() => changes++);
    hints.handle(limited("vscode"));
    hints.handle(limited("cursor"));
    hints.handle(limited("vscode"));
    expect(hints.pending()).toEqual(["vscode", "cursor"]);
    expect(changes).toBe(2);
  });

  it("ignores other events and other reasons", () => {
    const hints = new LearnHints(new MemoryStorage() as unknown as Storage);
    hints.handle({ type: "Level", value: 0.5 });
    hints.handle({ type: "LearnLimited", app: "vscode", reason: "something_else" });
    expect(hints.pending()).toEqual([]);
  });

  it("remembers a dismissal across runs", () => {
    const storage = new MemoryStorage() as unknown as Storage;
    const first = new LearnHints(storage);
    first.handle(limited("vscode"));
    first.dismiss("vscode");
    expect(first.pending()).toEqual([]);
    const next = new LearnHints(storage);
    next.handle(limited("vscode"));
    expect(next.pending()).toEqual([]);
  });

  it("works without storage", () => {
    const hints = new LearnHints(undefined);
    hints.handle(limited("windsurf"));
    hints.dismiss("windsurf");
    expect(hints.pending()).toEqual([]);
  });

  it("names the known editors", () => {
    expect(editorName("vscode")).toBe("VS Code");
    expect(editorName("unknown-editor")).toBe("unknown-editor");
  });
});
