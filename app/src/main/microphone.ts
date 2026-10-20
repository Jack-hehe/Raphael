import { t } from "./i18n";
import type { AudioDevices } from "./types";

/** Older cores omit the resolved name; the automatic option remains available. */
export function automaticMicrophoneLabel(devices: AudioDevices | null): string {
  return devices?.automatic_device
    ? t("mic.automaticCurrent", { p0: devices.automatic_device })
    : t("onboarding.005");
}

/**
 * A microphone's name without the driver Windows puts after it in brackets: 「麥克風排列 (Realtek(R)
 * Audio)」 is 「麥克風排列」. It is what a closed dropdown has room for; the list and the tooltip keep
 * the whole name, which tells two microphones of the same kind apart. A name that is all brackets,
 * or has none, stays as it is.
 */
export function shortMicrophoneName(name: string): string {
  const open = name.indexOf(" (");
  return open > 0 && name.trimEnd().endsWith(")") ? name.slice(0, open).trim() : name;
}

/** The automatic choice on a closed dropdown: 「自動（麥克風排列）」, or just 「自動偵測」 before detection or with an older core. */
export function automaticMicrophoneShort(devices: AudioDevices | null): string {
  return devices?.automatic_device
    ? t("mic.automaticShort", { p0: shortMicrophoneName(devices.automatic_device) })
    : t("onboarding.005");
}
