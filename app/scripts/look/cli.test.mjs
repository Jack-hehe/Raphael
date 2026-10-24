import { spawnSync } from "node:child_process";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const app = resolve(import.meta.dirname, "../..");
const look = (...args) => spawnSync(process.execPath, ["scripts/look/index.mjs", ...args], { cwd: app, encoding: "utf8" });

describe("look CLI", () => {
  it("lists scenes without starting Vite or requiring a browser package", () => {
    const result = look("--list");
    expect(result.status, result.stderr).toBe(0);
    for (const scene of ["chat", "picker", "automations-calendar", "connections", "dictation-history"])
      expect(result.stdout).toMatch(new RegExp(`^${scene}\\s`, "m"));
  });
  it.each([["unknown-scene"], ["chat", "--width", "0"], ["--all", "--theme", "sepia"], ["chat", "--locale", "fr"], ["chat", "--out"], ["chat", "--typo"]])("rejects invalid arguments before browser startup: %j", (...args) => {
    const result = look(...args);
    expect(result.status).toBe(1);
    expect(result.stderr).toMatch(/Invalid|Unknown|Missing/);
    expect(result.stderr).not.toContain("not implemented");
  });
});
