import { describe, expect, it, vi } from "vitest";
import { brandIcon, hydrateBrandIcons } from "./brand";

describe("brand icon", () => {
  it("uses the local portrait and falls back once when it is unavailable", () => {
    const html = brandIcon();
    expect(html).toContain('src="/private/app-icon.png"');
    expect(html).toContain('alt=""');
    let fail: (() => void) | undefined;
    const img = { src: "/private/app-icon.png", addEventListener: vi.fn((_event, handler) => { fail = handler; }) };
    const root = { querySelectorAll: () => [img] } as unknown as ParentNode;
    hydrateBrandIcons(root);
    fail!();
    expect(img.src).toContain("128x128.png");
    expect(img.addEventListener).toHaveBeenCalledWith("error", expect.any(Function), { once: true });
  });
});
