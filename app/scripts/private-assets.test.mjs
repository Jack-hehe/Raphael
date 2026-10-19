import { spawnSync } from "node:child_process";
import {
  copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync,
  statSync, utimesSync, writeFileSync,
} from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, describe, expect, it } from "vitest";

const scripts = dirname(fileURLToPath(import.meta.url));
const fixtureDirectory = join(scripts, "..", "node_modules", ".cache", "private-assets-tests");
const temporaryRoots = [];
const releaseConfig = join("src-tauri", "tauri.release.conf.json");

afterEach(() => {
  for (const root of temporaryRoots.splice(0)) {
    if (relative(fixtureDirectory, root).startsWith("..")) throw new Error("Fixture escaped its cache directory");
    rmSync(root, { recursive: true, force: true });
  }
});

function fixture({ worktree = false } = {}) {
  mkdirSync(fixtureDirectory, { recursive: true });
  const root = mkdtempSync(join(fixtureDirectory, "case-"));
  temporaryRoots.push(root);
  const repository = join(root, "repository");
  const checkout = worktree ? join(root, "checkout") : repository;
  const app = join(checkout, "app");
  const privateDir = join(repository, "assets-private");
  mkdirSync(join(app, "scripts"), { recursive: true });
  mkdirSync(privateDir, { recursive: true });
  for (const file of readdirSync(scripts).filter((name) => name.endsWith(".mjs") && !name.endsWith(".test.mjs"))) {
    copyFileSync(join(scripts, file), join(app, "scripts", file));
  }
  if (worktree) {
    const gitDir = join(repository, ".git", "worktrees", "checkout");
    mkdirSync(gitDir, { recursive: true });
    writeFileSync(join(checkout, ".git"), `gitdir: ${gitDir}\n`);
    writeFileSync(join(gitDir, "commondir"), "../..\n");
  }
  return { root, app, privateDir };
}

function spawnScript(app, name, args, privateDir, env) {
  // The suite may itself run on CI, where every build is public: start from a development environment.
  const base = { ...process.env };
  for (const variable of ["CI", "RAPHAEL_PUBLIC_BUILD", "RAPHAEL_PRIVATE_DIR", "TAURI_CONFIG"]) delete base[variable];
  if (privateDir) base.RAPHAEL_PRIVATE_DIR = privateDir;
  const result = spawnSync(process.execPath, [join(app, "scripts", name), ...args], {
    cwd: join(app, ".."), env: { ...base, ...env }, encoding: "utf8",
  });
  expect(result.error).toBeUndefined();
  return result;
}

function runScript(app, name, args = [], privateDir, env = {}) {
  const result = spawnScript(app, name, args, privateDir, env);
  expect(result.status, result.stderr).toBe(0);
  return result.stdout;
}

function runFailing(app, name, args = [], privateDir, env = {}) {
  const result = spawnScript(app, name, args, privateDir, env);
  expect(result.status).toBe(1);
  return result.stderr;
}

// Stands in for the Tauri CLI: `icon` writes the icon set, `build` records what it was given and runs
// beforeBuildCommand's frontend step (copy-private.mjs, which npm's prebuild hook runs) with its own environment.
function installIconCli(app) {
  const cli = join(app, "node_modules", "@tauri-apps", "cli");
  mkdirSync(cli, { recursive: true });
  writeFileSync(join(cli, "package.json"), JSON.stringify({ name: "@tauri-apps/cli", type: "module" }));
  writeFileSync(join(cli, "tauri.js"), `
import { spawnSync } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { isAbsolute, join, resolve } from "node:path";
const args = process.argv.slice(2);
if (args[0] === "icon") {
  const outputArg = args[args.indexOf("-o") + 1];
  const output = isAbsolute(outputArg) ? outputArg : resolve(outputArg);
  mkdirSync(output, { recursive: true });
  const content = "generated:" + readFileSync(args[1], "utf8");
  for (const name of ["32x32.png", "128x128.png", "128x128@2x.png", "icon.icns", "icon.ico"]) {
    writeFileSync(join(output, name), content);
  }
} else if (args[0] === "build") {
  writeFileSync("last-build-args.json", JSON.stringify(args));
  writeFileSync("last-build-env.json", JSON.stringify({
    publicBuild: process.env.RAPHAEL_PUBLIC_BUILD ?? null, privateDir: process.env.RAPHAEL_PRIVATE_DIR ?? null,
  }));
  const frontend = spawnSync(process.execPath, [join("scripts", "copy-private.mjs")], { stdio: "inherit" });
  process.exitCode = frontend.status;
} else {
  process.exitCode = 1;
}
`);
}

