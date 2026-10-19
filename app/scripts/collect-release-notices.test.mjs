import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";
import {
  collectDependencyNotices, collectLicenseTexts, collectReleaseNotices, renderDependencyNotices,
} from "./collect-release-notices.mjs";

const scripts = dirname(fileURLToPath(import.meta.url));
const fixtureDirectory = join(scripts, "..", "node_modules", ".cache", "release-notices-tests");
const temporaryRoots = [];
const mit = `Copyright (c) 2026 Fixture contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.`;

afterEach(() => {
  for (const root of temporaryRoots.splice(0)) {
    if (relative(fixtureDirectory, root).startsWith("..")) throw new Error("Fixture escaped its cache directory");
    rmSync(root, { recursive: true, force: true });
  }
});

function write(path, text) {
  mkdirSync(dirname(path), { recursive: true });
  writeFileSync(path, text);
}

function fixture() {
  mkdirSync(fixtureDirectory, { recursive: true });
  const root = mkdtempSync(join(fixtureDirectory, "case-"));
  temporaryRoots.push(root);
  const appDirectory = join(root, "app");
  const pythonNotices = join(root, "core", "dist", "raphael-core", "THIRD_PARTY_NOTICES.txt");
  write(pythonNotices, "Python-package 1.2.3\r\nSPDX: MIT\r\nPython license text\r\n");
  const metadata = {
    packages: [{ id: "root", name: "app", version: "0.1.0", manifest_path: join(appDirectory, "src-tauri", "Cargo.toml") }],
    resolve: { root: "root", nodes: [{ id: "root", deps: [] }] },
  };
  const lock = { lockfileVersion: 3, packages: { "": { name: "app", version: "0.1.0" } } };
  const options = { appDirectory, pythonNotices, metadata };
  const saveLock = () => write(join(appDirectory, "package-lock.json"), JSON.stringify(lock));
  saveLock();
  return {
    root, ...options, lock, options, saveLock,
    rust(name, version, files = { LICENSE: mit }, { connected = true, vendored = false, licenseFile } = {}) {
      const directory = vendored ? join(appDirectory, "src-tauri", "vendor", name) : join(root, "crates", `${name}-${version}`);
      const pkg = {
        id: `${name}@${version}`, name, version, manifest_path: join(directory, "Cargo.toml"),
        license: "MIT", license_file: licenseFile, repository: `https://github.com/fixture/${name}`,
        source: vendored ? null : "registry+https://github.com/rust-lang/crates.io-index",
      };
      write(pkg.manifest_path, "[package]\n");
      for (const [file, text] of Object.entries(files)) write(join(directory, file), text);
      metadata.packages.push(pkg);
      metadata.resolve.nodes.push({ id: pkg.id, deps: [] });
      if (connected) metadata.resolve.nodes[0].deps.push({ pkg: pkg.id });
      return { pkg, directory };
    },
    node(name, version, files = { LICENSE: mit }, { location = `node_modules/${name}`, ...entry } = {}) {
      const directory = join(appDirectory, location);
      write(join(directory, "package.json"), JSON.stringify({ name, version, license: "MIT", repository: { url: `git+https://github.com/fixture/${name}.git` } }));
      for (const [file, text] of Object.entries(files)) write(join(directory, file), text);
      lock.packages[location] = { version, dev: true, ...entry };
      saveLock();
      return directory;
    },
  };
}

