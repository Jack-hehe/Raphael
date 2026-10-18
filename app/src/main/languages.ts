// The languages and their variants (the 語言 section of the settings). The speech recognition
// always tells by itself which language is spoken; what a person chooses here is how each one is
// written: 繁體 or 簡體, American or British spelling. The core lists the languages in groups
// (`languages.list`): a group with several variants (中文: 台灣、香港、簡體…) or a single language
// (日本語). A person keeps up to five, at most one variant of each group, and at least one. The
// choice is the `languages` setting: locale ids such as ["zh-TW", "en-US"]. The texts are plain;
// the functions that make HTML escape them.

import { t } from "./i18n";

import { CHECK, esc, ico } from "./ui";

export interface LanguageVariant {
  id: string;
  name: string;
  /** How the variant is written, such as 「台灣用語，例如 軟體、視窗」; may be empty. */
  note?: string;
}

export interface LanguageGroup {
  id: string;
  name: string;
  variants: LanguageVariant[];
}

/** The same as the core's default. */
export const DEFAULT_LANGUAGES = ["zh-TW"];
export const MAX_LANGUAGES = 5;
export const AUTO_DETECT_HINT = () => t("languages.001");

const VARIANT_NAMES: Record<string, "language.zh-TW" | "language.zh-HK" | "language.zh-CN" | "language.yue-HK"> = {
  "zh-TW": "language.zh-TW", "zh-HK": "language.zh-HK", "zh-CN": "language.zh-CN", "yue-HK": "language.yue-HK",
};
const VARIANT_NOTES: Record<string, "language.note.zh-TW" | "language.note.zh-HK" | "language.note.zh-CN" | "language.note.yue-HK"> = {
  "zh-TW": "language.note.zh-TW", "zh-HK": "language.note.zh-HK", "zh-CN": "language.note.zh-CN", "yue-HK": "language.note.yue-HK",
};
const groupName = (g: LanguageGroup): string => g.id === "zh" ? t("language.zh") : g.name;
const variantName = (v: LanguageVariant): string => VARIANT_NAMES[v.id] ? t(VARIANT_NAMES[v.id]) : v.name;
const variantNote = (v: LanguageVariant): string => VARIANT_NOTES[v.id] ? t(VARIANT_NOTES[v.id]) : v.note ?? "";

/** The group a locale id belongs to, or undefined when the list does not have it. */
export function groupOf(groups: LanguageGroup[], id: string): LanguageGroup | undefined {
  return groups.find((g) => g.variants.some((v) => v.id === id));
}

/** What a locale id is called; an id the list does not know is shown as it is. */
export function languageName(groups: LanguageGroup[], id: string): string {
  for (const g of groups) {
    const v = g.variants.find((x) => x.id === id);
    if (v) return variantName(v);
  }
  return id;
}

export type Toggle = { ok: true; languages: string[] } | { ok: false; reason: string };

/**
 * What choosing `id` in the panel makes of `selected`: a chosen one is taken out (unless it is the
 * last); another variant of a group that has one chosen takes its place; a new one is added at the
 * end while there are fewer than five.
 */
export function toggleLanguage(groups: LanguageGroup[], selected: string[], id: string): Toggle {
  if (selected.includes(id)) {
    if (selected.length <= 1) return { ok: false, reason: t("languages.002") };
    return { ok: true, languages: selected.filter((x) => x !== id) };
  }
  const group = groupOf(groups, id);
  const sibling = group ? selected.find((x) => x !== id && group.variants.some((v) => v.id === x)) : undefined;
  if (sibling) return { ok: true, languages: selected.map((x) => (x === sibling ? id : x)) };
  if (selected.length >= MAX_LANGUAGES) return { ok: false, reason: t("languages.003", { p0: MAX_LANGUAGES }) };
  return { ok: true, languages: [...selected, id] };
}

