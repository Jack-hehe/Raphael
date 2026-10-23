import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "vitest";

test("both window UI stacks prefer Inter with the same CJK fallbacks", () => {
  for (const file of ["main/styles.css", "overlay/overlay.css"]) {
    const css = readFileSync(join(__dirname, file), "utf8");
    expect(css, file).toContain('--ui: "Inter", "Microsoft JhengHei UI", "Microsoft JhengHei", "PingFang TC", "Noto Sans TC", system-ui, sans-serif;');
    expect(css, file).not.toContain("Segoe UI");
    expect(css, file).toContain('@import "../assets/fonts/inter.css";');
  }
});

test("both Inter 4.1 faces ship locally with their original release bytes", () => {
  const css = readFileSync(join(__dirname, "assets", "fonts", "inter.css"), "utf8");
  const hashes = {
    "InterVariable.woff2": "693b77d4f32ee9b8bfc995589b5fad5e99adf2832738661f5402f9978429a8e3",
    "InterVariable-Italic.woff2": "e564f652916db6c139570fefb9524a77c4d48f30c92928de9db19b6b5c7a262a",
  };
  const faces = [...css.matchAll(/@font-face\s*\{([^}]+)\}/g)].map((match) => match[1]);
  for (const [file, hash] of Object.entries(hashes)) {
    const face = faces.find((rule) => rule.includes(file));
    expect(face, file).toBeDefined();
    expect(face).toMatch(/font-weight:\s*100 900/);
    expect(face).toMatch(/font-display:\s*swap/);
    expect(face).toContain(`url("./${file}")`);
    expect(face).toContain(`font-style: ${file.includes("Italic") ? "italic" : "normal"}`);
    expect(createHash("sha256").update(readFileSync(join(__dirname, "assets", "fonts", file))).digest("hex")).toBe(hash);
  }
});
