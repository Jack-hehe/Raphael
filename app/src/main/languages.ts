// Transcription languages and written variants are separate controls. The persisted core setting
// remains an ordered list of variant ids, with one variant per selected language (one to five).
import { t } from "./i18n";
import { CHECK, esc, ico } from "./ui";

export interface LanguageVariant {
  id: string;
  name: string;
  /** Guidance about a variant's spelling or vocabulary; may be empty. */
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

/** The language group containing a persisted variant id. */
export function groupOf(groups: LanguageGroup[], id: string): LanguageGroup | undefined {
  return groups.find((g) => g.variants.some((v) => v.id === id));
}

/** Selected language groups in the persisted priority order, without changing their variants. */
export function selectedLanguageGroups(groups: LanguageGroup[], selected: string[]): LanguageGroup[] {
  const chosen: LanguageGroup[] = [];
  for (const id of selected) {
    const group = groupOf(groups, id);
    if (group && !chosen.some((g) => g.id === group.id)) chosen.push(group);
  }
  return chosen;
}

/** Unknown ids remain readable while the core list is unavailable. */
export function languageName(groups: LanguageGroup[], id: string): string {
  const variant = groupOf(groups, id)?.variants.find((v) => v.id === id);
  return variant ? variantName(variant) : id;
}

export type LanguageChange = { ok: true; languages: string[] } | { ok: false; reason: string };

/** Adding a language uses its first variant; removing it leaves all other variants untouched. */
export function toggleTranscriptionLanguage(groups: LanguageGroup[], selected: string[], groupId: string): LanguageChange {
  const group = groups.find((g) => g.id === groupId);
  if (!group?.variants.length) return { ok: false, reason: t("languages.unavailable") };
  const remaining = selected.filter((id) => !group.variants.some((v) => v.id === id));
  if (remaining.length !== selected.length) {
    return remaining.length
      ? { ok: true, languages: remaining }
      : { ok: false, reason: t("languages.002") };
  }
  if (selected.length >= MAX_LANGUAGES) return { ok: false, reason: t("languages.003", { p0: MAX_LANGUAGES }) };
  return { ok: true, languages: [...selected, group.variants[0].id] };
}

/** A variant can only replace the variant of an already selected transcription language. */
export function chooseLanguageVariant(groups: LanguageGroup[], selected: string[], id: string): LanguageChange {
  const group = groupOf(groups, id);
  if (!group) return { ok: false, reason: t("languages.unavailable") };
  const current = selected.find((value) => group.variants.some((v) => v.id === value));
  if (!current) return { ok: false, reason: t("variants.chooseLanguageFirst") };
  return { ok: true, languages: selected.map((value) => value === current ? id : value) };
}

/** The setting as the page uses it: a list of variant ids, never empty. */
export function normalizeLanguages(value: unknown): string[] {
  if (!Array.isArray(value)) return [...DEFAULT_LANGUAGES];
  const ids = value.filter((x): x is string => typeof x === "string" && x.length > 0);
  return ids.length ? ids : [...DEFAULT_LANGUAGES];
}

function transcriptionNames(groups: LanguageGroup[], selected: string[]): string[] {
  return selected.map((id) => {
    const group = groupOf(groups, id);
    return group ? groupName(group) : id;
  });
}

/** The transcription control shows language names, without regional variants. */
export function languageChipsHTML(groups: LanguageGroup[], selected: string[]): string {
  return `<span class="lang-chips">${transcriptionNames(groups, selected)
    .map((name) => `<span class="lchip">${esc(name)}</span>`).join("")}</span>${ico("pencil")}`;
}

export const transcriptionLabel = (groups: LanguageGroup[], selected: string[]): string =>
  t("transcription.label", { p0: transcriptionNames(groups, selected).join(", ") });

export const languageLabel = (groups: LanguageGroup[], selected: string[]): string =>
  t("languages.004", { p0: selected.map((id) => languageName(groups, id)).join(", ") });

export function transcriptionButtonHTML(groups: LanguageGroup[], selected: string[], attrs = ""): string {
  return `<button type="button" class="btn sel lang-sel" aria-haspopup="dialog" aria-expanded="false" ` +
    `aria-label="${esc(transcriptionLabel(groups, selected))}" title="${esc(t("transcription.choose"))}"${attrs}>` +
    languageChipsHTML(groups, selected) + `</button>`;
}

/** The separate variant button includes the current variants in its accessible summary. */
export function languageButtonHTML(groups: LanguageGroup[], selected: string[], attrs = ""): string {
  return `<button type="button" class="btn sel variant-button" aria-haspopup="dialog" aria-expanded="false" ` +
    t("languages.005", { p0: esc(languageLabel(groups, selected)), p1: attrs });
}

/** The transcription picker selects language groups, with no variant controls. */
export function transcriptionPanelHTML(groups: LanguageGroup[] | null, selected: string[]): string {
  const head = `<div class="lang-head"><span class="lang-title">${esc(t("transcription.title"))}</span>` +
    t("languages.007", { p0: selected.length, p1: MAX_LANGUAGES });
  if (!groups?.length) return head + t("languages.008");
  const chosen = selectedLanguageGroups(groups, selected);
  const items = groups.filter((g) => g.variants.length).map((group) => {
    const on = chosen.some((g) => g.id === group.id);
    const blocked = !on && selected.length >= MAX_LANGUAGES;
    return `<button type="button" class="lang-item lang-language" data-language="${esc(group.id)}" aria-pressed="${on}"${blocked ? ' aria-disabled="true"' : ""}>` +
      `<span class="ck">${on ? CHECK : ""}</span><span class="mt">${esc(groupName(group))}</span></button>`;
  }).join("");
  return head + `<div class="lang-list">${items}</div><p class="lang-foot">${esc(AUTO_DETECT_HINT())}</p>`;
}

/** Only selected languages with multiple variants appear, one radio group per language. */
export function variantPanelHTML(groups: LanguageGroup[] | null, selected: string[]): string {
  const head = `<div class="lang-head"><span class="lang-title">${esc(t("settings.061"))}</span></div>`;
  if (!groups?.length) return head + t("languages.008");
  const withVariants = selectedLanguageGroups(groups, selected).filter((g) => g.variants.length > 1);
  if (!withVariants.length) return head + `<p class="lang-empty">${esc(t("variants.empty"))}</p>`;
  const items = withVariants.map((group) => {
    const variants = group.variants.map((variant) => {
      const guidance = variantNote(variant);
      return `<button type="button" role="radio" class="lang-item lang-variant" data-variant="${esc(variant.id)}" aria-checked="${selected.includes(variant.id)}">` +
        `<span class="radio" aria-hidden="true"></span><span class="mtd"><span class="mt">${esc(variantName(variant))}</span>` +
        (guidance ? `<span class="mdesc">${esc(guidance)}</span>` : "") + `</span></button>`;
    }).join("");
    return `<div class="lang-variant-group" data-variant-group="${esc(group.id)}" role="radiogroup" aria-label="${esc(groupName(group))}">` +
      `<div class="lang-sub">${esc(groupName(group))}</div>${variants}</div>`;
  }).join("");
  return head + `<div class="lang-list">${items}</div><p class="lang-foot">${esc(t("variants.description"))}</p>`;
}
