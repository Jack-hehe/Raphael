/// <reference types="node" />
import { readFileSync } from "node:fs";
import { describe, expect, test } from "vitest";
import { readdirSync } from "node:fs";
import { BRAND_COLORS, LOGO_IDS, SERVICE_ICONS, brandStyle, hasLogo, monogram, serviceIconOf, serviceIconSVG, serviceLogoHTML } from "./service-icons";
import { iconHTML } from "./connections";
import { ico } from "./ui";

test("reserved provider brands with no installed artwork show the plug instead of a broken image or initial", () => {
  for (const id of ["openai", "azure", "litellm", "portkey", "nebius", "bifrost", "groq", "anthropic", "googlegemini", "googlecloud", "ollama", "vercel", "lmstudio", "deepseek", "mistral"] as const) {
    expect(LOGO_IDS).toContain(id);
    const html = iconHTML(id, id, "md");
    expect(html).not.toContain('src=""');
    if (!hasLogo(id)) expect(html).toContain(ico("plug"));
  }
});

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
    [{ name: "OpenRouter" }, "openrouter"],
    [{ name: "Brave Search" }, "brave"],
    [{ name: "SearXNG" }, "searxng"],
    [{ name: "Tavily" }, "tavily"],
    [{ name: "Jina AI" }, "jina"],
    [{ name: "Firecrawl" }, "firecrawl"],
    [{ name: "Fish Audio" }, "fish"],
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
    for (const id of Object.keys(SERVICE_ICONS) as (keyof typeof SERVICE_ICONS)[]) {
      const svg = serviceIconSVG(id);
      expect(svg).toContain('fill="currentColor"');
      expect(svg).toContain('aria-hidden="true"');
      expect(svg).not.toMatch(/#[0-9a-f]{3,6}|rgb\(/i);
      expect(SERVICE_ICONS[id].path).toMatch(/^[Mm][\d. ,\-a-zA-Z]+$/);
    }
  });

  test("a brand with artwork of its own is found by its name or its address", () => {
    expect(serviceIconOf({ name: "x", url: "https://mcp.monday.com/mcp" })).toBe("monday");
    expect(serviceIconOf({ name: "x", url: "https://huggingface.co/mcp" })).toBe("huggingface");
    expect(serviceIconOf({ name: "x", url: "https://mcp.webflow.com/mcp" })).toBe("webflow");
  });

  test("plain words that are also a brand do not pick a picture for a program of the user's own", () => {
    for (const name of ["close", "ramp", "jam", "gamma", "exa"]) expect(serviceIconOf({ name })).toBeNull();
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
    for (const id of ["notion", "github", "spotify", "obsidian", "atlassian", "cloudflare", "sentry", "paypal", "telegram"] as const) expect(BRAND_COLORS[id]).toBeUndefined();
  });

  test("the brand colour is given to the wrapper as a variable, and only for the marks that have one", () => {
    expect(brandStyle("linear")).toBe(' style="--brand:#5E6AD2"');
    expect(brandStyle("notion")).toBe("");
  });

  test("a paired person's avatar can keep an escaped initial", () => {
    expect(monogram("filesystem")).toBe("F");
    expect(monogram("  筆記")).toBe("筆");
    expect(monogram("<b>")).toBe("&lt;");
    expect(monogram("")).toBe("?");
  });

  test("an official full-colour logo is a picture file of its own, with the brand's own colours and no tile of ours", () => {
    expect(hasLogo("monday")).toBe(true);
    expect(hasLogo("notion")).toBe(false);
    const html = serviceLogoHTML("monday");
    expect(html).toMatch(/^<img class="logo lg-l" src="data:image\/png;base64,/);
    expect(html).toContain('alt=""');
    expect(html).not.toContain("lg-d");
  });

  test("a logo that is dark in its official form has its own light version, shown on the dark theme", () => {
    const html = serviceLogoHTML("attio");
    expect(html).toMatch(/class="logo lg-l" src="data:image\/svg\+xml/);
    expect(html).toMatch(/class="logo lg-d" src="data:image\/svg\+xml/);
  });

  test("Fish Audio uses its official PNG artwork instead of an unattributed vector", () => {
    expect(hasLogo("fish")).toBe(true);
    expect(SERVICE_ICONS).not.toHaveProperty("fish");
    expect(serviceLogoHTML("fish")).toMatch(/^<img class="logo lg-l" src="data:image\/png;base64,/);
    const sources = JSON.parse(readFileSync("src/main/service-logos/sources.json", "utf8"));
    expect(sources.fish).toMatchObject({ file: "fish.png", url: "https://fish.audio/apple-touch-icon.png", page: "https://fish.audio/" });
  });

  test("release credits name all the added service marks and their source groups", () => {
    const notice = readFileSync("scripts/release-licenses/brand-logos-NOTICE.txt", "utf8");
    const manifest = JSON.parse(readFileSync("scripts/release-licenses/manifest.json", "utf8"));
    const icons = manifest.components.find((entry: { name: string }) => entry.name === "Simple Icons");
    const logos = manifest.components.find((entry: { name: string }) => entry.name === "Brand logos");
    for (const name of ["OpenRouter", "Brave", "SearXNG"]) {
      expect(notice).toContain(name);
      expect(icons.usage).toContain(name);
    }
    for (const name of ["Tavily", "Jina", "Firecrawl", "Fish Audio"]) {
      expect(notice).toContain(name);
      expect(logos.usage).toContain(name);
    }
  });

  test("the logo files and the list of ids are the same, and each has its source written down", () => {
    const files = readdirSync("src/main/service-logos");
    const ids = new Set(files.filter((f) => /\.(svg|png)$/.test(f)).map((f) => f.replace(/\.dark\.svg$|\.(svg|png)$/, "")));
    const installed = LOGO_IDS.filter(hasLogo);
    expect([...ids].sort()).toEqual([...installed].sort());
    const sources = JSON.parse(readFileSync("src/main/service-logos/sources.json", "utf8")) as Record<string, { file: string; url: string }>;
    expect(Object.keys(sources).sort()).toEqual([...installed].sort());
    for (const [id, source] of Object.entries(sources)) {
      expect(source.url, id).toMatch(/^https:\/\//);
      expect(files, id).toContain(source.file);
    }
  });

  test("a logo file is plain drawing: no script, no outside address, nothing that runs", () => {
    for (const file of readdirSync("src/main/service-logos").filter((f) => f.endsWith(".svg"))) {
      const svg = readFileSync(`src/main/service-logos/${file}`, "utf8");
      expect(svg, file).toMatch(/^\s*(<\?xml[^>]*>\s*)?<svg\b/);
      expect(svg, file).toContain("viewBox");
      expect(svg, file).not.toMatch(/<script|<foreignObject|<image|href=|\son\w+=|@import|url\(\s*["']?https?:/i);
    }
  });
});
