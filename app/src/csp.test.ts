/// <reference types="node" />
// What the webviews may load is written in src-tauri/tauri.conf.json (the content security policy).
// These tests read that file and the two pages, so that a change which opens the app up has to
// change a test too.

import { describe, expect, test } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";

const app = path.resolve(import.meta.dirname, "..");
const config = JSON.parse(readFileSync(path.join(app, "src-tauri", "tauri.conf.json"), "utf8"));

/** The policy as directive -> sources, whether tauri.conf.json writes it as a string or as a map. */
function policy(): Map<string, string[]> {
  const csp: unknown = config.app?.security?.csp;
  const directives = new Map<string, string[]>();
  if (typeof csp === "string") {
    for (const part of csp.split(";")) {
      const [name, ...sources] = part.trim().split(/\s+/);
      if (name) directives.set(name, sources);
    }
  } else if (csp && typeof csp === "object") {
    for (const [name, value] of Object.entries(csp)) {
      directives.set(name, Array.isArray(value) ? value.map(String) : String(value).split(/\s+/).filter(Boolean));
    }
  }
  return directives;
}

describe("content security policy", () => {
  test("the webviews have one", () => {
    expect(policy().size).toBeGreaterThan(0);
  });

  test("nothing may evaluate strings as code, and scripts are never inline", () => {
    for (const [name, sources] of policy()) {
      expect(sources, name).not.toContain("'unsafe-eval'");
    }
    const scripts = policy().get("script-src");
    expect(scripts).toBeDefined();
    expect(scripts).not.toContain("'unsafe-inline'");
  });

  test("sources are the app itself, its IPC, local data and allowed media/YouTube embeds: no wildcard", () => {
    const allowed = new Set(["'self'", "'none'", "data:", "ipc:", "http://ipc.localhost"]);
    const frameHosts = new Set(["'self'", "https://www.youtube-nocookie.com", "https://www.youtube.com"]);
    for (const [name, sources] of policy()) {
      for (const source of sources) {
        if (source === "'unsafe-inline'") {
          expect(name, "'unsafe-inline' is for style attributes only").toBe("style-src");
        } else if (name === "frame-src") {
          expect(frameHosts, `${name} ${source}`).toContain(source);
        } else if ((name === "img-src" || name === "media-src") && source === "https:") {
          expect(source).toBe("https:");
        } else {
          expect(allowed, `${name} ${source}`).toContain(source);
        }
      }
    }
    expect(policy().get("frame-src")).toEqual(
      expect.arrayContaining(["https://www.youtube-nocookie.com", "https://www.youtube.com"]),
    );
  });

  test("plugins, objects, base tags, framing and forms are shut", () => {
    const directives = policy();
    expect(directives.get("default-src")).toEqual(["'self'"]);
    for (const name of ["object-src", "base-uri", "frame-ancestors", "form-action"]) {
      expect(directives.get(name), name).toEqual(["'none'"]);
    }
  });

  test("the IPC is reachable: the custom protocol on Windows and elsewhere", () => {
    expect(policy().get("connect-src")).toEqual(expect.arrayContaining(["ipc:", "http://ipc.localhost"]));
  });

  // The pages write style="..." attributes into the HTML they build, which needs 'unsafe-inline'
  // in style-src. Tauri puts a nonce in style-src for an HTML file that has a <style> element,
  // and a browser ignores 'unsafe-inline' next to a nonce: the main window's styles would go.
  test("index.html has no <style> element and no inline script", () => {
    const html = readFileSync(path.join(app, "index.html"), "utf8");
    expect(html).not.toMatch(/<style[\s>]/i);
    expect(html).not.toMatch(/<script(?![^>]*\ssrc=)[^>]*>/i);
  });

  test("the overlay page has no inline script and no style attribute (its <style> gets a nonce from Tauri)", () => {
    const html = readFileSync(path.join(app, "overlay.html"), "utf8");
    expect(html).not.toMatch(/<script(?![^>]*\ssrc=)[^>]*>/i);
    expect(html.replace(/<style[\s\S]*?<\/style>/gi, "")).not.toMatch(/\sstyle\s*=/i);
  });
});
