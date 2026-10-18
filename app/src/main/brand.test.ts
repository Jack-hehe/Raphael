import { describe, expect, it, vi } from "vitest";
import { brandIcon, characterEmblem, hydrateBrandIcons } from "./brand";

describe("brand icon", () => {
  it("uses private in-app artwork and falls back once when it is unavailable", () => {
    const html = brandIcon();
    expect(html).toContain('src="/private/in-app-icon.png"');
    expect(html).not.toContain('src="/private/app-icon.png"');
    expect(html).toContain('alt=""');
    let fail: (() => void) | undefined;
    const img = { src: "/private/in-app-icon.png", addEventListener: vi.fn((_event, handler) => { fail = handler; }) };
    const root = { querySelectorAll: (selector: string) => selector === "[data-brand-icon]" ? [img] : [] } as unknown as ParentNode;
    hydrateBrandIcons(root);
    fail!();
    expect(img.src).toContain("128x128.png");
    expect(img.addEventListener).toHaveBeenCalledWith("error", expect.any(Function), { once: true });
  });
});

describe("character emblem", () => {
  it("renders a vector ring without an image asset", () => {
    const html = characterEmblem();
    expect(html).toContain("<svg");
    expect(html).toContain("<circle");
    expect(html).not.toContain("<img");
    expect(html).not.toContain("/private/");
  });
});
