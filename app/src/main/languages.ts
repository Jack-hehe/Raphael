// The languages Raphael listens for (the 語言 row of the settings). The core lists them in groups
// (`languages.list`): a group with several variants (中文: 台灣、香港、簡體…) or a single language
// (日本語). A person picks up to five, at most one variant of each group, and at least one; with
// more than one the speech recognition tells them apart by itself. The choice is the `languages`
// setting: locale ids such as ["zh-TW", "en-US"]. The texts are plain; `languagePanelHTML` escapes.

import { CHECK, esc, ico } from "./ui";

export interface LanguageVariant {
  id: string;
  name: string;
  /** A short remark under the name, such as 「粵語」. */
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
export const AUTO_DETECT_HINT = "選多個語言時會自動偵測";

/** The group a locale id belongs to, or undefined when the list does not have it. */
export function groupOf(groups: LanguageGroup[], id: string): LanguageGroup | undefined {
  return groups.find((g) => g.variants.some((v) => v.id === id));
}

/** What a locale id is called; an id the list does not know is shown as it is. */
export function languageName(groups: LanguageGroup[], id: string): string {
  for (const g of groups) {
    const v = g.variants.find((x) => x.id === id);
    if (v) return v.name;
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
    if (selected.length <= 1) return { ok: false, reason: "至少要保留一個語言" };
    return { ok: true, languages: selected.filter((x) => x !== id) };
  }
  const group = groupOf(groups, id);
  const sibling = group ? selected.find((x) => x !== id && group.variants.some((v) => v.id === x)) : undefined;
  if (sibling) return { ok: true, languages: selected.map((x) => (x === sibling ? id : x)) };
  if (selected.length >= MAX_LANGUAGES) return { ok: false, reason: `最多選 ${MAX_LANGUAGES} 個語言，請先取消一個` };
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
    ico("down")
  );
}

/** What a screen reader says for the 語言 button. Plain text. */
export const languageLabel = (groups: LanguageGroup[], selected: string[]): string =>
  `語言：${selected.map((id) => languageName(groups, id)).join("、")}`;

/** The button of the 語言 row; `attrs` names it for the page. */
export function languageButtonHTML(groups: LanguageGroup[], selected: string[], attrs = ""): string {
  return (
    `<button type="button" class="sel lang-sel" aria-haspopup="dialog" aria-expanded="false" ` +
    `aria-label="${esc(languageLabel(groups, selected))}"${attrs}>${languageChipsHTML(groups, selected)}</button>`
  );
}

/**
 * The panel that opens from the 語言 button. `open` is the group whose variants are shown (one at
 * a time). `groups` null: the list could not be read.
 */
export function languagePanelHTML(groups: LanguageGroup[] | null, selected: string[], open: string | null): string {
  const head =
    `<div class="lang-head"><span class="lang-title">辨識的語言</span>` +
    `<span class="lang-count num">已選 ${selected.length}／${MAX_LANGUAGES}</span></div>`;
  if (!groups || groups.length === 0) {
    return head + `<p class="lang-empty">目前無法取得語言清單，請稍後再試。</p>`;
  }
  const full = selected.length >= MAX_LANGUAGES;
  const items = groups
    .map((g) => {
      const chosen = g.variants.find((v) => selected.includes(v.id));
      if (g.variants.length === 1) {
        const v = g.variants[0];
        const on = selected.includes(v.id);
        return (
          `<button type="button" class="lang-item" data-lang="${esc(v.id)}" aria-pressed="${on}"${!on && full ? ' aria-disabled="true"' : ""}>` +
          `<span class="ck">${on ? CHECK : ""}</span><span class="mt">${esc(v.name)}</span>` +
          (v.note ? `<span class="md">${esc(v.note)}</span>` : "") +
          `</button>`
        );
      }
      const expanded = open === g.id;
      const variants = expanded
        ? `<div class="lang-variants" role="group" aria-label="${esc(g.name)}">` +
          g.variants
            .map((v) => {
              const on = selected.includes(v.id);
              return (
                `<button type="button" class="lang-item lang-variant" data-lang="${esc(v.id)}" aria-pressed="${on}"${!on && !chosen && full ? ' aria-disabled="true"' : ""}>` +
                `<span class="radio" aria-hidden="true"></span><span class="mt">${esc(v.name)}</span>` +
                (v.note ? `<span class="md">${esc(v.note)}</span>` : "") +
                `</button>`
              );
            })
            .join("") +
          `</div>`
        : "";
      return (
        `<button type="button" class="lang-item lang-group" data-group="${esc(g.id)}" aria-expanded="${expanded}">` +
        `<span class="ck">${chosen ? CHECK : ""}</span><span class="mt">${esc(g.name)}</span>` +
        `<span class="md">${chosen ? esc(chosen.name) : `${g.variants.length} 種`}</span>${ico("down")}` +
        `</button>` +
        variants
      );
    })
    .join("");
  return head + `<div class="lang-list">${items}</div><p class="lang-foot">${esc(AUTO_DETECT_HINT)}</p>`;
}
