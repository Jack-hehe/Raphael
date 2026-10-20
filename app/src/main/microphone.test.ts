import { afterEach, expect, it } from "vitest";
import { automaticMicrophoneLabel, automaticMicrophoneShort, shortMicrophoneName } from "./microphone";
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

it("gives a closed dropdown the microphone's name without the driver Windows puts after it", () => {
  expect(shortMicrophoneName("麥克風排列 (Realtek(R) Audio)")).toBe("麥克風排列");
  expect(shortMicrophoneName("Headset Microphone (2- Jabra EVOLVE 20 MS)")).toBe("Headset Microphone");
  // nothing to take off, or nothing that would be left: the name stays as it is
  expect(shortMicrophoneName("USB Microphone")).toBe("USB Microphone");
  expect(shortMicrophoneName("(Realtek Audio)")).toBe("(Realtek Audio)");
  expect(shortMicrophoneName("Mic (left) side")).toBe("Mic (left) side");
});

it("says which microphone automatic detection uses, briefly, on the closed dropdown", () => {
  const devices = { devices: [], selected: null, automatic_device: "麥克風排列 (Realtek(R) Audio)" };
  expect(automaticMicrophoneShort(devices)).toBe("自動（麥克風排列）");
  setLocale("en");
  expect(automaticMicrophoneShort(devices)).toBe("Automatic (麥克風排列)");
  setLocale("ja");
  expect(automaticMicrophoneShort(devices)).toBe("自動（麥克風排列）");
  setLocale("zh-TW");
  expect(automaticMicrophoneShort(null)).toBe("自動偵測");
  expect(automaticMicrophoneShort({ devices: [], selected: null, automatic_device: null })).toBe("自動偵測");
});
