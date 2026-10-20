// Assembles the files a release run uploads: the installer, the third-party notices, BUILD-INFO.txt
// (which source and which run built them, with what) and SHA256SUMS.txt covering all of them.
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { copyFileSync, existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const script = fileURLToPath(import.meta.url);
const appDirectory = resolve(dirname(script), "..");
const coreDirectory = resolve(appDirectory, "..", "core");
const unavailable = "unavailable";

/** Starts a command and waits for it, its output as text. */
const spawnText = (command, args, options = {}) => spawnSync(command, args, { encoding: "utf8", ...options });

function firstLine(run, command, args) {
  const result = run(command, args);
  const line = result.status === 0 ? result.stdout.trim().split(/\r?\n/)[0] : "";
  return line || unavailable;
}

// npm is npm.cmd on Windows, which only a shell can start.
function npmVersion(run) {
  const result = run("npm --version", [], { shell: true });
  return (result.status === 0 && result.stdout.trim()) || unavailable;
}

function lockedVersion(lockPath, name) {
  try {
    return new RegExp(`\\[\\[package\\]\\]\\r?\\nname = "${name}"\\r?\\nversion = "([^"]+)"`).exec(readFileSync(lockPath, "utf8"))?.[1] ?? unavailable;
  } catch {
    return unavailable;
  }
}

function readJson(path) {
  try {
    return JSON.parse(readFileSync(path, "utf8"));
  } catch {
    return undefined;
  }
}

/**
 * The versions of what built the release, each "unavailable" if it cannot be read. `run` starts a
 * tool as spawnSync does; a test gives its own, so that it starts none.
 */
export function collectTools(env = process.env, run = spawnText) {
  const nsis = join(env.LOCALAPPDATA ?? "", "tauri", "NSIS", "makensis.exe");
  return {
    node: process.version,
    npm: npmVersion(run),
    rustc: firstLine(run, "rustc", ["--version"]),
    cargo: firstLine(run, "cargo", ["--version"]),
    uv: firstLine(run, "uv", ["--version"]),
    "python in the frozen core": readJson(join(coreDirectory, "dist", "raphael-core", "bundle-manifest.json"))?.python ?? unavailable,
    // `uv sync --locked` installs exactly this version; asking uv to run Python could create an environment.
    pyinstaller: lockedVersion(join(coreDirectory, "uv.lock"), "pyinstaller"),
    "tauri cli": readJson(join(appDirectory, "node_modules", "@tauri-apps", "cli", "package.json"))?.version ?? unavailable,
    nsis: existsSync(nsis) ? firstLine(run, nsis, ["/VERSION"]) : unavailable,
  };
}

/** BUILD-INFO.txt: plain ASCII lines, so it reads the same everywhere and hashes the same everywhere. */
export function buildInfo({ version, env = process.env, tools }) {
  const run = env.GITHUB_RUN_ID;
  const attempt = env.GITHUB_RUN_ATTEMPT ?? "1";
  const server = env.GITHUB_SERVER_URL ?? "https://github.com";
  const runUrl = run && env.GITHUB_REPOSITORY ? `${server}/${env.GITHUB_REPOSITORY}/actions/runs/${run}/attempts/${attempt}` : "not a GitHub Actions run";
  const lines = [
    "Raphael Windows x64 release build",
    "",
    `version: ${version}`,
    `commit: ${env.GITHUB_SHA ?? "unknown"}`,
    `ref: ${env.GITHUB_REF ?? "unknown"}`,
    `repository: ${env.GITHUB_REPOSITORY ?? "unknown"}`,
    `workflow: ${env.GITHUB_WORKFLOW ?? "unknown"}`,
    `run id: ${run ?? "unknown"}`,
    `run attempt: ${attempt}`,
    `run url: ${runUrl}`,
    `runner image: ${[env.ImageOS, env.ImageVersion].filter(Boolean).join(" ") || "unknown"}`,
    "",
    "tools:",
    ...Object.entries(tools).map(([name, value]) => `  ${name}: ${value}`),
  ];
  return `${lines.join("\n")}\n`;
}

/** SHA256SUMS.txt in the format of `sha256sum -c`: lowercase hash, two spaces, name, LF line ends. */
export function checksums(directory) {
  const names = readdirSync(directory).filter((name) => name !== "SHA256SUMS.txt").sort((a, b) => (a < b ? -1 : a > b ? 1 : 0));
  return names.map((name) => `${createHash("sha256").update(readFileSync(join(directory, name))).digest("hex")}  ${name}\n`).join("");
}

export function prepareReleaseAssets({ installer, notices, version, output, env = process.env, tools = collectTools(env) }) {
  if (existsSync(output)) throw new Error(`Output directory already exists: ${output}`);
  mkdirSync(output, { recursive: true });
  copyFileSync(installer, join(output, `Raphael-${version}-windows-x64-setup.exe`));
  copyFileSync(notices, join(output, "THIRD_PARTY_NOTICES.txt"));
  writeFileSync(join(output, "BUILD-INFO.txt"), buildInfo({ version, env, tools }), "utf8");
  writeFileSync(join(output, "SHA256SUMS.txt"), checksums(output), "utf8");
  return readdirSync(output).sort();
}

/** Runs the command line `args`. A test gives its own `env` and `tools`, so that no tool is started. */
export function main(args = process.argv.slice(2), { env = process.env, tools } = {}) {
  const options = {};
  for (let index = 0; index < args.length; index += 2) {
    const name = args[index]?.replace(/^--/, "");
    if (!["installer", "notices", "version", "output"].includes(name) || !args[index + 1]) {
      throw new Error(`Usage: node scripts/prepare-release-assets.mjs --installer FILE --notices FILE --version X.Y.Z --output DIRECTORY (got ${args[index] ?? "nothing"})`);
    }
    options[name] = name === "version" ? args[index + 1] : resolve(args[index + 1]);
  }
  for (const name of ["installer", "notices", "version", "output"]) {
    if (!options[name]) throw new Error(`Missing --${name}`);
  }
  console.log(prepareReleaseAssets({ ...options, env, tools }).join("\n"));
}

if (process.argv[1] && resolve(process.argv[1]) === script) {
  try {
    main();
  } catch (error) {
    console.error(`prepare-release-assets: ${error.message}`);
    process.exitCode = 1;
  }
}
