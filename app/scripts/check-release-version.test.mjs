import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";
import { readVersions, releaseVersion } from "./check-release-version.mjs";

const scripts = dirname(fileURLToPath(import.meta.url));
const fixtureDirectory = join(scripts, "..", "node_modules", ".cache", "release-version-tests");
const temporaryRoots = [];

afterEach(() => {
  for (const root of temporaryRoots.splice(0)) {
    if (relative(fixtureDirectory, root).startsWith("..")) throw new Error("Fixture escaped its cache directory");
    rmSync(root, { recursive: true, force: true });
  }
});

const cargo = (version) => `[package]
name = "app"
version = "${version}"
edition = "2024"

[dependencies]
tauri = { version = "2.12", features = ["tray-icon"] }

[target.'cfg(windows)'.dependencies]
windows = { version = "0.62" }
`;
const pyproject = (version) => `[project]
name = "raphael-core"
version = "${version}"
dependencies = ["httpx>=0.28.1,<1"]

[dependency-groups]
dev = ["pytest>=8"]
`;

function repository(versions = {}, { cargoText, pyprojectText, initText } = {}) {
  mkdirSync(fixtureDirectory, { recursive: true });
  const root = mkdtempSync(join(fixtureDirectory, "case-"));
  temporaryRoots.push(root);
  const write = (path, text) => {
    mkdirSync(dirname(join(root, path)), { recursive: true });
    writeFileSync(join(root, path), text);
  };
  const { npm = "1.2.3", tauri = "1.2.3", rust = "1.2.3", python = "1.2.3", core = "1.2.3" } = versions;
  write("app/package.json", JSON.stringify({ name: "app", version: npm }));
  write("app/src-tauri/tauri.conf.json", JSON.stringify({ productName: "Raphael", version: tauri }));
  write("app/src-tauri/Cargo.toml", cargoText ?? cargo(rust));
  write("core/pyproject.toml", pyprojectText ?? pyproject(python));
  write("core/src/raphael_core/__init__.py", initText ?? `"""Raphael's Python core."""\n\n__version__ = "${core}"\n`);
  return root;
}

describe("release version gate", () => {
  it("returns the version that all five files share", () => {
    const root = repository();
    expect(releaseVersion(root)).toBe("1.2.3");
    expect(readVersions(root)).toEqual({
      "app/package.json": "1.2.3", "app/src-tauri/tauri.conf.json": "1.2.3",
      "app/src-tauri/Cargo.toml": "1.2.3", "core/pyproject.toml": "1.2.3",
      "core/src/raphael_core/__init__.py": "1.2.3",
    });
  });

  it.each([
    [{ npm: "1.2.4" }, "app/package.json: 1.2.4"],
    [{ tauri: "1.3.0" }, "app/src-tauri/tauri.conf.json: 1.3.0"],
    [{ rust: "2.0.0" }, "app/src-tauri/Cargo.toml: 2.0.0"],
    [{ python: "1.2.2" }, "core/pyproject.toml: 1.2.2"],
    [{ core: "1.2.5" }, "core/src/raphael_core/__init__.py: 1.2.5"],
  ])("refuses a release whose files disagree: %j", (versions, listing) => {
    expect(() => releaseVersion(repository(versions))).toThrow(listing);
  });

  it.each(["1.2", "1.2.3-beta.1", "v1.2.3", "1.2.3.4", ""])("refuses %j, which is not a numeric major.minor.patch", (version) => {
    const root = repository({ npm: version, tauri: version, rust: version, python: version, core: version });
    expect(() => releaseVersion(root)).toThrow("numeric major.minor.patch");
  });

  it("reads the version of the package table only, not of dependencies or other tables", () => {
    const cargoText = `# dependencies come first here
[dependencies]
tauri = { version = "9.9.9" }

[dependencies.serde]
version = "8.8.8"

[[bin]]
name = "app"
version = "7.7.7"

[package]
name = "app"
version = "1.2.3" # the release
`;
    const pyprojectText = `[tool.other]\nversion = "6.6.6"\n\n[project]\r\nname = "raphael-core"\r\nversion = "1.2.3"\r\n`;
    expect(releaseVersion(repository({}, { cargoText, pyprojectText }))).toBe("1.2.3");
  });

  it("reads the core's own __version__ assignment, not a line that only mentions it", () => {
    const initText = `"""Raphael's Python core."""\r\n\r\n# __version__ = "9.9.9" was the old one\r\n__version__ = '1.2.3'\r\n`;
    expect(readVersions(repository({}, { initText }))["core/src/raphael_core/__init__.py"]).toBe("1.2.3");
  });

  it("reports a missing version instead of treating it as a match", () => {
    const root = repository({}, { cargoText: "[package]\nname = \"app\"\n", pyprojectText: "[project]\nname = \"raphael-core\"\n" });
    expect(() => releaseVersion(root)).toThrow("app/src-tauri/Cargo.toml: no version found");
  });

  it("holds for this repository, so a version bump that misses a file fails before a release is built", () => {
    expect(releaseVersion()).toMatch(/^\d+\.\d+\.\d+$/);
  });

  it("prints the version and exits cleanly from the command line", () => {
    const result = spawnSync(process.execPath, [join(scripts, "check-release-version.mjs")], { encoding: "utf8" });
    expect(result.status, result.stderr).toBe(0);
    expect(result.stdout.trim()).toBe(releaseVersion());
  });
});
