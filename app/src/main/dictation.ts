// How the dictation key works, and the words that explain it. The person chooses between two modes
// in the settings (device_settings.rs keeps the choice); every text in the window that tells how to
// use the key comes from here, so that none of them describes the other mode.
//   toggle  press once to start listening, press again to stop and transcribe (the default)
//   hold    hold the key while talking, let go to stop and transcribe
// The texts are plain: a page escapes them where it puts them into HTML.

import type { DeviceSettings } from "./types";
import type { Choice } from "./ui";

export type DictationMode = DeviceSettings["dictation_mode"];

/** The same as `DictationMode::default()` in device_settings.rs. */
export const DEFAULT_DICTATION_MODE: DictationMode = "toggle";

/** The segmented control of the settings page. */
export const DICTATION_MODES: Choice[] = [
  { value: "toggle", label: "按一下開始，再按一下結束" },
  { value: "hold", label: "按住說話，放開結束" },
];

export function isDictationMode(value: unknown): value is DictationMode {
  return value === "toggle" || value === "hold";
}

/** The line under 「聽寫方式」 in the settings: what the chosen mode does with the speech. */
export function modeDescription(mode: DictationMode): string {
  return mode === "toggle" ? "按一下開始聽，再按一下就轉成文字並貼上" : "按住時聽，放開就轉成文字並貼上";
}

/** The home page's hint, after the keys. */
export function homeHint(mode: DictationMode): string {
  return mode === "toggle" ? "按一下開始說話，再按一下就貼上" : "按住說話，放開就貼上";
}

/** The empty history's line; `key` is the name of the dictation key. */
export function historyEmptyHint(mode: DictationMode, key: string): string {
  return mode === "toggle" ? `按一下${key} 開始說話，結果會出現在這裡。` : `按住${key} 說話，結果會出現在這裡。`;
}

/** The first-run guide's step 4, above the try box; `keys` names the dictation keys. */
export function tryBoxText(mode: DictationMode, keys: string): string {
  const how = mode === "toggle" ? `按一下${keys} 開始說話，再按一下就會貼上整理好的文字。` : `按住${keys} 說話，放開就會貼上整理好的文字。`;
  return `把游標放在下面的框裡，${how}之後在任何程式裡都一樣。`;
}

/** The first-run guide's key line: what the dictation keys do, next to the keys. */
export function keylineAction(mode: DictationMode): string {
  return mode === "toggle" ? "按一下開始聽寫，再按一下結束" : "按住聽寫，放開結束";
}
