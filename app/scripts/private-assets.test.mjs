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

function runScript(app, name, args = [], privateDir) {
  const env = { ...process.env };
  delete env.RAPHAEL_PRIVATE_DIR;
  if (privateDir) env.RAPHAEL_PRIVATE_DIR = privateDir;
  const result = spawnSync(process.execPath, [join(app, "scripts", name), ...args], {
    cwd: join(app, ".."), env, encoding: "utf8",
  });
  expect(result.error).toBeUndefined();
  expect(result.status, result.stderr).toBe(0);
  return result.stdout;
}

function installIconCli(app) {
  const cli = join(app, "node_modules", "@tauri-apps", "cli");
  mkdirSync(cli, { recursive: true });
  writeFileSync(join(cli, "package.json"), JSON.stringify({ name: "@tauri-apps/cli", type: "module" }));
  writeFileSync(join(cli, "tauri.js"), `
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
} else {
  process.exitCode = 1;
}
`);
}

describe("private frontend assets", () => {
  it("copies both assets from the original repository when invoked in a worktree", () => {
    const { app, privateDir } = fixture({ worktree: true });
    writeFileSync(join(privateDir, "glyph-ring.png"), "glyph");
    writeFileSync(join(privateDir, "app-icon.png"), "portrait");

    runScript(app, "copy-private.mjs");

    expect(existsSync(join(app, "public/private/glyph-ring.png"))).toBe(true);
    expect(existsSync(join(app, "public/private/app-icon.png"))).toBe(true);
    expect(readFileSync(join(app, "public/private/app-icon.png"), "utf8")).toBe("portrait");
  });

  it("uses an explicit private directory and removes copies when their sources disappear", () => {
    const { app, root } = fixture();
    const privateDir = join(root, "personal assets");
    mkdirSync(privateDir);
    for (const name of ["glyph-ring.png", "app-icon.png"]) writeFileSync(join(privateDir, name), name);
    runScript(app, "copy-private.mjs", [], privateDir);
    expect(existsSync(join(app, "public/private/app-icon.png"))).toBe(true);

    for (const name of ["glyph-ring.png", "app-icon.png"]) rmSync(join(privateDir, name));
    runScript(app, "copy-private.mjs", [], privateDir);

    expect(existsSync(join(app, "public/private/glyph-ring.png"))).toBe(false);
    expect(existsSync(join(app, "public/private/app-icon.png"))).toBe(false);
  });

  it("keeps optional asset copy failures from failing the frontend build", () => {
    const { app, privateDir } = fixture();
    writeFileSync(join(privateDir, "glyph-ring.png"), "glyph");
    writeFileSync(join(privateDir, "app-icon.png"), "portrait");
    mkdirSync(join(app, "public/private/app-icon.png"), { recursive: true });

    runScript(app, "copy-private.mjs");

    expect(readFileSync(join(app, "public/private/glyph-ring.png"), "utf8")).toBe("glyph");
  });
});

describe("private desktop icon cache", () => {
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
    runScript(app, "tauri.mjs", ["build"], privateDir);
    expect(existsSync(join(app, "src-tauri/icons-private/icon.ico"))).toBe(true);
    rmSync(source);

    runScript(app, "tauri.mjs", ["build"], privateDir);

    expect(existsSync(join(app, "src-tauri/icons-private"))).toBe(false);
    expect(existsSync(join(app, "src-tauri/tauri.private.conf.json"))).toBe(false);
    expect(JSON.parse(readFileSync(join(app, "last-build-args.json"), "utf8"))).toEqual(["build"]);
  });
});
