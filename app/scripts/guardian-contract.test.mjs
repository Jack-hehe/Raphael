import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, it } from "vitest";
import { coreCheck } from "./guardian.mjs";
import { scenes } from "./look/scenes/index.mjs";

const root = resolve(import.meta.dirname, "../..");
const read = path => readFileSync(resolve(root, path), "utf8");

it("CI enforces the same offline layers and real three-size UI for every PR", () => {
  const ci = read(".github/workflows/ci.yml");
  for (const command of ["npm run verify", "npm run look -- --all --theme both --width 1120,720,1920x1080", "pytest -q", "cargo test --locked", "npm run judge", "actions/upload-artifact", "actions/cache/restore", "actions/cache/save", "merge_group:", "Guardian required gates"])
    expect(ci).toContain(command);
  expect(ci).not.toContain("paths-ignore:");
  expect(ci).toContain("out true true true");
  expect(ci).toContain("Design judge SKIP");
  expect(ci).toContain("--max-cost-usd");
  expect(ci).toContain("secrets.OPENROUTER_API_KEY");
  expect(ci).toContain("needs: [changes, frontend, python, windows-shell]");
  const nightly = read(".github/workflows/guardian-live.yml");
  expect(nightly).toContain("--api-only");
  expect(nightly).toContain("--max-turns 120");
  expect(nightly).toContain("Live API matrix SKIP");
});

it("portable guard commands use the installed core environment without shell interpolation", () => {
  for (const kind of ["live", "judge"]) {
    const check = coreCheck(kind, kind === "judge" ? ["C:/an evidence path/run"] : []);
    expect(check.cwd).toBe(resolve(root, "core"));
    expect(check.args).toContain(kind === "live" ? "raphael_core.evaluate.guardian" : "raphael_core.evaluate.design_judge");
    expect(check.args).not.toContain("sync");
  }
});

it("every captured scene has feature context for the judge", () => {
  const names = ["home", "conversation", "members", "automations", "team-and-rooms", "dictation", "settings-appearance", "connections-mcp", "usage", "onboarding"];
  const mapped = new Set(names.flatMap(name => [...read(`.agents/skills/verify-raphael/features/${name}.md`).matchAll(/^npm run look -- (.+)$/gm)].flatMap(([, command]) => command.split(/\s+/))));
  expect(scenes.filter(scene => !mapped.has(scene.name)).map(scene => scene.name)).toEqual([]);
});

it("the hand-off contract requires rule IDs and evidence without claiming unrun acceptance", () => {
  const template = read(".github/pull_request_template.md");
  for (const text of ["RULES IDs", "ARCHITECTURE IDs", "Judge report", "images inspected", "Unverified", "Live brain matrix"]) expect(template).toContain(text);
  const staged = ".fold/.agents/skills/verify-raphael/SKILL.md";
  const spec = existsSync(resolve(root, staged)) ? staged : ".agents/skills/verify-raphael/SKILL.md";
  expect(existsSync(resolve(root, spec))).toBe(true);
  expect(read(spec)).toContain("after look and before UI hand-off");
});
