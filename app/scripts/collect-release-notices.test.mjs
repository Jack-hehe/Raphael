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
// `rustc --print cfg --target x86_64-pc-windows-msvc`, kept here so the tests need no Rust toolchain.
const windowsX64Cfg = [
  'debug_assertions', 'panic="unwind"', 'target_abi=""', 'target_arch="x86_64"', 'target_endian="little"',
  'target_env="msvc"', 'target_family="windows"', 'target_feature="sse2"', 'target_has_atomic="64"',
  'target_os="windows"', 'target_pointer_width="64"', 'target_vendor="pc"', 'windows',
];
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
  const options = { appDirectory, pythonNotices, metadata, rustTargetCfg: windowsX64Cfg };
  const saveLock = () => write(join(appDirectory, "package-lock.json"), JSON.stringify(lock));
  saveLock();
  return {
    root, ...options, lock, options, saveLock,
    rust(name, version, files = { LICENSE: mit }, { connected = true, vendored = false, licenseFile, depKinds = [{ kind: null, target: null }] } = {}) {
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
      if (connected) metadata.resolve.nodes[0].deps.push({ pkg: pkg.id, dep_kinds: depKinds });
      return { pkg, directory };
    },
    node(name, version, files = { LICENSE: mit }, { location = `node_modules/${name}`, ...entry } = {}) {
      const directory = join(appDirectory, location);
      write(join(directory, "package.json"), JSON.stringify({ name, version, license: "MIT", repository: { url: `git+https://github.com/fixture/${name}.git` } }));
      for (const [file, text] of Object.entries(files)) write(join(directory, file), text);
      lock.packages[location] = { version, dev: false, ...entry };
      saveLock();
      return directory;
    },
  };
}

