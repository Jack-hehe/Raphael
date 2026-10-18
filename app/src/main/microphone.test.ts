import { afterEach, expect, it } from "vitest";
import { automaticMicrophoneLabel } from "./microphone";
import { setLocale } from "./i18n";

afterEach(() => setLocale("zh-TW"));

it("shows the microphone that automatic detection actually resolved to in each UI language", () => {
  const devices = { devices: [], selected: null, automatic_device: "Realtek Array" };
  expect(automaticMicrophoneLabel(devices)).toBe("自動偵測（目前：Realtek Array）");
  setLocale("en");
  expect(automaticMicrophoneLabel(devices)).toBe("Automatic (current: Realtek Array)");
  setLocale("ja");
  expect(automaticMicrophoneLabel(devices)).toBe("自動検出（現在：Realtek Array）");
});

it("keeps the automatic option usable before detection or with an older core", () => {
  expect(automaticMicrophoneLabel(null)).toBe("自動偵測");
  expect(automaticMicrophoneLabel({ devices: [], selected: null })).toBe("自動偵測");
  expect(automaticMicrophoneLabel({ devices: [], selected: null, automatic_device: null })).toBe("自動偵測");
});
