import { describe, expect, it } from "vitest";
import { brandIcon } from "./brand";

describe("brand icon", () => {
  it("shows the in-app artwork", () => {
    const html = brandIcon("about-icon");
    expect(html).toContain('src="/brand/in-app-icon.png"');
    expect(html).toContain("about-icon");
    expect(html).toContain('alt=""');
  });
});
