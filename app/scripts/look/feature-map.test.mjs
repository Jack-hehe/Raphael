import { existsSync, readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { scenes } from "./scenes/index.mjs";
import { parseOptions } from "./options.mjs";

const root = resolve(import.meta.dirname, "../../..");
const deployed = resolve(root, ".agents/skills/verify-raphael");
// Protected instruction paths can be staged at the brief's explicit fallback.
const skill = existsSync(deployed) ? deployed : resolve(root, ".fold/.agents/skills/verify-raphael");
const readMap = file => {
  const staged = resolve(root, ".fold/.agents/skills/verify-raphael", file);
  return readFileSync(existsSync(staged) ? staged : resolve(skill, file), "utf8");
};
const names = ["home", "conversation", "members", "automations", "team-and-rooms", "dictation", "settings-appearance", "connections-mcp", "usage", "onboarding"];

describe("maintained verification feature map", () => {
  it("documents every requested feature with executable, current scene commands", () => {
    const index = readMap("features/README.md");
    for (const name of names) {
      expect(index).toContain(`](${name}.md)`);
      const content = readMap(`features/${name}.md`);
      for (const heading of ["Sub-features", "How to get to it (user POV)", "Driving it with look", "Gotchas"])
        expect(content, name).toContain(`## ${heading}`);
      const commands = [...content.matchAll(/^npm run look -- (.+)$/gm)];
      expect(commands.length, name).toBeGreaterThan(0);
      for (const [, command] of commands) {
        const options = parseOptions(command.trim().split(/\s+/), scenes);
        expect(options.themes, `${name}: ${command}`).toEqual(["light", "dark"]);
        const wide = ["home", "automations", "dictation", "members", "conversation", "team-and-rooms"].includes(name) || options.selected.some(scene => scene.name.startsWith("cli-account-") || scene.name.startsWith("window-hover-"));
        const allowedWidths = wide ? [[1120, 720, 1920]] : [[1120, 720], [1120, 720, 1920]];
        expect(allowedWidths, `${name}: ${command}`).toContainEqual(options.widths);
        if (options.widths.includes(1920)) expect(options.heights[1920], `${name}: ${command}`).toBe(1080);
      }
    }
    const staged = resolve(root, ".fold/.agents/skills/verify-raphael/features");
    const files = [...new Set([...readdirSync(resolve(skill, "features")), ...(existsSync(staged) ? readdirSync(staged) : [])])];
    expect(files.filter(file => file !== "README.md").sort()).toEqual(names.map(name => `${name}.md`).sort());
  });
  it("retires deliberately removed owner controls", () => {
    for (const name of ["owner-menu", "owner-keyboard-menu", "owner-photo", "home-expand", "automations-list", "automation-example"])
      expect(() => parseOptions([name], scenes)).toThrow(/Unknown/);
  });
});
