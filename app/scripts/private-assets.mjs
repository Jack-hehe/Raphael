import { existsSync, readFileSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";

/** The artwork the frontend loads from /private/. tauri.mjs reads the desktop icon from the private directory itself. */
export const PRIVATE_FRONTEND_ASSETS = ["glyph-ring.png", "in-app-icon.png"];

// What a development build generates from private artwork and a later build would embed:
// frontend copies, the desktop and tray icons (build.rs picks icons-private/icon.ico over the public icon)
// and the Tauri config that selects them.
const GENERATED_PRIVATE_OUTPUT = [
  "public/private", "dist/private", "src-tauri/icons-private", "src-tauri/tauri.private.conf.json",
];

/**
 * A public build never uses private artwork: release builds (tauri.mjs sets RAPHAEL_PUBLIC_BUILD for the
 * release config) and any run with CI set. The private directory is then ignored, even when
 * RAPHAEL_PRIVATE_DIR points at one.
 */
export function isPublicBuild(env = process.env) {
  const ci = env.CI?.toLowerCase();
  return env.RAPHAEL_PUBLIC_BUILD === "1" || (Boolean(ci) && ci !== "false" && ci !== "0");
}

/** Fails when a public build would embed generated private output left over from a development build. */
export function assertPublicAssets(appDirectory) {
  const found = GENERATED_PRIVATE_OUTPUT.filter((path) => existsSync(join(appDirectory, path)));
  if (found.length) {
    throw new Error(`Public builds refuse private artwork, but these generated paths exist: ${found.join(", ")}. `
      + "They are gitignored; delete them and build again.");
  }
}

/** The private source directory, or null for a public build, which has none. */
export function privateAssetsDirectory(appDirectory) {
  if (isPublicBuild()) return null;
  if (process.env.RAPHAEL_PRIVATE_DIR) return resolve(process.env.RAPHAEL_PRIVATE_DIR);

  const repository = resolve(appDirectory, "..");
  const marker = join(repository, ".git");
  try {
    if (statSync(marker).isFile()) {
      const gitDir = readFileSync(marker, "utf8").match(/^gitdir:\s*(.+)\s*$/m)?.[1].trim();
      if (gitDir) {
        const worktreeGitDir = resolve(repository, gitDir);
        const commonMarker = join(worktreeGitDir, "commondir");
        if (existsSync(commonMarker)) {
          // Linked worktrees share the original checkout's private source, never its output.
          const commonGitDir = resolve(worktreeGitDir, readFileSync(commonMarker, "utf8").trim());
          return join(dirname(commonGitDir), "assets-private");
        }
      }
    }
  } catch {
    // Source archives without Git metadata keep the ordinary repository-relative fallback.
  }
  return join(repository, "assets-private");
}
