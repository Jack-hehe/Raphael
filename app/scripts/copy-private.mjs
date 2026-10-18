// Private media and its public/private/ copies are gitignored. Missing assets use UI fallbacks;
// the optional media must never prevent a frontend build.

import { copyFileSync, existsSync, mkdirSync, rmSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { PRIVATE_FRONTEND_ASSETS, privateAssetsDirectory } from "./private-assets.mjs";

const app = resolve(import.meta.dirname, "..");
const privateDir = privateAssetsDirectory(app);

for (const name of PRIVATE_FRONTEND_ASSETS) {
  const source = join(privateDir, name);
  const target = resolve(app, "public/private", name);
  try {
    if (existsSync(source)) {
      mkdirSync(dirname(target), { recursive: true });
      copyFileSync(source, target);
      console.log(`copy-private: ${name} copied to public/private/`);
    } else {
      // An old copy must not outlive its source.
      rmSync(target, { force: true });
      console.log(`copy-private: ${name} not found, the UI will use its fallback`);
    }
  } catch (err) {
    try {
      rmSync(target, { force: true });
    } catch {
      // Read-only or invalid targets must not make these optional assets block the build.
    }
    console.log(`copy-private: could not copy ${name} (${err.message}), the UI will use its fallback`);
  }
}
