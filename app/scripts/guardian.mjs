import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { resolve, join } from "node:path";
import { pathToFileURL } from "node:url";

export function coreCheck(kind, args = []) {
  const root = resolve(import.meta.dirname, "../..");
  const core = join(root, "core");
  const python = join(core, ".venv", process.platform === "win32" ? "Scripts/python.exe" : "bin/python");
  const module = kind === "live" ? "raphael_core.evaluate.guardian" : "raphael_core.evaluate.design_judge";
  const options = kind === "live" && process.env.RAPHAEL_LIVE_CONFIG ? ["--config", process.env.RAPHAEL_LIVE_CONFIG] : [];
  if (kind === "judge" && process.env.RAPHAEL_JUDGE_REFERENCES) {
    const references = JSON.parse(process.env.RAPHAEL_JUDGE_REFERENCES);
    if (!Array.isArray(references) || !references.length || references.some(path => typeof path !== "string")) throw new Error("RAPHAEL_JUDGE_REFERENCES must be a JSON array of image paths");
    options.push("--references", ...references);
  }
  return existsSync(python)
    ? { name: kind, command: python, args: ["-m", module, ...args, ...options], cwd: core }
    : { name: kind, command: "uv", args: ["run", "--offline", "--frozen", "--no-sync", "python", "-m", module, ...args, ...options], cwd: core };
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  try {
    const args = process.argv.slice(2);
    if (args[0] && !args[0].startsWith("--")) args[0] = resolve(args[0]);
    const references = args.indexOf("--references");
    if (references >= 0) for (let i = references + 1; i < args.length && !args[i].startsWith("--"); i++) args[i] = resolve(args[i]);
    const check = coreCheck("judge", args);
    const child = spawn(check.command, check.args, { cwd: check.cwd, windowsHide: true, shell: false, stdio: "inherit" });
    const stop = signal => child.kill(signal);
    process.on("SIGINT", stop); process.on("SIGTERM", stop);
    child.once("error", error => { console.error(`Judge could not start: ${error.code ?? "unknown"}`); process.exitCode = 1; });
    child.once("close", code => { process.exitCode = code ?? 1; process.removeListener("SIGINT", stop); process.removeListener("SIGTERM", stop); });
  } catch (error) { console.error(error.message); process.exitCode = 1; }
}