describe("private frontend assets", () => {
  it("copies only the artwork the frontend loads, from the original checkout", () => {
    const { app, privateDir } = fixture({ worktree: true });
    writeFileSync(join(privateDir, "glyph-ring.png"), "glyph");
    writeFileSync(join(privateDir, "app-icon.png"), "desktop icon");
    writeFileSync(join(privateDir, "in-app-icon.png"), "watercolour in-app icon");
    writeFileSync(join(privateDir, "avatar.png"), "watercolour character");

    runScript(app, "copy-private.mjs");

    expect(readFileSync(join(app, "public/private/glyph-ring.png"), "utf8")).toBe("glyph");
    expect(readFileSync(join(app, "public/private/in-app-icon.png"), "utf8")).toBe("watercolour in-app icon");
    // The desktop icon is read from the private directory by tauri.mjs; no view loads it or the portrait.
    expect(readdirSync(join(app, "public/private")).sort()).toEqual(["glyph-ring.png", "in-app-icon.png"]);
  });

  it("uses an explicit private directory and removes copies when their sources disappear", () => {
    const { app, root } = fixture();
    const privateDir = join(root, "personal assets");
    mkdirSync(privateDir);
    for (const name of ["glyph-ring.png", "in-app-icon.png"]) writeFileSync(join(privateDir, name), name);
    runScript(app, "copy-private.mjs", [], privateDir);
    expect(existsSync(join(app, "public/private/glyph-ring.png"))).toBe(true);
    expect(existsSync(join(app, "public/private/in-app-icon.png"))).toBe(true);

    for (const name of ["glyph-ring.png", "in-app-icon.png"]) rmSync(join(privateDir, name));
    runScript(app, "copy-private.mjs", [], privateDir);

    expect(existsSync(join(app, "public/private/glyph-ring.png"))).toBe(false);
    expect(existsSync(join(app, "public/private/in-app-icon.png"))).toBe(false);
  });

  it("keeps optional asset copy failures from failing the frontend build", () => {
    const { app, privateDir } = fixture();
    writeFileSync(join(privateDir, "glyph-ring.png"), "glyph");
    writeFileSync(join(privateDir, "in-app-icon.png"), "portrait");
    mkdirSync(join(app, "public/private/in-app-icon.png"), { recursive: true });

    runScript(app, "copy-private.mjs");

    expect(readFileSync(join(app, "public/private/glyph-ring.png"), "utf8")).toBe("glyph");
  });
});

