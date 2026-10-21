// Runs the installed Tauri CLI directly, so no shell, recursive npm script or network fetch can
// consume or re-quote its arguments.
import { spawnSync } from "node:child_process";
import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const appDir = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const entry = createRequire(import.meta.url).resolve("@tauri-apps/cli/tauri.js");
const result = spawnSync(process.execPath, [entry, ...process.argv.slice(2)], { cwd: appDir, stdio: "inherit" });
if (result.error) throw result.error;
process.exitCode = result.status ?? 1;
