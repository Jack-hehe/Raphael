import { existsSync, readFileSync, readdirSync } from "node:fs";
import { resolve, join } from "node:path";
import ts from "typescript";
import { expect, it } from "vitest";

const root = resolve(__dirname, "../..");
const read = (path: string): string => readFileSync(path, "utf8");
const walk = (dir: string): string[] => readdirSync(dir, { withFileTypes: true }).flatMap(entry => entry.isDirectory() ? walk(join(dir, entry.name)) : [join(dir, entry.name)]);
const sources = walk(join(root, "app/src/main")).filter(path => path.endsWith(".ts") && !path.endsWith(".test.ts"));

it("mechanical rules link existing named tests", () => {
  for (const name of ["RULES", "ARCHITECTURE"]) {
    const deployed = join(root, `.agents/product/${name}.md`), staged = join(root, `.fold/.agents/product/${name}.md`);
    const spec = read(existsSync(staged) ? staged : deployed);
    const rows = spec.split("\n").filter(line => /^\| (?:R-[A-Z]+-\d+|A-\d+) \|/.test(line));
    expect(rows.length).toBeGreaterThan(10);
    const ids = rows.map(row => row.split("|")[1]!.trim());
    expect(new Set(ids).size).toBe(ids.length);
    for (const row of rows) {
      const checks = row.split("|")[name === "RULES" ? 4 : 3]!.split(",").map(check => check.trim());
      if (!checks.some(check => ["lint", "look", "pytest", "vitest"].includes(check))) continue;
      const references = [...row.matchAll(/([\w./-]+(?:\.test\.(?:ts|mjs)|test_[\w]+\.py))::([^|;]+)/g)];
      expect(references.length, row).toBeGreaterThan(0);
      for (const [, path, title] of references) {
        const absolute = resolve(root, path!);
        expect(absolute.startsWith(root + "/") || absolute.startsWith(root + "\\"), row).toBe(true);
        expect(existsSync(absolute), row).toBe(true);
        const source = read(absolute);
        const label = title!.trim();
        expect(source.includes(`"${label}"`) || source.includes(`'${label}'`) || source.includes(`def ${label}(`), row).toBe(true);
      }
    }
  }
});

it("core IPC has one frontend boundary", () => {
  const violations: string[] = [];
  for (const path of sources) {
    if (path === join(root, "app/src/main/api.ts")) continue;
    const tree = ts.createSourceFile(path, read(path), ts.ScriptTarget.Latest, true);
    const hotkeys = path === join(root, "app/src/main/window-hotkeys.ts");
    const visit = (node: ts.Node): void => {
      if (ts.isImportDeclaration(node) && ts.isStringLiteral(node.moduleSpecifier) && node.moduleSpecifier.text === "@tauri-apps/api/core" && !hotkeys) violations.push(path);
      if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && node.expression.text === "invoke") {
        const arg = node.arguments[0];
        if (!hotkeys || !arg || !ts.isStringLiteral(arg) || arg.text !== "window_hotkey") violations.push(path);
      }
      if (ts.isStringLiteral(node) && node.text === "core_call") violations.push(path);
      ts.forEachChild(node, visit);
    };
    visit(tree);
  }
  expect(violations).toEqual([]);
});

it("main templates never introduce native selects", () => {
  expect(sources.filter(path => /<select(?:\s|>)/i.test(read(path)))).toEqual([]);
});

it("literal frontend RPC calls are allowed by the shell", () => {
  const shell = read(join(root, "app/src-tauri/src/commands.rs"));
  const constants = ["ALLOWED_METHODS", "ALLOWED_PREFIXES"].map(name => {
    const block = shell.match(new RegExp(`const ${name}:[^=]+?=\\s*&?\\[([\\s\\S]*?)\\];`));
    expect(block, name).not.toBeNull();
    return [...block![1]!.matchAll(/^\s*"([a-z][a-z0-9_.]+)"/gm)].map(match => match[1]!);
  });
  const [methods, prefixes] = constants;
  const rejected: string[] = [];
  let checked = 0;
  for (const path of sources) {
    const tree = ts.createSourceFile(path, read(path), ts.ScriptTarget.Latest, true);
    const visit = (node: ts.Node): void => {
      if (ts.isCallExpression(node) && ((ts.isIdentifier(node.expression) && ["core", "connectorCall"].includes(node.expression.text)) || (ts.isPropertyAccessExpression(node.expression) && node.expression.name.text === "core"))) {
        const method = node.arguments[0];
        if (method && ts.isStringLiteral(method)) {
          checked++;
          if (!methods!.includes(method.text) && !prefixes!.some(prefix => method.text.startsWith(prefix))) rejected.push(method.text);
        }
      }
      ts.forEachChild(node, visit);
    };
    visit(tree);
  }
  expect(checked).toBeGreaterThan(100);
  expect(rejected).toEqual([]);
});