describe("private desktop icon cache", () => {
  it("keeps cached desktop icons unchanged when only the in-app artwork changes", () => {
    const { app, privateDir } = fixture({ worktree: true });
    installIconCli(app);
    writeFileSync(join(privateDir, "app-icon.png"), "desktop icon");
    const inAppSource = join(privateDir, "in-app-icon.png");
    writeFileSync(inAppSource, "first in-app icon");
    runScript(app, "tauri.mjs", ["build"]);
    const cachePath = join(app, "src-tauri/icons-private/.cache.json");
    const cache = readFileSync(cachePath, "utf8");

    writeFileSync(inAppSource, "new in-app icon");
    runScript(app, "copy-private.mjs");

    expect(runScript(app, "tauri.mjs", ["build"])).toContain("using cached private icons");
    expect(readFileSync(join(app, "public/private/in-app-icon.png"), "utf8")).toBe("new in-app icon");
    expect(readFileSync(join(app, "src-tauri/icons-private/icon.ico"), "utf8")).toBe("generated:desktop icon");
    expect(readFileSync(cachePath, "utf8")).toBe(cache);
  });

  it("invalidates by file content even when a replacement preserves size and mtime", () => {
    const { app, privateDir } = fixture({ worktree: true });
    installIconCli(app);
    const source = join(privateDir, "app-icon.png");
    const icon = join(app, "src-tauri/icons-private/icon.ico");
    writeFileSync(source, "first");
    const originalTime = statSync(source).mtime;

    runScript(app, "tauri.mjs", ["build"]);
    expect(existsSync(icon)).toBe(true);
    expect(readFileSync(icon, "utf8")).toBe("generated:first");
    expect(runScript(app, "tauri.mjs", ["build"])).toContain("using cached private icons");

    writeFileSync(source, "other");
    utimesSync(source, originalTime, originalTime);
    expect(statSync(source).mtime.getTime()).toBe(originalTime.getTime());
    expect(runScript(app, "tauri.mjs", ["build"])).toContain("generated desktop icons");
    expect(readFileSync(icon, "utf8")).toBe("generated:other");

    rmSync(join(app, "src-tauri/icons-private/128x128.png"));
    expect(runScript(app, "tauri.mjs", ["build"])).toContain("generated desktop icons");
    expect(existsSync(join(app, "src-tauri/icons-private/128x128.png"))).toBe(true);
  });

  it("removes every generated private icon and stops injecting config when the source is absent", () => {
    const { app, privateDir } = fixture();
    installIconCli(app);
    const source = join(privateDir, "app-icon.png");
    writeFileSync(source, "portrait");
    writeFileSync(join(privateDir, "in-app-icon.png"), "watercolour in-app icon");
    runScript(app, "tauri.mjs", ["build"], privateDir);
    expect(existsSync(join(app, "src-tauri/icons-private/icon.ico"))).toBe(true);
    rmSync(source);

    runScript(app, "tauri.mjs", ["build"], privateDir);

    expect(existsSync(join(app, "src-tauri/icons-private"))).toBe(false);
    expect(existsSync(join(app, "src-tauri/tauri.private.conf.json"))).toBe(false);
    expect(JSON.parse(readFileSync(join(app, "last-build-args.json"), "utf8"))).toEqual(["build"]);
  });
});