describe("release notice collection", () => {
  it("collects reachable Rust dependencies, vendored source, shipped frontend packages and Python notices", () => {
    const test = fixture();
    test.rust("serde", "1.0.0", { "LICENSE-MIT": mit, NOTICE: "Additional attribution", AUTHORS: "Fixture authors" });
    test.rust("handy-keys", "0.3.4", undefined, { vendored: true });
    test.rust("unreachable-platform", "9.0.0", {}, { connected: false });
    test.node("@scope/tool", "2.0.0");
    write(join(test.root, ".env"), "DO_NOT_DISTRIBUTE_SECRET");
    write(join(test.root, "unshipped-art", "AUTHORS"), "DO_NOT_DISTRIBUTE_UNSHIPPED_ART");

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
    for (const excluded of [test.root, "unreachable-platform", "Rust: app", "DO_NOT_DISTRIBUTE", ".env", "unshipped-art", "\r"]) {
      expect(result.text).not.toContain(excluded);
    }
  });

  it("neither requires nor lists dev-only npm packages, whatever their license files or repository URL", () => {
    const test = fixture();
    // The jsdom case that blocked a release: a test-only package with no license file and an ssh repository.
    const directory = test.node("dev-only", "1.0.0", {}, { dev: true });
    write(join(directory, "package.json"), JSON.stringify({ name: "dev-only", version: "1.0.0", repository: "ssh://git@github.com/fixture/dev-only.git" }));
    test.node("dev-optional", "1.0.0", {}, { devOptional: true });
    test.lock.packages["node_modules/absent-dev"] = { version: "1", dev: true };
    test.saveLock();

    const result = collectReleaseNotices(test.options);

    expect(result.gaps).toEqual([]);
    expect(result.records).toEqual([]);
    expect(result.text).not.toContain("dev-only");

    // The same package fails closed as soon as it is no longer a dev-only dependency.
    test.lock.packages["node_modules/dev-only"].dev = false;
    test.saveLock();
    expect(() => collectReleaseNotices(test.options)).toThrow("Frontend dev-only@1.0.0");
  });

  it("includes Vite, a dev dependency whose modulepreload polyfill is emitted into the shipped bundle", () => {
    const test = fixture();
    const vite = test.node("vite", "8.3.4", undefined, { dev: true });
    test.node("rolldown", "1.0.0", {}, { dev: true });
    expect(collectReleaseNotices(test.options).records.map((record) => record.name)).toEqual(["vite"]);

    // The allowlist does not weaken the shipped-package checks: Vite must be installed and licensed.
    rmSync(join(vite, "LICENSE"));
    expect(() => collectReleaseNotices(test.options)).toThrow("Frontend vite@8.3.4: no license text");
    rmSync(vite, { recursive: true });
    expect(() => collectReleaseNotices(test.options)).toThrow("Frontend vite@8.3.4: locked Windows x64 package is not installed");
  });

  it("follows only normal Rust edges that apply to Windows x64, judging each edge kind on its own", () => {
    const test = fixture();
    const normal = { kind: null, target: null };
    test.rust("always", "1", undefined, { depKinds: [normal] });
    test.rust("build-only", "1", {}, { depKinds: [{ kind: "build", target: null }] });
    test.rust("dev-only", "1", {}, { depKinds: [{ kind: "dev", target: null }] });
    test.rust("build-and-normal", "1", undefined, { depKinds: [{ kind: "build", target: null }, normal] });
    // cargo --filter-platform keeps an edge when ANY kind matches: here only the build edge does.
    test.rust("linux-normal-windows-build", "1", {}, { depKinds: [{ kind: null, target: 'cfg(target_os = "linux")' }, { kind: "build", target: "cfg(windows)" }] });
    test.rust("other-triple", "1", {}, { depKinds: [{ kind: null, target: "aarch64-pc-windows-msvc" }] });
    test.rust("this-triple", "1", undefined, { depKinds: [{ kind: null, target: "x86_64-pc-windows-msvc" }] });
    // A build tool's own normal dependencies are build tools too.
    test.rust("build-tool-child", "1", {}, { connected: false });
    test.metadata.resolve.nodes.find((node) => node.id === "build-only@1").deps.push({ pkg: "build-tool-child@1", dep_kinds: [normal] });

    expect(collectReleaseNotices(test.options).records.map((record) => record.name)).toEqual(["always", "build-and-normal", "this-triple"]);
  });

  it.each([
    ["cfg(windows)", true],
    ['cfg(target_os = "windows")', true],
    ['cfg(all(target_os = "windows", target_env = "msvc"))', true],
    ['cfg(all(any(target_arch = "x86_64", target_arch = "arm64ec"), target_env = "msvc", not(windows_raw_dylib)))', true],
    ['cfg(any(target_os = "macos", windows, target_os = "linux"))', true],
    ['cfg(any(not(target_family = "wasm"), all(target_os = "wasi", not(target_env = "p1"))))', true],
    ['cfg(not(all(target_arch = "wasm32", any(target_os = "unknown", target_os = "none"))))', true],
    ['cfg(any(unix, windows, target_os = "wasi"))', true],
    ['cfg( all( target_os = "windows" , target_pointer_width = "64" , ) )', true],
    ["cfg(all())", true],
    ["cfg(any())", false],
    ['cfg(target_os = "macos")', false],
    ["cfg(unix)", false],
    ['cfg(all(tokio_unstable, target_os = "linux"))', false],
    ['cfg(any(target_os = "linux", target_os = "freebsd"))', false],
    ['cfg(target_feature = "crt-static")', false],
    ['cfg(not(target_env = "msvc"))', false],
    ["i686-pc-windows-msvc", false],
  ])("evaluates the Cargo target condition %s as %s", (target, applies) => {
    const test = fixture();
    test.rust("conditional", "1", applies ? undefined : {}, { depKinds: [{ kind: null, target }] });
    expect(collectReleaseNotices(test.options).records.map((record) => record.name)).toEqual(applies ? ["conditional"] : []);
  });

  it.each([
    "cfg(target_os = windows)", 'cfg(all(target_os = "windows"', 'cfg(foo(bar))', 'cfg(not(windows, unix))',
    'cfg(target_os "windows")', "cfg()", "cfg(windows) extra", "cfg(windows",
  ])("refuses the Cargo target condition %s instead of guessing", (target) => {
    const test = fixture();
    test.rust("odd", "1", undefined, { depKinds: [{ kind: null, target }] });
    expect(() => collectReleaseNotices(test.options)).toThrow("Unsupported Cargo target condition");
  });

  it("refuses Cargo metadata without dependency kinds", () => {
    const test = fixture();
    test.rust("legacy", "1");
    delete test.metadata.resolve.nodes[0].deps[0].dep_kinds;
    expect(() => collectReleaseNotices(test.options)).toThrow("must include dependency kinds");
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
      "alloc-stdlib@0.3.0", "selectors@0.38.0", "webview2-com@0.39.1", "webview2-com-macros@0.8.1", "webview2-com-sys@0.39.1",
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

describe("pinned binary and ported components", () => {
  function componentFixture() {
    const test = fixture();
    const directory = join(test.appDirectory, "scripts", "release-licenses");
    const component = {
      name: "Static loader", version: "2.0.0", license: "BSD-3-Clause", repository: "https://example.com/loader",
      source: "https://example.com/loader/2.0.0", usage: "linked into the desktop executable by the loader crate",
      requiresCrate: { name: "loader-sys", version: "1.0.0" },
      files: [{ file: "loader-LICENSE.txt", sourceUrl: "https://example.com/loader/2.0.0/LICENSE", sha256: createHash("sha256").update(mit).digest("hex") }],
    };
    const save = () => write(join(directory, "manifest.json"), JSON.stringify({ schemaVersion: 1, packages: [], components: [component] }));
    write(join(directory, "loader-LICENSE.txt"), mit);
    save();
    return { ...test, directory, component, save };
  }

  it("lists a component with its provenance and license text while the crate that links it ships", () => {
    const test = componentFixture();
    test.rust("loader-sys", "1.0.0");

    const result = collectReleaseNotices(test.options);

    expect(result.records.map((record) => `${record.ecosystem} ${record.name} ${record.version}`)).toEqual([
      "Component Static loader 2.0.0", "Rust loader-sys 1.0.0",
    ]);
    expect(result.text).toContain("Component: Static loader 2.0.0\nSPDX: BSD-3-Clause\nRepository: https://example.com/loader\nSource: https://example.com/loader/2.0.0\nUsed for: linked into the desktop executable");
  });

  it("omits a component whose crate does not ship and refuses one pinned for another crate version", () => {
    const test = componentFixture();
    expect(collectReleaseNotices(test.options).records).toEqual([]);

    test.rust("loader-sys", "1.1.0");
    expect(() => collectReleaseNotices(test.options)).toThrow("Component Static loader@2.0.0: pinned for loader-sys 1.0.0, but the build has 1.1.0");
  });

  it("lists a component with no crate requirement unconditionally and rejects modified or unsourced texts", () => {
    const test = componentFixture();
    delete test.component.requiresCrate;
    test.save();
    expect(collectReleaseNotices(test.options).records.map((record) => record.name)).toEqual(["Static loader"]);

    write(join(test.directory, "loader-LICENSE.txt"), `${mit}\nChanged copyright`);
    expect(() => collectReleaseNotices(test.options)).toThrow("Component Static loader@2.0.0: supplemental license SHA-256 mismatch");

    write(join(test.directory, "loader-LICENSE.txt"), mit);
    test.component.source = "http://example.com/loader";
    test.save();
    expect(() => collectReleaseNotices(test.options)).toThrow("component provenance is incomplete");
  });

  it("checks in a pinned license text for every component the release binaries carry", () => {
    const directory = join(scripts, "release-licenses");
    const manifest = JSON.parse(readFileSync(join(directory, "manifest.json"), "utf8"));
    expect(manifest.components.map((entry) => `${entry.name} ${entry.version}`)).toEqual([
      "Handy 9e57a0d", "Microsoft Edge WebView2 SDK loader 1.0.3800.47", "Simple Icons 16.35.0", "Brand logos 1", "Inter 4.1",
    ]);
    for (const entry of manifest.components) {
      expect(entry.source).toMatch(/^https:\/\//);
      for (const file of entry.files) {
        expect(createHash("sha256").update(readFileSync(join(directory, file.file))).digest("hex")).toBe(file.sha256);
        expect(file.sourceUrl).toMatch(/^https:\/\//);
      }
    }
    // The ported code is pinned to the commit named in the headers of the files that contain it.
    const handy = manifest.components[0];
    expect(handy.files[0].sourceUrl).toContain("/9e57a0d7929fcc6417ea0d6e54c1b57f62635edd/");
    for (const source of ["clipboard.rs", "windows.rs"]) {
      expect(readFileSync(join(scripts, "..", "src-tauri", "src", "paste", source), "utf8")).toContain("Handy");
    }
    // webview2-com-sys ships the static loader; the pinned SDK is the one its library file comes from.
    const webview = manifest.components[1];
    expect(webview.requiresCrate).toEqual({ name: "webview2-com-sys", version: "0.39.1" });
    expect(webview.files[0].archiveSha256).toMatch(/^[a-f0-9]{64}$/);
    expect(webview.usage).toContain("89c6b872783b8f6c3cedbff618adb42082d455c615453ae10cfc753f1e8f25d8");
    // The vendored brand marks are CC0 and pinned to the commit of the release they were taken from.
    const icons = manifest.components[2];
    expect(icons.license).toBe("CC0-1.0");
    expect(icons.usage).toContain("trademarks of their owners");
    expect(icons.files[0].sourceUrl).toContain("/955f17f35634cd58de1748204059bce380d379af/");
    expect(readFileSync(join(directory, icons.files[0].file), "utf8")).toContain("CC0 1.0 Universal");
    expect(readFileSync(join(scripts, "..", "src", "main", "service-icons.ts"), "utf8")).toContain("Simple Icons 16.35.0");
  });

  it("ships the full Inter OFL notice even without a linked Rust crate", () => {
    const test = fixture();
    const result = collectReleaseNotices({ ...test.options, supplementalDirectory: join(scripts, "release-licenses") });
    const inter = result.records.find((record) => record.name === "Inter");
    expect(inter).toMatchObject({ ecosystem: "Component", version: "4.1", spdx: "OFL-1.1" });
    expect(result.text).toContain("SIL OPEN FONT LICENSE Version 1.1");
    expect(result.text).toContain(readFileSync(join(scripts, "release-licenses", "inter-4.1-LICENSE.txt"), "utf8").trim());
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
