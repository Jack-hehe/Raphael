import { describe, expect, test } from "vitest";
import { SERVICE_ICONS, type ServiceIconId, monogram, serviceIconOf, serviceIconSVG } from "./service-icons";

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

  test("a letter stands in for the others, escaped", () => {
    expect(monogram("filesystem")).toBe("F");
    expect(monogram("  筆記")).toBe("筆");
    expect(monogram("<b>")).toBe("&lt;");
    expect(monogram("")).toBe("?");
  });
});