describe("release notice collection", () => {
  it("collects only reachable Rust dependencies, vendored source, installed frontend tools and Python notices", () => {
    const test = fixture();
    test.rust("serde", "1.0.0", { "LICENSE-MIT": mit, NOTICE: "Additional attribution", AUTHORS: "Fixture authors" });
    test.rust("handy-keys", "0.3.4", undefined, { vendored: true });
    test.rust("unreachable-platform", "9.0.0", {}, { connected: false });
    test.node("@scope/tool", "2.0.0");
    write(join(test.root, ".env"), "DO_NOT_DISTRIBUTE_SECRET");
    write(join(test.root, "assets-private", "AUTHORS"), "DO_NOT_DISTRIBUTE_PRIVATE_ART");

    const result = collectReleaseNotices(test.options);

    expect(result.gaps).toEqual([]);
    expect(result.records.map((record) => `${record.ecosystem} ${record.name}`)).toEqual([
      "Frontend @scope/tool", "Rust handy-keys", "Rust serde",
    ]);
    expect(result.text).toContain("Source: https://crates.io/api/v1/crates/serde/1.0.0/download");
    expect(result.text).toContain("Source: app/src-tauri/vendor/handy-keys");
    expect(result.text).toContain("Repository: https://github.com/fixture/@scope/tool.git");
    expect(result.text).toContain("Additional attribution");
    expect(result.text).toContain("Fixture authors");
    expect(result.text).toContain(mit);
    expect(result.text).toMatch(/Python core dependencies[\s\S]*Python-package 1\.2\.3\nSPDX: MIT\nPython license text\n$/);
    for (const excluded of [test.root, "unreachable-platform", "Rust: app", "DO_NOT_DISTRIBUTE", ".env", "assets-private", "\r"]) {
      expect(result.text).not.toContain(excluded);
    }
  });

  it("is byte-stable across input ordering, line endings and duplicate frontend locations", () => {
    const test = fixture();
    const { directory } = test.rust("zeta", "2.0.0", { LICENSE: mit.replace(/\n/g, "\r\n"), "LICENSE-MIT": mit });
    test.rust("alpha", "1.0.0");
    test.node("tool", "3.0.0");
    test.node("tool", "3.0.0", undefined, { location: "node_modules/parent/node_modules/tool" });
    const first = collectReleaseNotices(test.options);
    test.metadata.packages.reverse();
    test.metadata.resolve.nodes.reverse();
    test.lock.packages = Object.fromEntries(Object.entries(test.lock.packages).reverse());
    test.saveLock();
    write(join(directory, "LICENSE"), mit);

    const second = collectReleaseNotices(test.options);

    expect(second.text).toBe(first.text);
    expect(second.records).toHaveLength(3);
    expect(second.records.find((record) => record.name === "zeta").texts).toEqual([mit]);
  });

  it("searches declared license files, license subdirectories and complete README license sections", () => {
    const test = fixture();
    test.rust("declared", "1.0.0", { "terms.txt": mit }, { licenseFile: "terms.txt" });
    test.rust("nested", "1.0.0", { "licenses/mit/full.txt": mit, "licenses/NOTICE.txt": "Preserve nested notice" });
    test.rust("readme", "1.0.0", { "README.md": `# Package\nPrivate build notes\n\n## License\n\n${mit}\n\n## Development\nUnrelated instructions\n` });

    const result = collectReleaseNotices(test.options);

    expect(result.gaps).toEqual([]);
    expect(result.text).toContain("Preserve nested notice");
    expect(result.text).not.toContain("Private build notes");
    expect(result.text).not.toContain("Unrelated instructions");
  });

  it("refuses missing license text even when SPDX, AUTHORS, NOTICE or README pointers are present", () => {
    const test = fixture();
    test.rust("missing", "1.0.0", { AUTHORS: "A Person", NOTICE: "An attribution", "README.md": "## License\nMIT. See the upstream LICENSE file.\n" });
    const result = collectDependencyNotices(test.options);

    expect(result.gaps).toEqual([expect.stringMatching(/Rust missing@1\.0\.0: no license text .*README license sections/)]);
    expect(() => renderDependencyNotices(result)).toThrow("License collection is incomplete");
    expect(() => collectReleaseNotices(test.options)).toThrow("missing@1.0.0");
  });

  it("rejects a declared license_file escaping the dependency and conflicting installed versions", () => {
    const test = fixture();
    const { directory } = test.rust("escape", "1.0.0", {}, { licenseFile: "../outside-LICENSE" });
    write(join(directory, "..", "outside-LICENSE"), mit);
    test.node("mismatch", "1.0.0");
    test.lock.packages["node_modules/mismatch"].version = "2.0.0";
    test.saveLock();

    const result = collectDependencyNotices(test.options);

    expect(result.gaps).toContain("Rust escape@1.0.0: declared license_file is missing, outside the package, or not UTF-8 text");
    expect(result.gaps).toContain("Frontend mismatch@2.0.0: installed package does not match package-lock.json");
    expect(result.records.find((record) => record.name === "escape").texts).toEqual([]);
  });

  it("skips absent optional packages for other platforms but requires locked Windows x64 packages", () => {
    const test = fixture();
    test.lock.packages["node_modules/other-os"] = { version: "1", optional: true, os: ["!win32"] };
    test.lock.packages["node_modules/other-cpu"] = { version: "1", optional: true, os: ["win32"], cpu: ["arm64"] };
    test.lock.packages["node_modules/windows-optional"] = { version: "1", optional: true, os: ["win32"], cpu: ["x64"] };
    test.lock.packages["node_modules/required"] = { version: "1" };
    test.saveLock();

    expect(collectDependencyNotices(test.options).gaps).toEqual([
      "Frontend required@1: locked Windows x64 package is not installed; run npm ci",
      "Frontend windows-optional@1: locked Windows x64 package is not installed; run npm ci",
    ]);
  });

  it("preserves bundled notices and reuses native wrapper licenses only with an exact matching upstream identity", () => {
    const test = fixture();
    const parent = test.node("wrapper", "1.0.0", { LICENSE: mit, "THIRD-PARTY-LICENSE": "Bundled dependency attribution" });
    const native = test.node("@scope/native", "1.0.0", {});
    const parentManifest = { name: "wrapper", version: "1.0.0", license: "MIT", repository: "fixture/native-suite", optionalDependencies: { "@scope/native": "1.0.0" } };
    write(join(parent, "package.json"), JSON.stringify(parentManifest));
    write(join(native, "package.json"), JSON.stringify({ name: "@scope/native", version: "1.0.0", license: "MIT", repository: "https://github.com/fixture/native-suite" }));

    const result = collectReleaseNotices(test.options);

    expect(result.records.find((record) => record.name === "@scope/native").texts).toEqual(["Bundled dependency attribution", mit].sort());
    parentManifest.optionalDependencies["@scope/native"] = "^1.0.0";
    write(join(parent, "package.json"), JSON.stringify(parentManifest));
    expect(() => collectReleaseNotices(test.options)).toThrow("Frontend @scope/native@1.0.0: no license text");
  });

  it("extracts full setext README licenses while ignoring binary files and attribution-only directories", () => {
    const test = fixture();
    const { directory } = test.rust("text-check", "1.0.0", {
      "LICENSE.png": Buffer.from([137, 80, 78, 71, 0, 1]),
      "licenses/NOTICE": "Attribution without a grant",
      "README.md": `License\n-------\n${mit}\n\nDevelopment\n-----------\nDo not distribute this section`,
    });
    const result = collectLicenseTexts(directory);
    expect(result.hasLicense).toBe(true);
    expect(result.texts).toContain(mit);
    expect(result.texts.join("\n")).not.toContain("Do not distribute");
  });

  it("requires nonempty Python notices and rejects absolute build-machine paths", () => {
    const test = fixture();
    rmSync(test.pythonNotices);
    expect(() => collectReleaseNotices(test.options)).toThrow("Python THIRD_PARTY_NOTICES.txt is required");
    write(test.pythonNotices, "\n\r\n");
    expect(() => collectReleaseNotices(test.options)).toThrow("must contain UTF-8 notice text");
    write(test.pythonNotices, "License source: C:\\Users\\builder\\.venv\\LICENSE");
    expect(() => collectReleaseNotices(test.options)).toThrow("absolute local path");
  });
});

