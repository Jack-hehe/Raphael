// The setting 「說話時暫停播放中的音樂和影片」: while a dictation or a question is recorded, the music and
// video that play on this computer are paused, and played again when the recording ends. Raphael's
// shell does it (media/ in src-tauri; device_settings.rs keeps the choice, on by default). A change
// applies from the next recording.

import { t } from "./i18n";
import type { DeviceSettings } from "./types";
import { row, tg } from "./ui";

/** The `data-k` of the switch, by which the settings page knows a click on it. */
export const PAUSE_MEDIA_KEY = "pause-media";

/** The row of the dictation tab's audio section. */
export function pauseMediaRow(on: boolean): string {
  return row(t("settings.pauseMedia"), t("settings.pauseMediaDesc"), tg(on, t("settings.pauseMedia"), ` data-k="${PAUSE_MEDIA_KEY}"`));
}

/** What a click on the switch changes: the opposite of what is set now, and nothing else. */
export function pauseMediaToggle(device: Pick<DeviceSettings, "pause_media">): Pick<DeviceSettings, "pause_media"> {
  return { pause_media: !device.pause_media };
}
