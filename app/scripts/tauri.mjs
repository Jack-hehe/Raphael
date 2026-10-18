// Local icons and the generated config must stay in the gitignored private directories.
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import {
  copyFileSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync,
} from "node:fs";
import { createRequire } from "node:module";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { privateAssetsDirectory } from "./private-assets.mjs";

const script = fileURLToPath(import.meta.url);
const appDir = resolve(dirname(script), "..");
const configArg = join("src-tauri", "tauri.private.conf.json");
const desktopIcons = ["32x32.png", "128x128.png", "128x128@2x.png", "icon.icns", "icon.ico"];

function isPrivateCommand(args) {
  const command = args.slice(0, args.indexOf("--") < 0 ? args.length : args.indexOf("--"))
    .find((arg) => !arg.startsWith("-"));
  return command === "dev" || command === "build";
}

// A null config means the private source was absent, even if an old config once existed.
export function withPrivateConfig(args, privateConfig) {
  if (!privateConfig || !isPrivateCommand(args)) return [...args];
  const separator = args.indexOf("--");
  const index = separator < 0 ? args.length : separator;
  return [...args.slice(0, index), "--config", privateConfig, ...args.slice(index)];
}

function runTauri(args) {
  // Invoke the installed CLI directly: no shell quoting, recursive npm script, or network fetch.
  const entry = createRequire(import.meta.url).resolve("@tauri-apps/cli/tauri.js");
  const result = spawnSync(process.execPath, [entry, ...args], { cwd: appDir, stdio: "inherit" });
  if (result.error) throw result.error;
  return result.status ?? 1;
}

function writeIfChanged(path, content) {
  if (!existsSync(path) || readFileSync(path, "utf8") !== content) writeFileSync(path, content);
}

export function preparePrivateIcons() {
  const privateDir = privateAssetsDirectory(appDir);
  const source = join(privateDir, "app-icon.png");
  const output = join(appDir, "src-tauri", "icons-private");
  const config = join(appDir, configArg);
  const tray = join(output, "tray-icon.png");
  if (!existsSync(source)) {
    // A removed source must not leave private tray or Windows executable icons active.
    rmSync(config, { force: true });
    rmSync(output, { recursive: true, force: true });
    return null;
  }

  const sourceHash = createHash("sha256").update(readFileSync(source)).digest("hex");
  const cachePath = join(output, ".cache.json");
  let cache;
  try {
    cache = JSON.parse(readFileSync(cachePath, "utf8"));
  } catch {
    // A missing or interrupted cache is regenerated below.
  }
  const cached = cache?.sourceHash === sourceHash && Array.isArray(cache.files)
    && [...desktopIcons, "tray-icon.png", ...cache.files]
      .every((name) => existsSync(join(output, name)) && statSync(join(output, name)).isFile());

  if (!cached) {
    mkdirSync(output, { recursive: true });
    rmSync(cachePath, { force: true });
    const status = runTauri(["icon", source, "-o", join("src-tauri", "icons-private")]);
    if (status !== 0) throw new Error(`private icon generation failed (exit ${status})`);
    copyFileSync(join(output, "32x32.png"), tray);
    console.log("tauri-private: generated desktop icons and 32x32 tray icon");
  } else {
    console.log("tauri-private: using cached private icons");
  }

  // The personal desktop icon must not leave generated mobile assets behind.
  for (const mobile of ["android", "ios"]) rmSync(join(output, mobile), { recursive: true, force: true });
  if (!cached) {
    const files = readdirSync(output).filter((name) => statSync(join(output, name)).isFile());
    writeFileSync(cachePath, `${JSON.stringify({ sourceHash, files }, null, 2)}\n`);
  }
  writeIfChanged(config, `${JSON.stringify({
    bundle: { icon: desktopIcons.map((name) => `icons-private/${name}`) },
  }, null, 2)}\n`);
  return configArg;
}

if (process.argv[1] && resolve(process.argv[1]) === script) {
  try {
    const args = process.argv.slice(2);
    const config = isPrivateCommand(args) ? preparePrivateIcons() : null;
    process.exitCode = runTauri(withPrivateConfig(args, config));
  } catch (error) {
    console.error(`tauri-private: ${error.message}`);
    process.exitCode = 1;
  }
}
