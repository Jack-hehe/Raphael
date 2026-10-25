import { spawnSync } from "node:child_process";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const app = resolve(import.meta.dirname, "../..");
const look = (...args) => spawnSync(process.execPath, ["scripts/look/index.mjs", ...args], { cwd: app, encoding: "utf8" });

describe("look CLI", () => {
  it("takes a full-screen size as WIDTHxHEIGHT beside plain widths", async () => {
    const { parseOptions } = await import("./options.mjs");
    const options = parseOptions(["chat", "--width", "1120,720,1920x1080"], [{ name: "chat" }]);
    expect(options.widths).toEqual([1120, 720, 1920]);
    expect(options.heights).toEqual({ 1920: 1080 });
  });
  it("lists scenes without starting Vite or requiring a browser package", () => {
    const result = look("--list");
    expect(result.status, result.stderr).toBe(0);
    for (const scene of ["chat", "details", "automations-calendar", "connections", "dictation-history"])
      expect(result.stdout).toMatch(new RegExp(`^${scene}\\s`, "m"));
  });
  it.each([["unknown-scene"], ["chat", "--width", "0"], ["chat", "--width", "1920x100"], ["--all", "--theme", "sepia"], ["chat", "--locale", "fr"], ["chat", "--out"], ["chat", "--typo"]])("rejects invalid arguments before browser startup: %j", (...args) => {
    const result = look(...args);
    expect(result.status).toBe(1);
    expect(result.stderr).toMatch(/Invalid|Unknown|Missing/);
    expect(result.stderr).not.toContain("not implemented");
  });
});