/** The setting as the page uses it: a list of ids, never empty. */
export function normalizeLanguages(value: unknown): string[] {
  if (!Array.isArray(value)) return [...DEFAULT_LANGUAGES];
  const ids = value.filter((x): x is string => typeof x === "string" && x.length > 0);
  return ids.length ? ids : [...DEFAULT_LANGUAGES];
}

/** The inside of the 語言 button: the chosen languages as small chips. */
export function languageChipsHTML(groups: LanguageGroup[], selected: string[]): string {
  return (
    `<span class="lang-chips">${selected.map((id) => `<span class="lchip">${esc(languageName(groups, id))}</span>`).join("")}</span>` +
    ico("pencil")
  );
}

/** What a screen reader says for the 語言 button. Plain text. */
export const languageLabel = (groups: LanguageGroup[], selected: string[]): string =>
  t("languages.004", { p0: selected.map((id) => languageName(groups, id)).join("、") });

/** The button of the 語言 row; `attrs` names it for the page. */
export function languageButtonHTML(groups: LanguageGroup[], selected: string[], attrs = ""): string {
  return (
    `<button type="button" class="btn sel variant-button" aria-haspopup="dialog" aria-expanded="false" ` +
    t("languages.005", { p0: esc(languageLabel(groups, selected)), p1: attrs })
  );
}

/** A variant or a single language in the panel: its name, and how it is written on a second line. */
function itemHTML(v: LanguageVariant, on: boolean, blocked: boolean, mark: string, cls: string): string {
  return (
    `<button type="button" class="lang-item ${cls}" data-lang="${esc(v.id)}" aria-pressed="${on}"${blocked ? ' aria-disabled="true"' : ""}>` +
    mark +
    (v.note
      ? `<span class="mtd"><span class="mt">${esc(variantName(v))}</span><span class="mdesc">${esc(variantNote(v))}</span></span>`
      : `<span class="mt">${esc(variantName(v))}</span>`) +
    `</button>`
  );
}

/**
 * The panel that opens from the 語言 row: first the languages with variants (each folds out to
 * show them, one group at a time: `open`), then the other languages. `groups` null: the list
 * could not be read.
 */
export function languagePanelHTML(groups: LanguageGroup[] | null, selected: string[], open: string | null): string {
  const head =
    t("languages.006") +
    t("languages.007", { p0: selected.length, p1: MAX_LANGUAGES });
  if (!groups || groups.length === 0) {
    return head + t("languages.008");
  }
  const full = selected.length >= MAX_LANGUAGES;
  const withVariants = groups.filter((g) => g.variants.length > 1);
  const singles = groups.filter((g) => g.variants.length === 1);
  const grouped = withVariants
    .map((g) => {
      const chosen = g.variants.find((v) => selected.includes(v.id));
      const expanded = open === g.id;
      const variants = expanded
        ? `<div class="lang-variants" role="group" aria-label="${esc(groupName(g))}">` +
          g.variants
            .map((v) => {
              const on = selected.includes(v.id);
              return itemHTML(v, on, !on && !chosen && full, `<span class="radio" aria-hidden="true"></span>`, "lang-variant");
            })
            .join("") +
          `</div>`
        : "";
      return (
        `<button type="button" class="lang-item lang-group" data-group="${esc(g.id)}" aria-expanded="${expanded}">` +
        `<span class="ck">${chosen ? CHECK : ""}</span><span class="mt">${esc(groupName(g))}</span>` +
        `<span class="md">${chosen ? esc(variantName(chosen)) : t("languages.009", { p0: g.variants.length })}</span>${ico("down")}` +
        `</button>` +
        variants
      );
    })
    .join("");
  const others = singles
    .map((g) => {
      const v = g.variants[0];
      const on = selected.includes(v.id);
      return itemHTML(v, on, !on && full, `<span class="ck">${on ? CHECK : ""}</span>`, "lang-single");
    })
    .join("");
  return (
    head +
    `<div class="lang-list">${grouped}` +
    (others ? t("languages.010", { p0: others }) : "") +
    `</div><p class="lang-foot">${esc(AUTO_DETECT_HINT())}</p>`
  );
}
