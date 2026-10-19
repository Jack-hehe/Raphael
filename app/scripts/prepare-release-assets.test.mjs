import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";
import { buildInfo, checksums, collectTools, prepareReleaseAssets } from "./prepare-release-assets.mjs";

const scripts = dirname(fileURLToPath(import.meta.url));
const fixtureDirectory = join(scripts, "..", "node_modules", ".cache", "release-assets-tests");
const temporaryRoots = [];
const env = {
  GITHUB_SHA: "0123456789abcdef0123456789abcdef01234567", GITHUB_REF: "refs/heads/fix/release-licensing",
  GITHUB_REPOSITORY: "owner/Raphael", GITHUB_WORKFLOW: "Build Windows release", GITHUB_RUN_ID: "4242",
  GITHUB_RUN_ATTEMPT: "2", GITHUB_SERVER_URL: "https://github.com", ImageOS: "win25", ImageVersion: "20260901.1",
};
// Reading the tool versions starts npm, rustc, cargo and uv, which a busy runner can take seconds to do.
const slowTools = 30_000;
const tools = { node: "v22.17.0", npm: "10.9.2", "tauri cli": "2.12.3", nsis: "v3.13" };

afterEach(() => {
  for (const root of temporaryRoots.splice(0)) {
    if (relative(fixtureDirectory, root).startsWith("..")) throw new Error("Fixture escaped its cache directory");
    rmSync(root, { recursive: true, force: true });
  }
});

function inputs() {
  mkdirSync(fixtureDirectory, { recursive: true });
  const root = mkdtempSync(join(fixtureDirectory, "case-"));
  temporaryRoots.push(root);
  writeFileSync(join(root, "app_0.1.0_x64-setup.exe"), Buffer.from([0x4d, 0x5a, 0x00, 0x0d, 0x0a, 0xff]));
  writeFileSync(join(root, "notices.txt"), "Notices\r\nwith CRLF\r\n");
  return { root, installer: join(root, "app_0.1.0_x64-setup.exe"), notices: join(root, "notices.txt"), output: join(root, "release-assets") };
}

const sha256 = (path) => createHash("sha256").update(readFileSync(path)).digest("hex");

describe("release assets", () => {
  it("names the installer after the version and writes notices, build info and checksums", () => {
    const { installer, notices, output } = inputs();

    const names = prepareReleaseAssets({ installer, notices, version: "0.1.0", output, env, tools });

    expect(names).toEqual(["BUILD-INFO.txt", "Raphael-0.1.0-windows-x64-setup.exe", "SHA256SUMS.txt", "THIRD_PARTY_NOTICES.txt"]);
    expect(readFileSync(join(output, "Raphael-0.1.0-windows-x64-setup.exe"))).toEqual(readFileSync(installer));
    expect(readFileSync(join(output, "THIRD_PARTY_NOTICES.txt"), "utf8")).toBe("Notices\r\nwith CRLF\r\n");
  });

  it("writes SHA256SUMS.txt for sha256sum -c: LF line endings, two spaces, every file but itself", () => {
    const { installer, notices, output } = inputs();
    prepareReleaseAssets({ installer, notices, version: "0.1.0", output, env, tools });

    const bytes = readFileSync(join(output, "SHA256SUMS.txt"));
    const text = bytes.toString("utf8");

    // With CRLF, `sha256sum -c` looks for "file\r" and fails.
    expect(bytes.includes(0x0d)).toBe(false);
    expect(text.endsWith("\n")).toBe(true);
    const lines = text.trimEnd().split("\n");
    expect(lines.map((line) => line.slice(66))).toEqual(["BUILD-INFO.txt", "Raphael-0.1.0-windows-x64-setup.exe", "THIRD_PARTY_NOTICES.txt"]);
    for (const line of lines) {
      expect(line).toMatch(/^[0-9a-f]{64} {2}\S+$/);
      expect(line.slice(0, 64)).toBe(sha256(join(output, line.slice(66))));
    }
  });

  it("covers BUILD-INFO.txt, which says which commit, ref and run built the files and with what", () => {
    const { installer, notices, output } = inputs();
    prepareReleaseAssets({ installer, notices, version: "0.1.0", output, env, tools });

    const info = readFileSync(join(output, "BUILD-INFO.txt"), "utf8");

    expect(info).toBe(`Raphael Windows x64 release build

version: 0.1.0
commit: 0123456789abcdef0123456789abcdef01234567
ref: refs/heads/fix/release-licensing
repository: owner/Raphael
workflow: Build Windows release
run id: 4242
run attempt: 2
run url: https://github.com/owner/Raphael/actions/runs/4242/attempts/2
runner image: win25 20260901.1

tools:
  node: v22.17.0
  npm: 10.9.2
  tauri cli: 2.12.3
  nsis: v3.13
`);
    expect(checksums(output)).toContain(`${sha256(join(output, "BUILD-INFO.txt"))}  BUILD-INFO.txt\n`);
  });

  it("says so, instead of inventing values, when it does not run on GitHub Actions", () => {
    const info = buildInfo({ version: "0.1.0", env: {}, tools: { node: "v22.0.0" } });
    expect(info).toContain("commit: unknown");
    expect(info).toContain("run url: not a GitHub Actions run");
    expect(info).toContain("runner image: unknown");
    expect(info).not.toContain("\r");
  });

  it("does not write into an existing directory", () => {
    const { installer, notices, output } = inputs();
    mkdirSync(output);
    expect(() => prepareReleaseAssets({ installer, notices, version: "0.1.0", output, env, tools })).toThrow("already exists");
    expect(readdirSync(output)).toEqual([]);
  });

  it("collects tool versions without failing when a tool is missing", () => {
    const found = collectTools({ LOCALAPPDATA: join(fixtureDirectory, "nowhere") });
    expect(found.node).toBe(process.version);
    expect(Object.keys(found)).toEqual(["node", "npm", "rustc", "cargo", "uv", "python in the frozen core", "pyinstaller", "tauri cli", "nsis"]);
    expect(found.nsis).toBe("unavailable");
  }, slowTools);

  it("works from the command line and rejects unknown or missing arguments", () => {
    const { installer, notices, output } = inputs();
    const run = (...args) => spawnSync(process.execPath, [join(scripts, "prepare-release-assets.mjs"), ...args], { encoding: "utf8", env: { ...process.env, ...env } });

    const bad = run("--installer", installer, "--bogus", "x");
    expect(bad.status).toBe(1);
    expect(bad.stderr).toContain("Usage:");
    expect(run("--installer", installer, "--notices", notices, "--version", "0.1.0").stderr).toContain("Missing --output");
    expect(existsSync(output)).toBe(false);

    const good = run("--installer", installer, "--notices", notices, "--version", "0.1.0", "--output", output);
    expect(good.status, good.stderr).toBe(0);
    expect(good.stdout.split("\n").filter(Boolean)).toEqual(["BUILD-INFO.txt", "Raphael-0.1.0-windows-x64-setup.exe", "SHA256SUMS.txt", "THIRD_PARTY_NOTICES.txt"]);
    expect(readFileSync(join(output, "BUILD-INFO.txt"), "utf8")).toContain("commit: 0123456789abcdef0123456789abcdef01234567");
  }, slowTools);
});
