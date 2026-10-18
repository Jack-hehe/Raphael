// The dictionary page's suggestions: words found in the latest dictations, each a chip that adds
// the word, with a quiet × that dismisses it (`dictionary.dismiss`: not suggested again).

import { t } from "./i18n";

import { esc, ico } from "./ui";

/** The suggestion chips, or "" without words. Every word is escaped. */
export function suggestionsHTML(words: readonly string[]): string {
  if (words.length === 0) return "";
  return (
    t("suggest.001") +
    words
      .map((word) => {
        const w = esc(word);
        return (
          `<span class="chip">` +
          `<button type="button" class="chip-add" data-act="suggest" data-word="${w}">${ico("plus")}${w}</button>` +
          t("suggest.002", { p0: w, p1: w, p2: ico("x") }) +
          `</span>`
        );
      })
      .join("")
  );
}

/** The suggestions without the ones being dismissed (their toast is still up). */
export const visibleSuggestions = (words: readonly string[], dismissing: ReadonlySet<string>): string[] =>
  words.filter((word) => !dismissing.has(word));

/** What the toast says while a dismiss can still be undone. */
export const dismissMessage = (word: string): string => t("suggest.003", { p0: word });
