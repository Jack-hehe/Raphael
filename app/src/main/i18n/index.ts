import { zhTW, type MessageKey } from "./zh-TW";
import { en } from "./en";
import { ja } from "./ja";

export type UiLocale = "zh-TW" | "en" | "ja";
export const catalogs = { "zh-TW": zhTW, en, ja };
let locale: UiLocale = "zh-TW";
const listeners = new Set<() => void>();

export const getLocale = (): UiLocale => locale;
export const isUiLocale = (value: unknown): value is UiLocale => value === "zh-TW" || value === "en" || value === "ja";
type MessageValue = string | number | (() => string | number);
const rendered = new Map<string, { key: MessageKey; values?: Record<string, MessageValue> }>();

/** The persisted device setting is the authority; unknown legacy values use the default. */
export function setLocale(value: unknown): void {
  const next = isUiLocale(value) ? value : "zh-TW";
  if (typeof document !== "undefined") document.documentElement.lang = next;
  if (next === locale) return;
  locale = next;
  for (const listener of listeners) listener();
}

export function onLocaleChange(listener: () => void): () => void {
  listeners.add(listener);
  return () => void listeners.delete(listener);
}

type Slots<S extends string> = S extends `${string}{${infer P}}${infer Rest}` ? P | Slots<Rest> : never;
type Values<K extends MessageKey> = Record<Slots<(typeof zhTW)[K]>, MessageValue>;

/** Interpolates once, so braces in a user's text are never interpreted as a second message. */
export function t<K extends MessageKey>(
  key: K,
  ...args: [Slots<(typeof zhTW)[K]>] extends [never] ? [values?: Values<K>] : [values: Values<K>]
): string {
  const message: string = catalogs[locale][key];
  const values = args[0] as Record<string, MessageValue> | undefined;
  const result = interpolate(message, values);
  rendered.set(result, { key, values });
  if (rendered.size > 512) rendered.delete(rendered.keys().next().value!);
  return result;
}

function interpolate(message: string, values?: Record<string, MessageValue>): string {
  return message.replace(/\{(\w+)\}/g, (match, name: string) => {
    const value = values?.[name];
    return value === undefined ? match : String(typeof value === "function" ? value() : value);
  });
}

/** Keeps existing transient notices in sync; backend messages and user text stay as supplied. */
export function translatedMessage(message: string): () => string {
  const reference = rendered.get(message);
  return reference ? () => {
    const result = interpolate(catalogs[locale][reference.key], reference.values);
    rendered.set(result, reference);
    if (rendered.size > 512) rendered.delete(rendered.keys().next().value!);
    return result;
  } : () => message;
}
