import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import {
  existsSync, lstatSync, mkdirSync, readFileSync, readdirSync, renameSync, writeFileSync,
} from "node:fs";
import { dirname, isAbsolute, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const script = fileURLToPath(import.meta.url);
const defaultAppDirectory = resolve(dirname(script), "..");
const RUST_TARGET = "x86_64-pc-windows-msvc";
// Build-time npm packages whose code is emitted into the shipped bundle, so their notices ship too.
// Vite 8.3.4 writes its modulepreload polyfill (a port of Vite's own, injected by Rolldown) into
// dist/assets/*.js; no other Vite or Rolldown code is in the output. Check any addition against a real build.
const SHIPPED_BUILD_PACKAGES = new Set(["vite"]);
const licenseName = /^(?:licen[cs]e|copying)/i;
const noticeName = /^(?:licen[cs]e|copying|notice|authors|third[-_ ]?party.*(?:licen[cs]e|notice))/i;
const licenseDirectoryName = /^(?:licen[cs]es?|legal|notices?|third[-_]party[-_]licen[cs]es?)$/i;
const compare = (a, b) => a < b ? -1 : a > b ? 1 : 0;
const normalizeText = (text) => text.replace(/^\uFEFF/, "").replace(/\r\n?/g, "\n").trim();

function readJson(path) {
  return JSON.parse(readFileSync(path, "utf8").replace(/^\uFEFF/, ""));
}

function inside(directory, path) {
  const local = relative(directory, path);
  return local !== ".." && !local.startsWith(`..${sep}`) && !isAbsolute(local);
}

function regularFile(path) {
  return existsSync(path) && lstatSync(path).isFile();
}

function readText(path) {
  const bytes = readFileSync(path);
  if (bytes.length > 4 * 1024 * 1024 || bytes.includes(0)) return null;
  try {
    return normalizeText(new TextDecoder("utf-8", { fatal: true }).decode(bytes)) || null;
  } catch {
    return null;
  }
}

function repositoryUrl(repository) {
  let value = typeof repository === "string" ? repository : repository?.url;
  if (!value) return "not declared";
  value = value.replace(/^git\+/, "").replace(/^git@github\.com:/, "https://github.com/");
  if (/^github:/.test(value)) value = value.replace(/^github:/, "https://github.com/");
  if (/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+(?:#[^\s]*)?$/.test(value)) value = `https://github.com/${value}`;
  try {
    const url = new URL(value);
    if (!["https:", "http:", "git:"].includes(url.protocol) || url.username || url.password) {
      throw new Error("Unsupported repository URL");
    }
    return url.href;
  } catch {
    throw new Error("repository must be a public URL without credentials");
  }
}

function readmeLicense(text) {
  const lines = text.split("\n");
  for (let index = 0; index < lines.length; index++) {
    const heading = /^(#{1,6})\s+.*\blicen[cs](?:e|ing)\b/i.exec(lines[index]);
    const setext = /\blicen[cs](?:e|ing)\b/i.test(lines[index]) && /^\s*[=-]{3,}\s*$/.test(lines[index + 1] ?? "");
    if (!heading && !setext) continue;
    const level = heading ? heading[1].length : lines[index + 1].trim().startsWith("=") ? 1 : 2;
    const start = index + (setext ? 2 : 1);
    let end = start;
    while (end < lines.length) {
      const next = /^(#{1,6})\s+/.exec(lines[end]);
      if (next && next[1].length <= level) break;
      if (/^\s*[=-]{3,}\s*$/.test(lines[end + 1] ?? "") && level >= 2) break;
      end++;
    }
    const section = normalizeText(lines.slice(start, end).join("\n"));
    // A README pointing at a license or naming an SPDX ID is not the license text.
    if (section.length > 200 && /Permission (?:is hereby granted|to use, copy)|Redistribution and use in source|TERMS AND CONDITIONS FOR USE|Mozilla Public License Version 2\.0|GNU (?:LESSER |AFFERO )?GENERAL PUBLIC LICENSE/i.test(section)) {
      return section;
    }
  }
  return null;
}

export function collectLicenseTexts(directory, declaredFile) {
  const files = new Map();
  const gaps = [];
  const add = (path, primary) => {
    if (!regularFile(path)) return false;
    const text = readText(path);
    if (!text) return false;
    files.set(path, { text, primary });
    return true;
  };
  if (declaredFile) {
    const path = resolve(directory, declaredFile);
    if (!inside(directory, path) || !add(path, true)) {
      gaps.push("declared license_file is missing, outside the package, or not UTF-8 text");
    }
  }
  const walkLicenses = (path, depth = 0) => {
    if (depth > 8) throw new Error("license directory nesting exceeds the supported limit");
    for (const entry of readdirSync(path, { withFileTypes: true }).sort((a, b) => compare(a.name, b.name))) {
      const child = join(path, entry.name);
      if (entry.isDirectory()) walkLicenses(child, depth + 1);
      else if (entry.isFile()) add(child, !/^(?:notice|authors)/i.test(entry.name));
    }
  };
  const entries = readdirSync(directory, { withFileTypes: true }).sort((a, b) => compare(a.name, b.name));
  for (const entry of entries) {
    const path = join(directory, entry.name);
    if (entry.isFile() && noticeName.test(entry.name)) add(path, licenseName.test(entry.name));
    else if (entry.isDirectory() && licenseDirectoryName.test(entry.name)) walkLicenses(path);
  }
  if (![...files.values()].some((file) => file.primary)) {
    for (const entry of entries.filter((item) => item.isFile() && /^readme(?:\.|$)/i.test(item.name))) {
      const text = readText(join(directory, entry.name));
      const section = text && readmeLicense(text);
      if (section) files.set(join(directory, entry.name), { text: section, primary: true });
    }
  }
  return {
    texts: [...new Set([...files.values()].map((file) => file.text))].sort(compare),
    hasLicense: [...files.values()].some((file) => file.primary),
    gaps,
  };
}

function supplementalTexts(pkg, directory) {
  const indexPath = join(directory, "manifest.json");
  if (!existsSync(indexPath)) return [];
  const index = readJson(indexPath);
  if (index.schemaVersion !== 1 || !Array.isArray(index.packages)) throw new Error("invalid supplemental license manifest");
  const entries = index.packages.filter((entry) => entry.name === pkg.name && entry.version === pkg.version);
  if (!entries.length) return [];
  if (entries.length !== 1) throw new Error("duplicate supplemental license entries");
  const entry = entries[0];
  const vcsPath = join(dirname(pkg.manifest_path), ".cargo_vcs_info.json");
  const commit = existsSync(vcsPath) ? readJson(vcsPath).git?.sha1 : null;
  if (entry.license !== pkg.license || entry.repository !== pkg.repository || entry.gitCommit !== commit) {
    throw new Error("supplemental license metadata does not match the exact published crate");
  }
  if (!/^[a-f0-9]{40}$/.test(entry.gitCommit) || !Array.isArray(entry.files) || !entry.files.length) {
    throw new Error("supplemental license provenance is incomplete");
  }
  return entry.files.map((file) => {
    const path = resolve(directory, file.file);
    if (!inside(directory, path) || !regularFile(path) || !/^https:\/\//.test(file.sourceUrl)) {
      throw new Error("supplemental license file or source URL is invalid");
    }
    const bytes = readFileSync(path);
    if (createHash("sha256").update(bytes).digest("hex") !== file.sha256) {
      throw new Error("supplemental license SHA-256 mismatch");
    }
    const text = readText(path);
    if (!text) throw new Error("supplemental license is not UTF-8 text");
    return text;
  });
}

export function readCargoMetadata(appDirectory) {
  const result = spawnSync("cargo", [
    "metadata", "--locked", "--format-version", "1", "--filter-platform", RUST_TARGET,
    "--manifest-path", "src-tauri/Cargo.toml",
  ], { cwd: appDirectory, encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });
  if (result.error || result.status !== 0) throw new Error(`cargo metadata failed (exit ${result.status ?? "unavailable"})`);
  return JSON.parse(result.stdout);
}

/** The cfg set of the release target, as `rustc --print cfg` prints it: `windows`, `target_os="windows"`. */
export function readRustTargetCfg() {
  const result = spawnSync("rustc", ["--print", "cfg", "--target", RUST_TARGET], { encoding: "utf8" });
  if (result.error || result.status !== 0) throw new Error(`rustc --print cfg failed (exit ${result.status ?? "unavailable"})`);
  return result.stdout.split(/\r?\n/).filter(Boolean);
}

// Parses the cfg(...) part of a Cargo `[target.'cfg(...)'.dependencies]` key: all(), any(), not(),
// bare names and key = "value" pairs. Anything else is refused instead of guessed.
function parseCfg(text) {
  const tokens = text.match(/"[^"]*"|[A-Za-z_][A-Za-z0-9_]*|[(),=]|\S/g) ?? [];
  let position = 0;
  const fail = () => { throw new Error(`Unsupported Cargo target condition: ${text}`); };
  const take = (expected) => {
    const token = tokens[position++];
    if (token === undefined || (expected !== undefined && token !== expected)) fail();
    return token;
  };
  const expression = () => {
    const name = take();
    if (!/^[A-Za-z_]\w*$/.test(name)) fail();
    if (tokens[position] === "(") {
      if (!["all", "any", "not"].includes(name)) fail();
      take("(");
      const args = [];
      while (tokens[position] !== ")") {
        args.push(expression());
        if (tokens[position] === ",") take(",");
        else if (tokens[position] !== ")") fail();
      }
      take(")");
      if (name === "not" && args.length !== 1) fail();
      return { name, args };
    }
    if (tokens[position] === "=") {
      take("=");
      const value = take();
      if (!/^"[^"]*"$/.test(value)) fail();
      return { name, value: value.slice(1, -1) };
    }
    return { name };
  };
  take("cfg");
  take("(");
  const root = expression();
  take(")");
  if (position !== tokens.length) fail();
  return root;
}

function cfgHolds(node, cfg) {
  if (node.args) {
    if (node.name === "all") return node.args.every((arg) => cfgHolds(arg, cfg));
    if (node.name === "any") return node.args.some((arg) => cfgHolds(arg, cfg));
    return !cfgHolds(node.args[0], cfg);
  }
  return cfg.has(node.value === undefined ? node.name : `${node.name}="${node.value}"`);
}

// A dependency edge's `target` is null (always), a target triple or a cfg() expression.
function targetApplies(target, cfg) {
  if (target === null || target === undefined) return true;
  return target.trim().startsWith("cfg(") ? cfgHolds(parseCfg(target.trim()), cfg) : target.trim() === RUST_TARGET;
}

// Follow only the edges that put code into the release binary: normal dependencies (not build or
// dev) that apply to the release target. `--filter-platform` keeps an edge when any of its kinds
// matches, so each kind is checked on its own: a build-script-only or Linux-only crate is not shipped.
function rustPackages(metadata, targetCfg) {
  const root = metadata.resolve?.root;
  if (!root) throw new Error("Cargo metadata must have a root package");
  const cfg = new Set(targetCfg);
  const packages = new Map(metadata.packages.map((pkg) => [pkg.id, pkg]));
  const nodes = new Map(metadata.resolve.nodes.map((node) => [node.id, node]));
  const shipped = (dependency) => {
    if (!Array.isArray(dependency.dep_kinds)) throw new Error("Cargo metadata must include dependency kinds");
    return dependency.dep_kinds.some((kind) => kind.kind === null && targetApplies(kind.target, cfg));
  };
  const pending = [root];
  const visited = new Set();
  while (pending.length) {
    const id = pending.pop();
    if (visited.has(id)) continue;
    visited.add(id);
    const node = nodes.get(id);
    if (!node || !packages.has(id)) throw new Error("Cargo dependency graph is incomplete");
    pending.push(...node.deps.filter(shipped).map((dependency) => dependency.pkg));
  }
  return [...visited].filter((id) => id !== root).map((id) => packages.get(id));
}

function platformMatches(constraint, value) {
  if (!constraint?.length) return true;
  if (constraint.includes(`!${value}`)) return false;
  const allowed = constraint.filter((item) => !item.startsWith("!"));
  return !allowed.length || allowed.includes(value) || allowed.includes("any");
}

function nodeLicense(pkg, locked) {
  return typeof pkg.license === "string" ? pkg.license : pkg.license?.type ?? locked.license ?? "not declared";
}

function companionLicenseTexts(pkg, spdx, candidates) {
  // Native optional packages can omit licenses shipped in their same-version JS wrapper.
  // Require the exact optional dependency edge and matching upstream identity before reuse.
  for (const candidate of candidates) {
    const parent = candidate.pkg;
    if (parent.optionalDependencies?.[pkg.name] !== pkg.version || parent.version !== pkg.version) continue;
    if (nodeLicense(parent, candidate.entry) !== spdx || repositoryUrl(parent.repository) !== repositoryUrl(pkg.repository)) continue;
    if (repositoryUrl(pkg.repository) === "not declared") continue;
    const license = collectLicenseTexts(candidate.directory);
    if (license.hasLicense && !license.gaps.length) return license.texts;
  }
  return [];
}

export function collectDependencyNotices({
  appDirectory = defaultAppDirectory,
  metadata = readCargoMetadata(appDirectory),
  rustTargetCfg = readRustTargetCfg(),
  supplementalDirectory = join(appDirectory, "scripts", "release-licenses"),
} = {}) {
  const records = [];
  const gaps = [];
  const collect = (record, directory, declaredFile, supplement) => {
    const label = `${record.ecosystem} ${record.name}@${record.version}`;
    try {
      const license = collectLicenseTexts(directory, declaredFile);
      gaps.push(...license.gaps.map((gap) => `${label}: ${gap}`));
      if (!license.hasLicense && supplement) {
        const texts = supplement();
        license.texts.push(...texts);
        license.hasLicense = texts.length > 0;
      }
      if (!license.hasLicense) {
        gaps.push(`${label}: no license text (checked license_file, LICENSE/COPYING files, license directories and README license sections)`);
      }
      records.push({ ...record, repository: repositoryUrl(record.repository), texts: [...new Set(license.texts)].sort(compare) });
    } catch (error) {
      gaps.push(`${label}: ${error.message}`);
    }
  };
  const shippedCrates = rustPackages(metadata, rustTargetCfg);
  for (const pkg of shippedCrates) {
    let source;
    if (pkg.source === "registry+https://github.com/rust-lang/crates.io-index") {
      source = `https://crates.io/api/v1/crates/${encodeURIComponent(pkg.name)}/${encodeURIComponent(pkg.version)}/download`;
    } else if (!pkg.source && inside(resolve(appDirectory, ".."), dirname(pkg.manifest_path))) {
      source = relative(resolve(appDirectory, ".."), dirname(pkg.manifest_path)).split(sep).join("/");
    } else {
      gaps.push(`Rust ${pkg.name}@${pkg.version}: source is not a crates.io or in-repository vendored package`);
      continue;
    }
    collect({ ecosystem: "Rust", name: pkg.name, version: pkg.version, spdx: pkg.license ?? "not declared", repository: pkg.repository, source }, dirname(pkg.manifest_path), pkg.license_file, () => supplementalTexts(pkg, supplementalDirectory));
  }
  const lock = readJson(join(appDirectory, "package-lock.json"));
  if (lock.lockfileVersion < 2 || !lock.packages) throw new Error("package-lock.json must contain locked package locations");
  const nodeCandidates = [];
  for (const [location, entry] of Object.entries(lock.packages).sort(([a], [b]) => compare(a, b))) {
    if (!location) continue;
    if (!/^node_modules\//.test(location) || location.split("/").includes("..")) {
      gaps.push("Frontend lockfile contains an unsupported package location");
      continue;
    }
    // Packages only the build and tests need are not distributed, so they need no notice and no installed copy.
    const name = location.replace(/^(?:.*\/)?node_modules\//, "");
    if ((entry.dev || entry.devOptional) && !SHIPPED_BUILD_PACKAGES.has(name)) continue;
    const directory = resolve(appDirectory, location);
    const manifest = join(directory, "package.json");
    if (!existsSync(manifest)) {
      if (entry.optional && (!platformMatches(entry.os, "win32") || !platformMatches(entry.cpu, "x64"))) continue;
      gaps.push(`Frontend ${name}@${entry.version}: locked Windows x64 package is not installed; run npm ci`);
      continue;
    }
    const pkg = readJson(manifest);
    if (!pkg.name || pkg.version !== entry.version) {
      gaps.push(`Frontend ${pkg.name ?? "unnamed"}@${entry.version}: installed package does not match package-lock.json`);
      continue;
    }
    nodeCandidates.push({ directory, pkg, entry });
  }
  for (const { directory, pkg, entry } of nodeCandidates) {
    const spdx = nodeLicense(pkg, entry);
    collect({ ecosystem: "Frontend", name: pkg.name, version: pkg.version, spdx, repository: pkg.repository }, directory, undefined, () => companionLicenseTexts(pkg, spdx, nodeCandidates));
  }
  const unique = new Map();
  for (const record of records) {
    const key = `${record.ecosystem} ${record.name}@${record.version}`;
    if (unique.has(key) && JSON.stringify(unique.get(key)) !== JSON.stringify(record)) gaps.push(`${key}: conflicting copies of the same dependency`);
    unique.set(key, record);
  }
  return {
    records: [...unique.values()].sort((a, b) => compare(`${a.ecosystem}\0${a.name}\0${a.version}`, `${b.ecosystem}\0${b.name}\0${b.version}`)),
    gaps: [...new Set(gaps)].sort(compare),
  };
}

export function renderDependencyNotices({ records, gaps }) {
  if (gaps.length) throw new Error(`License collection is incomplete:\n${gaps.map((gap) => `- ${gap}`).join("\n")}`);
  return records.map((record) => [
    "=".repeat(78), `${record.ecosystem}: ${record.name} ${record.version}`, `SPDX: ${record.spdx}`,
    `Repository: ${record.repository}`, ...(record.source ? [`Source: ${record.source}`] : []),
    "-".repeat(78), record.texts.join(`\n\n${"-".repeat(78)}\n\n`),
  ].join("\n")).join("\n\n");
}

export function collectReleaseNotices({
  appDirectory = defaultAppDirectory,
  pythonNotices = resolve(appDirectory, "..", "core", "dist", "raphael-core", "THIRD_PARTY_NOTICES.txt"),
  ...options
} = {}) {
  const result = collectDependencyNotices({ appDirectory, ...options });
  const dependencies = renderDependencyNotices(result);
  if (!regularFile(pythonNotices)) throw new Error("Python THIRD_PARTY_NOTICES.txt is required; build raphael-core first or use --python-notices");
  const python = readText(pythonNotices);
  if (!python) throw new Error("Python THIRD_PARTY_NOTICES.txt must contain UTF-8 notice text");
  const text = [
    "THIRD-PARTY NOTICES", "", "Licenses and notices for dependencies distributed with Raphael.",
    "Dependency licenses do not assign a license to the Raphael project.", "", dependencies,
    "", "=".repeat(78), "Python core dependencies", "=".repeat(78), "", python, "",
  ].join("\n");
  if (/(?:^|[\s("'=])[A-Za-z]:[\\/]|file:\/\/|\/(?:Users|home|tmp|private\/tmp)\//m.test(text)) {
    throw new Error("Notice text contains an absolute local path; remove build-machine paths from the input notices");
  }
  return { ...result, text };
}

export function main(args = process.argv.slice(2)) {
  const options = {};
  let output = join(defaultAppDirectory, "src-tauri", "release-notices", "THIRD_PARTY_NOTICES.txt");
  for (let index = 0; index < args.length; index++) {
    const arg = args[index];
    if (arg === "--help") {
      console.log("Usage: node scripts/collect-release-notices.mjs [--output FILE] [--python-notices FILE] [--supplemental-licenses DIRECTORY]");
      return;
    }
    if (!["--output", "--python-notices", "--supplemental-licenses"].includes(arg) || !args[index + 1] || args[index + 1].startsWith("--")) {
      throw new Error(`Invalid argument: ${arg}`);
    }
    const value = resolve(args[++index]);
    if (arg === "--output") output = value;
    else if (arg === "--python-notices") options.pythonNotices = value;
    else options.supplementalDirectory = value;
  }
  const result = collectReleaseNotices(options);
  mkdirSync(dirname(output), { recursive: true });
  const temporary = `${output}.tmp`;
  writeFileSync(temporary, result.text, "utf8");
  renameSync(temporary, output);
  const count = (ecosystem) => result.records.filter((record) => record.ecosystem === ecosystem).length;
  console.log(`Collected ${count("Rust")} Rust and ${count("Frontend")} frontend packages, plus Python core notices.`);
}

if (process.argv[1] && resolve(process.argv[1]) === script) {
  try {
    main();
  } catch (error) {
    console.error(`collect-release-notices: ${error.message}`);
    process.exitCode = 1;
  }
}
