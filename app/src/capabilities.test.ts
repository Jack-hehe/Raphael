/// <reference types="node" />
// What the webviews may ask Tauri's own API for is written in src-tauri/capabilities/. These tests
// read those files, so that a grant which nothing uses has to be added on purpose.

import { describe, expect, test } from "vitest";
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";

describe("capabilities", () => {
  const dir = path.resolve(import.meta.dirname, "..", "src-tauri", "capabilities");
  const capabilities = readdirSync(dir)
    .filter((file) => file.endsWith(".json"))
    .map((file) => JSON.parse(readFileSync(path.join(dir, file), "utf8")) as { identifier: string; windows: string[]; permissions: string[] });

  test("every permission is a core one: the plugins are used from Rust, and no script imports them", () => {
    for (const capability of capabilities) {
      for (const permission of capability.permissions) {
        expect(permission, capability.identifier).toMatch(/^core:/);
      }
    }
  });

  test("the overlay window has none: its page only calls the app's own commands and opens a channel", () => {
    for (const capability of capabilities) {
      expect(capability.windows, capability.identifier).not.toContain("overlay");
      expect(capability.windows, capability.identifier).not.toContain("*");
    }
  });

  test("the main window keeps the core permissions and the frame controls it calls", () => {
    const main = capabilities.filter((capability) => capability.windows.includes("main")).flatMap((capability) => capability.permissions);
    expect(main).toEqual(
      expect.arrayContaining([
        "core:default",
        "core:window:allow-minimize",
        "core:window:allow-toggle-maximize",
        "core:window:allow-close",
        "core:window:allow-start-dragging",
        "core:window:allow-start-resize-dragging",
      ]),
    );
  });
});
