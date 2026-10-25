import { resolve } from "node:path";

export function parseOptions(args, scenes) {
  const options = { names: [], themes: ["light"], widths: [1120], skin: "default", locale: "zh-TW", out: resolve(".look"), all: false, list: false, help: false };
  for (let i = 0; i < args.length; i++) {
    const flag = args[i];
    if (["--all", "--list", "--help"].includes(flag)) { options[flag.slice(2)] = true; continue; }
    if (!flag.startsWith("-")) { options.names.push(flag); continue; }
    if (!["--theme", "--width", "--skin", "--locale", "--out"].includes(flag)) throw new Error(`Unknown option: ${flag}`);
    const value = args[++i];
    if (!value || value.startsWith("--")) throw new Error(`Missing value for ${flag}`);
    if (flag === "--theme") {
      if (!["light", "dark", "both"].includes(value)) throw new Error(`Invalid theme: ${value}`);
      options.themes = value === "both" ? ["light", "dark"] : [value];
    } else if (flag === "--width") {
      if (!/^\d+(x\d+)?(,\d+(x\d+)?)*$/.test(value)) throw new Error(`Invalid width: ${value}`);
      options.viewports = [...new Set(value.split(","))].map(size => {
        const [width, height = 900] = size.split("x").map(Number);
        if (width < 320 || width > 7680 || height < 320 || height > 7680) throw new Error(`Invalid width/height: ${size} (320..7680)`);
        return { width, height };
      });
      options.widths = [...new Set(options.viewports.map(size => size.width))];
    } else if (flag === "--locale") {
      if (!["zh-TW", "en", "ja"].includes(value)) throw new Error(`Invalid locale: ${value}`);
      options.locale = value;
    } else if (flag === "--skin") {
      if (!["default", "paper", "graphite", "lagoon", "dusk", "midnight", "meadow", "ink", "catppuccin"].includes(value)) throw new Error(`Invalid skin: ${value}`);
      options.skin = value;
    } else options.out = resolve(value);
  }
  for (const name of options.names) if (!scenes.some(scene => scene.name === name)) throw new Error(`Unknown scene: ${name}; use --list`);
  if (options.all && options.names.length) throw new Error("Invalid combination: --all and scene names");
  options.selected = options.all ? scenes : options.names.length ? options.names.map(name => scenes.find(scene => scene.name === name)) : [scenes.find(scene => scene.name === "chat")];
  return options;
}
