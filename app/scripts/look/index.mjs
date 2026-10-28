import { scenes } from "./scenes/index.mjs";
import { parseOptions } from "./options.mjs";

try {
  const options = parseOptions(process.argv.slice(2), scenes);
  if (options.help) console.log("npm run look -- <scene...> [--all|--list] [--theme light|dark|both] [--width 1120,720,1920x1080] [--skin id] [--locale zh-TW|en|ja] [--out dir]");
  else if (options.list) for (const scene of scenes) console.log(`${scene.name.padEnd(30)} ${scene.description}`);
  else {
    const { run } = await import("./runner.mjs");
    process.exitCode = await run(options);
  }
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
