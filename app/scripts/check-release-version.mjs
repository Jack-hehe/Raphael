// A release has one version. It is written in four files, and the installer, the executable and the
// Python core would each report a different one if they drifted apart.
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const script = fileURLToPath(import.meta.url);
const defaultRepository = resolve(dirname(script), "..", "..");

/** The `version = "x"` key of one TOML table. Dependency tables and inline tables are other tables. */
function tableVersion(text, table) {
  let inTable = false;
  for (const line of text.replace(/^﻿/, "").split(/\r?\n/)) {
    const header = /^\s*\[\[?([^\]]+)\]\]?\s*(?:#.*)?$/.exec(line);
    if (header) {
      inTable = header[1].trim() === table;
      continue;
    }
    const version = inTable && /^\s*version\s*=\s*"([^"]*)"\s*(?:#.*)?$/.exec(line);
    if (version) return version[1];
  }
  return undefined;
}

const versionFiles = [
  ["app/package.json", (text) => JSON.parse(text.replace(/^﻿/, "")).version],
  ["app/src-tauri/tauri.conf.json", (text) => JSON.parse(text.replace(/^﻿/, "")).version],
  ["app/src-tauri/Cargo.toml", (text) => tableVersion(text, "package")],
  ["core/pyproject.toml", (text) => tableVersion(text, "project")],
];

/** The version of each file that carries one, by repository-relative path. */
export function readVersions(repository = defaultRepository) {
  return Object.fromEntries(versionFiles.map(([path, read]) => [path, read(readFileSync(join(repository, path), "utf8"))]));
}

/** The one version all files agree on, which must be a numeric major.minor.patch. */
export function releaseVersion(repository = defaultRepository) {
  const versions = readVersions(repository);
  const [version, ...others] = new Set(Object.values(versions));
  if (others.length || !/^\d+\.\d+\.\d+$/.test(version ?? "")) {
    const listing = Object.entries(versions).map(([path, value]) => `  ${path}: ${value ?? "no version found"}`);
    throw new Error(`Release versions must match and use a numeric major.minor.patch:\n${listing.join("\n")}`);
  }
  return version;
}

if (process.argv[1] && resolve(process.argv[1]) === script) {
  try {
    console.log(releaseVersion());
  } catch (error) {
    console.error(`check-release-version: ${error.message}`);
    process.exitCode = 1;
  }
}