describe("public builds", () => {
  // A checkout whose original directory holds every private source, like the maintainer's.
  function checkoutWithPrivateArtwork() {
    const test = fixture({ worktree: true });
    installIconCli(test.app);
    for (const name of ["app-icon.png", "in-app-icon.png", "glyph-ring.png", "avatar.png"]) {
      writeFileSync(join(test.privateDir, name), name);
    }
    return test;
  }
  const generated = (app) => ["public/private", "src-tauri/icons-private", "src-tauri/tauri.private.conf.json"]
    .filter((path) => existsSync(join(app, path)));
  const releaseArgs = ["build", "--config", releaseConfig, "--bundles", "nsis", "--ci", "--", "--locked"];

  it("ignores private artwork for the release config, even from a linked worktree", () => {
    const { app } = checkoutWithPrivateArtwork();

    runScript(app, "tauri.mjs", releaseArgs);

    expect(generated(app)).toEqual([]);
    expect(JSON.parse(readFileSync(join(app, "last-build-args.json"), "utf8"))).toEqual(releaseArgs);
    // The frontend build the CLI starts inherits the decision and copies nothing.
    expect(JSON.parse(readFileSync(join(app, "last-build-env.json"), "utf8")).publicBuild).toBe("1");
  });

  it("ignores private artwork when CI is set, with or without the release config", () => {
    const { app } = checkoutWithPrivateArtwork();

    runScript(app, "tauri.mjs", ["build"], undefined, { CI: "true" });
    runScript(app, "tauri.mjs", ["build", "--debug", "--no-bundle"], undefined, { CI: "1" });

    expect(generated(app)).toEqual([]);
    expect(JSON.parse(readFileSync(join(app, "last-build-args.json"), "utf8"))).toEqual(["build", "--debug", "--no-bundle"]);
  });

  it("does not let RAPHAEL_PRIVATE_DIR bring private artwork into a public build", () => {
    const { app, root } = checkoutWithPrivateArtwork();
    const elsewhere = join(root, "personal assets");
    mkdirSync(elsewhere);
    writeFileSync(join(elsewhere, "app-icon.png"), "portrait");
    writeFileSync(join(elsewhere, "glyph-ring.png"), "glyph");

    runScript(app, "tauri.mjs", releaseArgs, elsewhere);
    runScript(app, "tauri.mjs", ["build"], elsewhere, { CI: "true" });

    expect(generated(app)).toEqual([]);
  });

  it("treats CI=false and an empty CI as an ordinary development build", () => {
    const { app } = checkoutWithPrivateArtwork();

    runScript(app, "tauri.mjs", ["build"], undefined, { CI: "false" });
    expect(existsSync(join(app, "src-tauri/icons-private/icon.ico"))).toBe(true);
    runScript(app, "tauri.mjs", ["build"], undefined, { CI: "" });
    expect(readFileSync(join(app, "public/private/in-app-icon.png"), "utf8")).toBe("in-app-icon.png");
  });

  it.each([
    ["public/private", (app) => mkdirSync(join(app, "public/private"), { recursive: true })],
    ["dist/private", (app) => mkdirSync(join(app, "dist/private"), { recursive: true })],
    ["src-tauri/icons-private", (app) => mkdirSync(join(app, "src-tauri/icons-private"), { recursive: true })],
    ["src-tauri/tauri.private.conf.json", (app) => {
      mkdirSync(join(app, "src-tauri"), { recursive: true });
      writeFileSync(join(app, "src-tauri/tauri.private.conf.json"), "{}");
    }],
  ])("fails a release build when a development build left %s behind", (leftover, create) => {
    const { app } = checkoutWithPrivateArtwork();
    create(app);

    const stderr = runFailing(app, "tauri.mjs", releaseArgs);

    expect(stderr).toContain("Public builds refuse private artwork");
    expect(stderr).toContain(leftover);
    expect(existsSync(join(app, "last-build-args.json"))).toBe(false);
  });

  it("names every leftover at once and fails a CI frontend build the same way", () => {
    const { app } = checkoutWithPrivateArtwork();
    mkdirSync(join(app, "public/private"), { recursive: true });
    mkdirSync(join(app, "src-tauri/icons-private"), { recursive: true });

    const stderr = runFailing(app, "tauri.mjs", ["build"], undefined, { CI: "true" });
    expect(stderr).toContain("public/private, src-tauri/icons-private");

    expect(runFailing(app, "copy-private.mjs", [], undefined, { CI: "true" })).toContain("public/private, src-tauri/icons-private");
  });

  it("copies nothing in the frontend build of a public build and succeeds without leftovers", () => {
    const { app } = checkoutWithPrivateArtwork();

    expect(runScript(app, "copy-private.mjs", [], undefined, { CI: "true" })).toContain("public build");
    expect(runScript(app, "copy-private.mjs", [], undefined, { RAPHAEL_PUBLIC_BUILD: "1" })).toContain("public build");

    expect(existsSync(join(app, "public/private"))).toBe(false);
  });

  it("refuses to be pointed at private files by its arguments or TAURI_CONFIG", () => {
    const { app } = checkoutWithPrivateArtwork();
    const privateConfig = join("src-tauri", "tauri.private.conf.json");

    expect(runFailing(app, "tauri.mjs", ["build", "--config", releaseConfig, "--config", privateConfig])).toContain("private artwork");
    const env = { TAURI_CONFIG: JSON.stringify({ bundle: { icon: ["icons-private/icon.ico"] } }) };
    expect(runFailing(app, "tauri.mjs", ["build", "--config", releaseConfig], undefined, env)).toContain("private artwork");
    expect(existsSync(join(app, "last-build-args.json"))).toBe(false);
  });

  it("leaves other commands alone on CI", () => {
    const { app } = checkoutWithPrivateArtwork();
    mkdirSync(join(app, "public/private"), { recursive: true });

    // `info` neither builds nor embeds anything, so a leftover does not matter to it.
    const result = spawnScript(app, "tauri.mjs", ["info"], undefined, { CI: "true" });

    expect(result.stderr).not.toContain("Public builds refuse");
  });
});
