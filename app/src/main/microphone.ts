import { t } from "./i18n";
import type { AudioDevices } from "./types";

/** Older cores omit the resolved name; the automatic option remains available. */
export function automaticMicrophoneLabel(devices: AudioDevices | null): string {
  return devices?.automatic_device
    ? t("mic.automaticCurrent", { p0: devices.automatic_device })
    : t("onboarding.005");
}
