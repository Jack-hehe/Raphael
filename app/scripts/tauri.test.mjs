import { describe, expect, it } from "vitest";
import { withPrivateConfig } from "./tauri.mjs";

const config = "src-tauri/tauri.private.conf.json";

describe("withPrivateConfig", () => {
  it.each(["dev", "build"])("adds the private config to %s", (command) => {
    expect(withPrivateConfig([command, "--verbose"], config)).toEqual([
      command, "--verbose", "--config", config,
    ]);
  });

  it.each(["dev", "build"])("leaves %s unchanged without a private config", (command) => {
    const args = [command, "--verbose"];
    expect(withPrivateConfig(args, null)).toEqual(args);
  });

  it.each([[], ["info"], ["icon", "dev"], ["android", "build"], ["--version"]])(
    "leaves other commands unchanged: %j",
    (...args) => {
      expect(withPrivateConfig(args, config)).toEqual(args);
    },
  );

  it("preserves global flags and existing config arguments", () => {
    const args = ["-vv", "build", "--config", "custom config.json", "--no-bundle"];
    expect(withPrivateConfig(args, config)).toEqual([
      ...args, "--config", config,
    ]);
  });

  it("inserts the config before arguments forwarded to cargo or the app", () => {
    expect(withPrivateConfig(["dev", "--", "--example", "hello"], config)).toEqual([
      "dev", "--config", config, "--", "--example", "hello",
    ]);
  });
});
