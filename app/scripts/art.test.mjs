import { existsSync, readFileSync, statSync } from "node:fs";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";

const app = resolve(import.meta.dirname, "..");
const tauri = join(app, "src-tauri");
const pngSize = (path) => {
  const header = readFileSync(path).subarray(0, 24);
  expect(header.subarray(1, 4).toString()).toBe("PNG");
  return [header.readUInt32BE(16), header.readUInt32BE(20)];
};

describe("artwork shipped in the repository", () => {
  it("has every icon the Tauri config lists", () => {
    const config = JSON.parse(readFileSync(join(tauri, "tauri.conf.json"), "utf8"));
    expect(config.bundle.icon.length).toBeGreaterThan(0);
    for (const icon of config.bundle.icon) expect(statSync(join(tauri, icon)).isFile(), icon).toBe(true);
  });

  it("has the source icon and the 32x32 tray icon", () => {
    const [width, height] = pngSize(join(tauri, "icons", "app-icon.png"));
    expect(width).toBe(height);
    expect(pngSize(join(tauri, "icons", "tray-icon.png"))).toEqual([32, 32]);
    expect(existsSync(join(tauri, "icons", "icon.ico"))).toBe(true);
  });

  it("serves the in-app artwork from public/brand", () => {
    for (const name of ["in-app-icon.png", "glyph-ring.png"]) pngSize(join(app, "public", "brand", name));
  });
});
