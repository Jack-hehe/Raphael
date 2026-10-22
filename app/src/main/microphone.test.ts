import { afterEach, expect, it } from "vitest";
import {
  DROPDOWN_ROOM,
  MENU_ROOM,
  automaticMicrophoneLabel,
  automaticMicrophoneShort,
  cutToFit,
  microphoneText,
  shortMicrophoneName,
} from "./microphone";
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

it("names a plain microphone by its device, without the number Windows puts before a second one", () => {
  expect(shortMicrophoneName("麥克風 (5- HyperX Cloud II Core Wireless)")).toBe("HyperX Cloud II Core Wireless");
  expect(shortMicrophoneName("Microphone (Logitech BRIO)")).toBe("Logitech BRIO");
  expect(shortMicrophoneName("マイク (2- USB Audio Device)")).toBe("USB Audio Device");
  expect(shortMicrophoneName("麦克风 (Realtek(R) Audio)")).toBe("Realtek(R) Audio");
});

it("ends a device MME cut short with an ellipsis, and keeps a kind that tells the microphone apart", () => {
  // MME names are at most 31 characters: the brackets of a longer one never close.
  expect(shortMicrophoneName("麥克風 (5- HyperX Cloud II Core Wi")).toBe("HyperX Cloud II Core Wi…");
  expect(shortMicrophoneName("Microphone Array (Realtek(R) Au")).toBe("Microphone Array");
  expect(shortMicrophoneName("麥克風 (5- ")).toBe("麥克風 (5- ");
});

it("cuts a text to its room after a whole word, a wide character taking two columns", () => {
  expect(cutToFit("Logitech BRIO", 26)).toBe("Logitech BRIO");
  expect(cutToFit("HyperX Cloud II Core Wireless", 26)).toBe("HyperX Cloud II Core…");
  // a name that ends in an ellipsis already does not get a second one
  expect(cutToFit("HyperX Cloud II Core Wi…", 18)).toBe("HyperX Cloud II…");
  expect(cutToFit("麥克風排列", 10)).toBe("麥克風排列");
  expect(cutToFit("麥克風排列", 9)).toBe("麥克風排…");
  // one long word is cut inside it
  expect(cutToFit("SteelSeriesArctisNova7Wireless", 12)).toBe("SteelSeries…");
});

it("puts a microphone's name in a text as it fits the room of the whole text", () => {
  expect(microphoneText("mic.disconnected", "USB Mic (C-Media)")).toBe("USB Mic (C-Media)（未連接）");
  expect(microphoneText("mic.disconnected", "USB Mic (C-Media)", DROPDOWN_ROOM)).toBe("USB Mic（未連接）");
  expect(microphoneText("mic.disconnected", "麥克風 (5- HyperX Cloud II Core Wireless)", DROPDOWN_ROOM)).toBe("HyperX Cloud II…（未連接）");
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

it("fits a long automatic microphone in the closed dropdown and the menu, keeping the whole name for the tooltip", () => {
  const devices = { devices: [], selected: null, automatic_device: "麥克風 (5- HyperX Cloud II Core Wi" };
  expect(automaticMicrophoneShort(devices)).toBe("自動（HyperX Cloud II…）");
  expect(automaticMicrophoneLabel(devices, MENU_ROOM)).toBe("自動偵測（目前：HyperX Cloud II Core Wi…）");
  expect(automaticMicrophoneLabel(devices)).toBe("自動偵測（目前：麥克風 (5- HyperX Cloud II Core Wi）");
  setLocale("en");
  expect(automaticMicrophoneShort(devices)).toBe("Automatic (HyperX Cloud…)");
  expect(automaticMicrophoneLabel(devices, MENU_ROOM)).toBe("Automatic (current: HyperX Cloud II Core…)");
  setLocale("ja");
  expect(automaticMicrophoneShort(devices)).toBe("自動（HyperX Cloud II…）");
});
