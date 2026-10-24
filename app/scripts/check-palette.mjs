// Report every theme token and WCAG contrast from the actual CSS, without dependencies.
import { readFileSync } from "node:fs";

const css = readFileSync(new URL("../src/main/styles.css", import.meta.url), "utf8");
const usageCss = readFileSync(new URL("../src/main/usage-analytics.css", import.meta.url), "utf8");
const overlayCss = readFileSync(new URL("../src/overlay/overlay.css", import.meta.url), "utf8");
const usageColors = [...usageCss.matchAll(/--ua-\d:\s*var\(--([\w-]+)\)/g)].map(([, token]) => token);
if (usageColors.length !== 6) throw new Error("Expected six usage chart palette colors");
const tokens = (block) => Object.fromEntries([...block.matchAll(/--([\w-]+):\s*([^;]+);/g)].map(([, key, value]) => [key, value.trim()]));
const light = tokens(css.match(/:root\s*\{([\s\S]*?)\n\}/)[1]);
const dark = { ...light, ...tokens(css.match(/:root\[data-theme="dark"\]\s*\{([\s\S]*?)\n\}/)[1]) };
const luminance = (hex) => {
  if (!/^#[0-9a-f]{6}$/i.test(hex)) throw new Error(`Unsupported contrast color: ${hex}`);
  const values = hex.slice(1).match(/../g).map((x) => parseInt(x, 16) / 255)
    .map((v) => v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4);
  return values[0] * 0.2126 + values[1] * 0.7152 + values[2] * 0.0722;
};
const ratio = (a, b) => {
  const [low, high] = [luminance(a), luminance(b)].sort((x, y) => x - y);
  const result = (high + 0.05) / (low + 0.05);
  if (!Number.isFinite(result)) throw new Error(`Invalid contrast pair: ${a}, ${b}`);
  return result;
};
const surfaces = ["side", "content", "panel", "surface-2", "menu", "btn", "key", "hover", "pressed", "selected", "accent-soft"];
const skinSource = readFileSync(new URL("../src/main/owner-settings.ts", import.meta.url), "utf8");
const skinNames = [...skinSource.match(/export const SKINS = \[([^\]]+)\]/)[1].matchAll(/"([^"]+)"/g)].map(([, name]) => name);
const skins = Object.fromEntries([...css.matchAll(/:root\[data-skin="([^"]+)"\]\[data-theme="(light|dark)"\],\s*\.skin-sample\[data-preview="\1"\]\[data-mode="\2"\]\s*\{([\s\S]*?)\n\}/g)].map(([, name, mode, block]) => [`${name}/${mode}`, tokens(block)]));
for (const name of skinNames.filter(name => name !== "default")) {
  for (const mode of ["light", "dark"]) {
    if (!skins[`${name}/${mode}`]) throw new Error(`Missing skin mode: ${name}/${mode}`);
  }
}
if (Object.keys(skins).length !== (skinNames.length - 1) * 2) throw new Error("Unexpected skin palettes");
for (const [name, palette] of Object.entries(skins)) {
  for (const token of Object.keys(tokens(css.match(/:root\[data-theme="dark"\]\s*\{([\s\S]*?)\n\}/)[1]))) {
    if (!(token in palette)) throw new Error(`Missing skin token: ${name}/${token}`);
  }
}
const report = {};
for (const [theme, palette] of Object.entries({ "default/light": light, "default/dark": dark, ...skins })) {
  const contrast = {};
  for (const [label, token] of Object.entries({ body: "text", secondary: "text-2", muted: "text-3", links: "accent-text" })) {
    const measured = surfaces.map((surface) => ({ surface, ratio: ratio(palette[token], palette[surface]) }));
    const worst = measured.reduce((a, b) => a.ratio < b.ratio ? a : b);
    contrast[label] = { foreground: palette[token], onPage: +ratio(palette[token], palette.content).toFixed(2), onPanel: +ratio(palette[token], palette.panel).toFixed(2), minimum: +worst.ratio.toFixed(2), worstSurface: worst.surface };
    if (worst.ratio < 4.5) process.exitCode = 1;
  }
  const fills = ["accent", "accent-hover", "accent-pressed"];
  const minimum = Math.min(...fills.map((fill) => ratio(palette["on-accent"], palette[fill])));
  contrast["on-accent"] = { foreground: palette["on-accent"], fill: palette.accent, ratio: +ratio(palette["on-accent"], palette.accent).toFixed(2), minimum: +minimum.toFixed(2) };
  if (minimum < 4.5) process.exitCode = 1;
  contrast.toast = { normal: +ratio(palette["toast-action"], palette.text).toFixed(2), hover: +ratio(palette["toast-action"], palette["toast-hover"]).toFixed(2) };
  if (Math.min(...Object.values(contrast.toast)) < 4.5) process.exitCode = 1;
  contrast.destructive = Object.fromEntries(["panel", "btn", "menu", "danger-hover", "danger-pressed"].map(surface => [surface, +ratio(palette.err, palette[surface]).toFixed(2)]));
  if (Object.values(contrast.destructive).some(value => value < 4.5)) process.exitCode = 1;
  // Published Catppuccin interaction colors retain the palette's lavender hue.
  for (const token of theme.startsWith("catppuccin/") ? [] : ["hover", "pressed", "hover-edge", "scroll-hover", "toast-hover"]) {
    const channels = palette[token].slice(1).match(/../g).map(value => parseInt(value, 16));
    if (Math.max(...channels) - Math.min(...channels) > 12) throw new Error(`Non-neutral interaction token: ${theme}/${token}`);
  }
  // Chart marks need 3:1 against their surface; legend text uses the ordinary text palette.
  contrast.usageCharts = Object.fromEntries(usageColors.map(token => [token, +ratio(palette[token], palette.panel).toFixed(2)]));
  if (usageColors.some(token => ratio(palette[token], palette.panel) < 3)) process.exitCode = 1;
  contrast.sendIcon = Object.fromEntries(["panel", "hover", "pressed"].map(surface => [surface, +ratio(palette["accent-icon"], palette[surface]).toFixed(2)]));
  if (Object.values(contrast.sendIcon).some(value => value < 3)) process.exitCode = 1;
  report[theme] = { tokens: palette, contrast };
}
// White labels on a translucent neutral overlay fill: a white desktop is the worst case.
const overlayHover = overlayCss.match(/\.ask-btn:hover:not\(:disabled\)\s*\{\s*background:\s*rgba\(([^)]+)\)/)[1].split(",").map(Number);
const overlayChannels = overlayHover.slice(0, 3);
if (Math.max(...overlayChannels) - Math.min(...overlayChannels) > 12) throw new Error("Non-neutral overlay hover");
const overlayOnWhite = "#" + overlayChannels.map(value => Math.round(value * overlayHover[3] + 255 * (1 - overlayHover[3])).toString(16).padStart(2, "0")).join("");
const overlayContrast = ratio("#ffffff", overlayOnWhite);
report.overlay = { hover: overlayHover, whiteTextMinimum: +overlayContrast.toFixed(2) };
if (overlayContrast < 4.5) process.exitCode = 1;
console.log(JSON.stringify(report, null, 2));
