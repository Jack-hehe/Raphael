import { describe, expect, it } from "vitest";
import { assertNoPrivateConfiguration, isReleaseBuild, withPrivateConfig } from "./tauri.mjs";

const config = "src-tauri/tauri.private.conf.json";
const release = "src-tauri/tauri.release.conf.json";
// An empty environment: the suite itself may run on CI, where every build is public.
const withConfig = (args, privateConfig) => withPrivateConfig(args, privateConfig, {});

describe("withPrivateConfig", () => {
  it.each(["dev", "build"])("adds the private config to %s", (command) => {
    expect(withConfig([command, "--verbose"], config)).toEqual([
      command, "--verbose", "--config", config,
    ]);
  });

  it.each(["dev", "build"])("leaves %s unchanged without a private config", (command) => {
    const args = [command, "--verbose"];
    expect(withConfig(args, null)).toEqual(args);
  });

  it.each([[], ["info"], ["icon", "dev"], ["android", "build"], ["--version"]])(
    "leaves other commands unchanged: %j",
    (...args) => {
      expect(withConfig(args, config)).toEqual(args);
    },
  );

  it("preserves global flags and existing config arguments", () => {
    const args = ["-vv", "build", "--config", "custom config.json", "--no-bundle"];
    expect(withConfig(args, config)).toEqual([
      ...args, "--config", config,
    ]);
  });

  it("inserts the config before arguments forwarded to cargo or the app", () => {
    expect(withConfig(["dev", "--", "--example", "hello"], config)).toEqual([
      "dev", "--config", config, "--", "--example", "hello",
    ]);
  });

  it("never adds the private config to a release build, whatever it is given", () => {
    const args = ["build", "--config", release, "--bundles", "nsis"];
    expect(withConfig(args, config)).toEqual(args);
    expect(withPrivateConfig(["build"], config, { CI: "true" })).toEqual(["build"]);
  });
});

describe("isReleaseBuild", () => {
  it.each([
    [["build", "--config", release]],
    [["build", "--config", "src-tauri\\tauri.release.conf.json"]],
    [["build", `--config=${release}`]],
    [["build", "-c", release]],
    [["build", `-c${release}`]],
    [["--verbose", "build", "--bundles", "nsis", "--config", "tauri.release.conf.json", "--ci"]],
  ])("recognises the release config: %j", (args) => {
    expect(isReleaseBuild(args, {})).toBe(true);
  });

  it.each([
    [["build"]],
    [["dev"]],
    [["build", "--config", "custom.json"]],
    [["build", "--config", "src-tauri/tauri.release.conf.json.bak"]],
    [["build", "--", "--config", release]],
    [["build", "--ci"]],
  ])("does not mistake other builds for a release: %j", (args) => {
    expect(isReleaseBuild(args, {})).toBe(false);
  });

  it.each([
    [{ CI: "true" }, true], [{ CI: "1" }, true], [{ CI: "yes" }, true],
    [{ CI: "false" }, false], [{ CI: "FALSE" }, false], [{ CI: "0" }, false], [{ CI: "" }, false], [{}, false],
    [{ RAPHAEL_PUBLIC_BUILD: "1" }, true], [{ RAPHAEL_PUBLIC_BUILD: "0" }, false],
  ])("treats the environment %j as public: %s", (env, expected) => {
    expect(isReleaseBuild(["build"], env)).toBe(expected);
  });
});

describe("assertNoPrivateConfiguration", () => {
  it("accepts the release configuration", () => {
    expect(() => assertNoPrivateConfiguration(["build", "--config", release, "--", "--locked"], {})).not.toThrow();
  });

  it.each([
    [["build", "--config", config]],
    [["build", "--config", "../assets-private/override.json"]],
    [["build", "--config", "src-tauri\\icons-private\\icon.json"]],
    [["build", "--config", "public/private/x"]],
  ])("refuses arguments that name private files: %j", (args) => {
    expect(() => assertNoPrivateConfiguration(args, {})).toThrow("private artwork");
  });

  it("refuses a TAURI_CONFIG that bundles private files", () => {
    const env = { TAURI_CONFIG: JSON.stringify({ bundle: { icon: ["icons-private/icon.ico"] } }) };
    expect(() => assertNoPrivateConfiguration(["build"], env)).toThrow("private artwork");
    expect(() => assertNoPrivateConfiguration(["build"], { TAURI_CONFIG: "{\"productName\":\"Raphael\"}" })).not.toThrow();
  });
});
