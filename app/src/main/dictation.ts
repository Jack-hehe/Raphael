// How the dictation key works, and the words that explain it. The person chooses between two modes
// in the settings (device_settings.rs keeps the choice); every text in the window that tells how to
// use the key comes from here, so that none of them describes the other mode.
//   toggle  press once to start listening, press again to stop and transcribe (the default)
//   hold    hold the key while talking, let go to stop and transcribe
// The texts are plain: a page escapes them where it puts them into HTML.

import { t } from "./i18n";

import type { DeviceSettings } from "./types";
import type { Choice } from "./ui";

export type DictationMode = DeviceSettings["dictation_mode"];

/** The menu of the settings page: a short name, and what the mode does under it. */
export const DICTATION_MODES: Choice[] = [
  { value: "toggle", get label() { return t("dictation.001"); }, get desc() { return modeDescription("toggle"); } },
  { value: "hold", get label() { return t("dictation.002"); }, get desc() { return modeDescription("hold"); } },
];

export function isDictationMode(value: unknown): value is DictationMode {
  return value === "toggle" || value === "hold";
}

/** The line under 「聽寫方式」 in the settings: what the chosen mode does with the speech. */
export function modeDescription(mode: DictationMode): string {
  return mode === "toggle" ? t("dictation.003") : t("dictation.004");
}

/** The home page's hint, after the keys. */
export function homeHint(mode: DictationMode): string {
  return mode === "toggle" ? t("dictation.005") : t("dictation.006");
}

/** The empty history's line; `key` is the name of the dictation key. */
export function historyEmptyHint(mode: DictationMode, key: string): string {
  return mode === "toggle" ? t("dictation.007", { p0: key }) : t("dictation.008", { p0: key });
}
