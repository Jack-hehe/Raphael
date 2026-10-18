import { existsSync, readFileSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";

/** Character art is independent of the desktop application's icon. */
export const PRIVATE_FRONTEND_ASSETS = ["glyph-ring.png", "app-icon.png", "avatar.png"];

export function privateAssetsDirectory(appDirectory) {
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
