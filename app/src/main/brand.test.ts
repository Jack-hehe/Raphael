import { describe, expect, it, vi } from "vitest";
import { brandIcon, characterPortrait, hydrateBrandIcons } from "./brand";

describe("brand icon", () => {
  it("uses the local portrait and falls back once when it is unavailable", () => {
    const html = brandIcon();
    expect(html).toContain('src="/private/app-icon.png"');
    expect(html).toContain('alt=""');
    let fail: (() => void) | undefined;
    const img = { src: "/private/app-icon.png", addEventListener: vi.fn((_event, handler) => { fail = handler; }) };
    const root = { querySelectorAll: (selector: string) => selector === "[data-brand-icon]" ? [img] : [] } as unknown as ParentNode;
    hydrateBrandIcons(root);
    fail!();
    expect(img.src).toContain("128x128.png");
    expect(img.addEventListener).toHaveBeenCalledWith("error", expect.any(Function), { once: true });
  });
});

describe("character portrait", () => {
  it("uses distinct private art with an inline vector fallback", () => {
    const html = characterPortrait();
    expect(html).toContain('src="/private/avatar.png"');
    expect(html).not.toContain("app-icon.png");
    expect(html).toContain("<svg");
  });

  it("reveals the portrait only on successful load and hides a failed image", () => {
    const listeners: Record<string, () => void> = {};
    const classes = new Set<string>();
    const host = { classList: { add: (name: string) => classes.add(name), remove: (name: string) => classes.delete(name) } };
    const img = {
      hidden: false, complete: false, naturalWidth: 0, parentElement: host,
      addEventListener: (event: string, handler: () => void) => { listeners[event] = handler; },
    };
    const root = { querySelectorAll: (selector: string) => selector === "[data-character-avatar]" ? [img] : [] } as unknown as ParentNode;
    hydrateBrandIcons(root);
    expect(classes.has("has-portrait")).toBe(false);
    listeners.load();
    expect(classes.has("has-portrait")).toBe(true);
    listeners.error();
    expect(img.hidden).toBe(true);
    expect(classes.has("has-portrait")).toBe(false);
  });
});
