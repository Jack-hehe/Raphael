import { t } from "./i18n";
import type { AudioDevices } from "./types";
import type { Choice } from "./ui";

// The word Windows names a plain microphone by in its display languages. A name that is only this
// word says nothing about which microphone it is; the device in brackets after it does.
const PLAIN_MICROPHONE = /^(?:microphone|麥克風|麦克风|マイク|마이크)$/i;

/**
 * A microphone's name as a closed dropdown has room for. Windows names a microphone 「what it is (the
 * device)」. 「麥克風排列 (Realtek(R) Audio)」 is 「麥克風排列」: the part before the brackets tells it
 * apart. 「麥克風 (5- HyperX Cloud II Core Wireless)」 is 「HyperX Cloud II Core Wireless」: every
 * microphone is a 麥克風, so the device does, without the number Windows puts before a second one of
 * the same kind. MME cuts names at 31 characters, which can leave the brackets open; what is left of
 * the device then ends in "…". The list and the tooltip keep the whole name, which tells two
 * microphones of the same kind apart. A name that is all brackets, or has none, stays as it is.
 */
export function shortMicrophoneName(name: string): string {
  const whole = name.trimEnd();
  const open = whole.indexOf(" (");
  if (open <= 0) return name;
  // Where the brackets close: at the end, or never when MME cut the name. Brackets that close
  // before the end ("Mic (left) side") are part of the name, not a device after it.
  let depth = 0;
  let close = -1;
  for (let i = open + 1; i < whole.length && close < 0; i++) {
    if (whole[i] === "(") depth++;
    else if (whole[i] === ")" && --depth === 0) close = i;
  }
  if (close >= 0 && close !== whole.length - 1) return name;
  const kind = whole.slice(0, open).trim();
  if (!PLAIN_MICROPHONE.test(kind)) return kind;
  const device = whole.slice(open + 2, close >= 0 ? close : undefined).replace(/^\d+-\s*/, "").trim();
  if (!device) return name;
  return close >= 0 ? device : `${device}…`;
}

// Characters that take two columns: CJK, kana, hangul and the full-width forms.
const WIDE = /[ᄀ-ᅟ⺀-꓏가-힣豈-﫿︰-﹏＀-｠￠-￦]/;
const columns = (text: string): number => Array.from(text).reduce((n, c) => n + (WIDE.test(c) ? 2 : 1), 0);

/**
 * `text` in at most `room` columns, a wide character taking two: cut after the last whole word that
 * fits, or inside the first word when none does, with "…" after it.
 */
export function cutToFit(text: string, room: number): string {
  if (columns(text) <= room) return text;
  const fits = (s: string): boolean => columns(s) <= room - 1;
  let kept = "";
  for (const word of text.replace(/…$/, "").split(" ")) {
    const next = kept ? `${kept} ${word}` : word;
    if (!fits(next)) break;
    kept = next;
  }
  if (!kept) {
    for (const c of Array.from(text)) {
      if (!fits(kept + c)) break;
      kept += c;
    }
  }
  return `${kept}…`;
}

/** What a closed microphone dropdown has room for, in columns: its width less its arrow. */
export const DROPDOWN_ROOM = 26;
/** What a menu has room for at its widest (styles.css), less its check mark. */
export const MENU_ROOM = 44;

/** The texts with a microphone's name in them. */
type MicrophoneKey = "mic.automaticCurrent" | "mic.automaticShort" | "mic.disconnected";

/**
 * The text of `key` with the microphone `name` in it as {p0}: the whole name, or, given the `room` of
 * the whole text in columns, the short name (`shortMicrophoneName`) cut to what the rest leaves.
 */
export function microphoneText(key: MicrophoneKey, name: string, room?: number): string {
  if (room === undefined) return t(key, { p0: name });
  return t(key, { p0: cutToFit(shortMicrophoneName(name), room - columns(t(key, { p0: "" }))) });
}

/**
 * The automatic choice: 「自動偵測（目前：麥克風排列 (Realtek(R) Audio)）」, with the microphone detection
 * resolved to, or just 「自動偵測」 before detection, when there is none yet. Given a
 * `room`, the microphone goes by its short name, cut to fit.
 */
export function automaticMicrophoneLabel(devices: AudioDevices | null, room?: number): string {
  return devices?.automatic_device ? microphoneText("mic.automaticCurrent", devices.automatic_device, room) : t("mic.autoDetect");
}

/** The automatic choice on a closed dropdown: 「自動（HyperX Cloud II…）」, or just 「自動偵測」 before detection. */
export function automaticMicrophoneShort(devices: AudioDevices | null): string {
  return devices?.automatic_device ? microphoneText("mic.automaticShort", devices.automatic_device, DROPDOWN_ROOM) : t("mic.autoDetect");
}

/** The value of the automatic choice in a microphone menu: no microphone is named. */
export const AUTO_MICROPHONE = "";

/**
 * The microphones to choose from: automatic first, then each one found, and `saved` (the one chosen
 * before) if it is not plugged in now, said so. With `fit` (the settings page) the list has their
 * whole names and the automatic choice the short name of the one it uses; the closed dropdown has
 * short names cut to fit it, and the tooltips the whole ones. Without (the guide, whose menu is
 * wider) every choice has its whole name.
 */
export function microphoneChoices(devices: AudioDevices | null, saved: string | null, fit = false): Choice[] {
  const found = devices?.devices ?? [];
  const choices: Choice[] = fit
    ? [
        {
          value: AUTO_MICROPHONE,
          get label() { return automaticMicrophoneLabel(devices, MENU_ROOM); },
          get short() { return automaticMicrophoneShort(devices); },
          get title() { return automaticMicrophoneLabel(devices); },
        },
        ...found.map((d) => ({ value: d.name, label: d.name, short: cutToFit(shortMicrophoneName(d.name), DROPDOWN_ROOM) })),
      ]
    : [
        { value: AUTO_MICROPHONE, get label() { return automaticMicrophoneLabel(devices); } },
        ...found.map((d) => ({ value: d.name, label: d.name })),
      ];
  if (saved === null || choices.some((c) => c.value === saved)) return choices;
  const gone: Choice = fit
    ? { value: saved, label: t("mic.disconnected", { p0: saved }), short: microphoneText("mic.disconnected", saved, DROPDOWN_ROOM) }
    : { value: saved, get label() { return t("mic.disconnected", { p0: saved }); } };
  return [...choices, gone];
}
