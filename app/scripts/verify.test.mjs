import { spawnSync } from "node:child_process";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

const app = resolve(import.meta.dirname, "..");
describe("verify CLI", () => {
  it("documents optional core/Rust checks without executing them", () => {
    const result = spawnSync(process.execPath, ["scripts/verify.mjs", "--help"], { cwd: app, encoding: "utf8" });
    expect(result.status, result.stderr).toBe(0);
    expect(result.stdout).toContain("--quick");
    expect(result.stdout).toContain("--core");
    expect(result.stdout).toContain("--rust");
    expect(result.stdout).toContain("--out dir");
  });
  it("rejects an output flag without a directory", () => {
    for (const args of [["--out"], ["--out", "--quick"]]) {
      const result = spawnSync(process.execPath, ["scripts/verify.mjs", ...args], { cwd: app, encoding: "utf8" });
      expect(result.status).toBe(1);
      expect(result.stderr).toContain("Unknown option: --out");
    }
  });
});