describe("version-pinned upstream license supplements", () => {
  function supplementalFixture() {
    const test = fixture();
    const { pkg, directory } = test.rust("upstream", "1.0.0", {});
    const gitCommit = "a".repeat(40);
    write(join(directory, ".cargo_vcs_info.json"), JSON.stringify({ git: { sha1: gitCommit } }));
    const supplementalDirectory = join(test.appDirectory, "scripts", "release-licenses");
    const entry = {
      name: pkg.name, version: pkg.version, license: pkg.license, repository: pkg.repository, gitCommit,
      files: [{ file: "upstream-LICENSE.txt", sourceUrl: `https://raw.githubusercontent.com/fixture/upstream/${gitCommit}/LICENSE`, sha256: createHash("sha256").update(mit).digest("hex") }],
    };
    const save = () => write(join(supplementalDirectory, "manifest.json"), JSON.stringify({ schemaVersion: 1, packages: [entry] }));
    write(join(supplementalDirectory, "upstream-LICENSE.txt"), mit);
    save();
    return { ...test, directory, supplementalDirectory, entry, save };
  }

  it("uses complete upstream copyright text only for the matching version, license, repository and published commit", () => {
    const test = supplementalFixture();
    expect(collectReleaseNotices(test.options).text).toContain(mit);
    test.entry.version = "2.0.0";
    test.save();
    expect(() => collectReleaseNotices(test.options)).toThrow("no license text");
    test.entry.version = "1.0.0";
    test.entry.gitCommit = "b".repeat(40);
    test.save();
    expect(() => collectReleaseNotices(test.options)).toThrow("does not match the exact published crate");
  });

  it("rejects modified license bytes and supplemental paths outside the manifest directory", () => {
    const test = supplementalFixture();
    write(join(test.supplementalDirectory, "upstream-LICENSE.txt"), `${mit}\nChanged copyright`);
    expect(() => collectReleaseNotices(test.options)).toThrow("SHA-256 mismatch");
    test.entry.files[0].file = "../outside.txt";
    test.save();
    expect(() => collectReleaseNotices(test.options)).toThrow("file or source URL is invalid");
  });

  it("checks in exact-version provenance and matching hashes for every real upstream supplement", () => {
    const directory = join(scripts, "release-licenses");
    const manifest = JSON.parse(readFileSync(join(directory, "manifest.json"), "utf8"));
    expect(manifest.packages.map((entry) => `${entry.name}@${entry.version}`)).toEqual([
      "alloc-stdlib@0.3.0", "selectors@0.38.0", "tauri-plugin@2.7.2", "webview2-com@0.39.1", "webview2-com-macros@0.8.1", "webview2-com-sys@0.39.1",
    ]);
    for (const entry of manifest.packages) {
      expect(entry.gitCommit).toMatch(/^[a-f0-9]{40}$/);
      for (const file of entry.files) {
        expect(createHash("sha256").update(readFileSync(join(directory, file.file))).digest("hex")).toBe(file.sha256);
        expect(file.sourceUrl).toMatch(/^https:\/\//);
        if (entry.name !== "selectors") expect(file.sourceUrl).toContain(entry.gitCommit);
      }
    }
    expect(manifest.packages.find((entry) => entry.name === "selectors").evidenceUrl).toContain("572ecba2d1600e7c3d490586692a209faf703baa/selectors/lib.rs");
  });
});

describe("notice collector CLI", () => {
  it("shows invocation help without running Cargo or requiring the Python bundle", () => {
    const result = spawnSync(process.execPath, [join(scripts, "collect-release-notices.mjs"), "--help"], { encoding: "utf8" });
    expect(result.status, result.stderr).toBe(0);
    expect(result.stdout).toContain("--python-notices FILE");
  });

  it("fails on invalid arguments before creating output", () => {
    const test = fixture();
    const output = join(test.root, "must-not-exist.txt");
    const result = spawnSync(process.execPath, [join(scripts, "collect-release-notices.mjs"), "--output", output, "--unknown"], { encoding: "utf8" });
    expect(result.status).toBe(1);
    expect(result.stderr).toContain("Invalid argument: --unknown");
    expect(existsSync(output)).toBe(false);
  });
});
