import { t } from "./index";
import { zhTW } from "./zh-TW";

/** Localize known Rust command errors at the UI boundary without changing the Rust contract. */
const exact = [
  "backend.limit", "backend.retention", "backend.language", "backend.stt", "backend.model",
  "backend.micTimeout", "backend.emptyDictation", "backend.emptyPaste", "backend.duplicateDictation",
  "backend.duplicatePaste", "backend.conflictingKeys", "backend.invalidKeys", "keys.004", "keys.005", "keys.006",
] as const;
const detailed = ["backend.save", "backend.autostartEnable", "backend.autostartDisable", "backend.microphone"] as const;

export function backendMessage(text: string): string {
  for (const key of exact) if (text === zhTW[key]) return t(key);
  for (const key of detailed) {
    const prefix = zhTW[key].split("{p0}")[0];
    if (text.startsWith(prefix)) return t(key, { p0: text.slice(prefix.length) });
  }
  // Unknown diagnostic details belong to the backend and remain literal.
  return text;
}
