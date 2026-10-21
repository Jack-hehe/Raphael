/// <reference types="node" />
import { readFileSync } from "node:fs";
import { describe, expect, test } from "vitest";
import { BRAND_COLORS, SERVICE_ICONS, type ServiceIconId, brandStyle, monogram, serviceIconOf, serviceIconSVG } from "./service-icons";

describe("the marks of well-known programs", () => {
  test.each([
    [{ name: "Notion" }, "notion"],
    [{ name: "docs", command: "npx", args: ["-y", "@notionhq/notion-mcp-server"] }, "notion"],
    [{ name: "x", url: "https://mcp.notion.com/mcp" }, "notion"],
    [{ name: "code", command: "npx", args: ["@modelcontextprotocol/server-github"] }, "github"],
    [{ name: "Google Drive" }, "googledrive"],
    [{ name: "files", command: "npx", args: ["-y", "@modelcontextprotocol/server-gdrive"] }, "googledrive"],
    [{ name: "mail", url: "https://gmail.googleapis.com/mcp" }, "gmail"],
    [{ name: "Google Calendar" }, "googlecalendar"],
    [{ name: "cal", url: "https://calendar.google.com/mcp" }, "googlecalendar"],
    [{ name: "Linear", url: "https://mcp.linear.app/sse" }, "linear"],
    [{ name: "design", command: "npx", args: ["figma-developer-mcp"] }, "figma"],
    [{ name: "Spotify" }, "spotify"],
    [{ name: "chat", command: "uvx", args: ["mcp-discord"] }, "discord"],
    [{ name: "tasks", command: "npx", args: ["todoist-mcp"] }, "todoist"],
    [{ name: "vault", command: "npx", args: ["obsidian-mcp"] }, "obsidian"],
    [{ name: "x", url: "https://mcp.atlassian.com/v1/mcp" }, "atlassian"],
    [{ name: "Jira" }, "atlassian"],
    [{ name: "x", url: "https://mcp.cloudflare.com/mcp" }, "cloudflare"],
    [{ name: "x", url: "https://mcp.sentry.dev/mcp" }, "sentry"],
    [{ name: "x", url: "https://mcp.stripe.com" }, "stripe"],
    [{ name: "x", url: "https://mcp.zapier.com/api/mcp/mcp" }, "zapier"],
    [{ name: "x", url: "https://mcp.paypal.com/mcp" }, "paypal"],
  ] as const)("%j is %s", (hints, id) => {
    expect(serviceIconOf(hints)).toBe(id);
  });

  test("a program that is none of them has none, and a part of a word is not the word", () => {
    expect(serviceIconOf({ name: "filesystem", command: "npx", args: ["-y", "@modelcontextprotocol/server-filesystem"] })).toBeNull();
    expect(serviceIconOf({ name: "Notes", command: "npx" })).toBeNull();
    expect(serviceIconOf({ name: "Notionally" })).toBeNull();
    expect(serviceIconOf({ name: "calendar" })).toBeNull(); // not Google's, as far as it says
    expect(serviceIconOf({ name: "drive", url: "not an address" })).toBeNull();
    expect(serviceIconOf({ name: "筆記" })).toBeNull();
  });

  test("every mark is one drawn path in the colour of the text, with no colour of its own", () => {
    for (const id of Object.keys(SERVICE_ICONS) as ServiceIconId[]) {
      const svg = serviceIconSVG(id);
      expect(svg).toContain('fill="currentColor"');
      expect(svg).toContain('aria-hidden="true"');
      expect(svg).not.toMatch(/#[0-9a-f]{3,6}|rgb\(/i);
      expect(SERVICE_ICONS[id].path).toMatch(/^M[\d. ,\-a-zA-Z]+$/);
    }
  });

  test("Canva is not in the set and gets its letter", () => {
    expect(serviceIconOf({ name: "Canva", url: "https://mcp.canva.com/mcp" })).toBeNull();
  });

  test("a brand colour is kept only where it reads on every surface of both themes", () => {
    const css = readFileSync("src/main/styles.css", "utf8");
    const token = (block: string, name: string): string => new RegExp(`${name}:\\s*(#[0-9a-fA-F]{6})`).exec(block)![1];
    const light = css.slice(css.indexOf(":root {"), css.indexOf(':root[data-theme="dark"]'));
    const dark = css.slice(css.indexOf(':root[data-theme="dark"]'));
    const surfaces = [token(light, "--content"), token(light, "--panel"), token(dark, "--content"), token(dark, "--panel")];
    const luminance = (hex: string): number => {
      const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
      return 0.2126 * r + 0.7152 * g + 0.0722 * b;
    };
    const ratio = (a: string, b: string): number => {
      const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
      return (hi + 0.05) / (lo + 0.05);
    };
    expect(Object.keys(BRAND_COLORS).length).toBeGreaterThan(5);
    for (const [id, colour] of Object.entries(BRAND_COLORS)) {
      expect(id in SERVICE_ICONS).toBe(true);
      for (const surface of surfaces) expect(ratio(colour, surface), `${id} on ${surface}`).toBeGreaterThanOrEqual(3);
    }
    // the ones that would not read are left to the colour of the text
    for (const id of ["notion", "github", "spotify", "obsidian", "atlassian", "cloudflare", "sentry", "paypal"] as const) expect(BRAND_COLORS[id]).toBeUndefined();
  });

  test("the brand colour is given to the wrapper as a variable, and only for the marks that have one", () => {
    expect(brandStyle("linear")).toBe(' style="--brand:#5E6AD2"');
    expect(brandStyle("notion")).toBe("");
  });

  test("a letter stands in for the others, escaped", () => {
    expect(monogram("filesystem")).toBe("F");
    expect(monogram("  筆記")).toBe("筆");
    expect(monogram("<b>")).toBe("&lt;");
    expect(monogram("")).toBe("?");
  });
});
