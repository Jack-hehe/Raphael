import { spawnSync } from "node:child_process";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { parseOptions } from "./options.mjs";

const app = resolve(import.meta.dirname, "../..");
const look = (...args) => spawnSync(process.execPath, ["scripts/look/index.mjs", ...args], { cwd: app, encoding: "utf8" });

describe("look CLI", () => {
  it("accepts explicit viewport dimensions alongside existing widths", () => {
    const options = parseOptions(["--width", "1120,720,1920x1080"], [{ name: "chat" }]);
    expect(options.viewports).toEqual([{ width: 1120, height: 900 }, { width: 720, height: 900 }, { width: 1920, height: 1080 }]);
    expect(() => parseOptions(["--width", "1920x0"], [])).toThrow(/Invalid/);
  });
  it("lists scenes without starting Vite or requiring a browser package", () => {
    const result = look("--list");
    expect(result.status, result.stderr).toBe(0);
    for (const scene of ["chat", "details", "automations-calendar", "connections", "dictation-history"])
      expect(result.stdout).toMatch(new RegExp(`^${scene}\\s`, "m"));
  });
  it.each([["unknown-scene"], ["chat", "--width", "0"], ["--all", "--theme", "sepia"], ["chat", "--locale", "fr"], ["chat", "--out"], ["chat", "--typo"]])("rejects invalid arguments before browser startup: %j", (...args) => {
    const result = look(...args);
    expect(result.status).toBe(1);
    expect(result.stderr).toMatch(/Invalid|Unknown|Missing/);
    expect(result.stderr).not.toContain("not implemented");
  });
});
