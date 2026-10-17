// Copies the private glyph ring texture to where Vite serves it (/private/glyph-ring.png).
//
// The texture is sampled from the anime, so it lives in the gitignored assets-private/ folder at
// the repository root and is never committed (the copy in public/private/ is gitignored too).
// Without it the overlay draws a plain vector ring. This script prints one line either way and
// never fails the build.

import { copyFileSync, existsSync, mkdirSync, rmSync } from "node:fs";
import { dirname, resolve } from "node:path";

const app = resolve(import.meta.dirname, "..");
const source = resolve(app, "../assets-private/glyph-ring.png");
const target = resolve(app, "public/private/glyph-ring.png");

try {
  if (existsSync(source)) {
    mkdirSync(dirname(target), { recursive: true });
    copyFileSync(source, target);
    console.log("copy-private: glyph-ring.png copied to public/private/");
  } else {
    // An old copy must not outlive its source, or the plain ring could never be tried.
    rmSync(target, { force: true });
    console.log("copy-private: assets-private/glyph-ring.png not found, the overlay will draw a plain vector ring");
  }
} catch (err) {
  console.log(`copy-private: could not copy glyph-ring.png (${err.message}), the overlay will draw a plain vector ring`);
}
