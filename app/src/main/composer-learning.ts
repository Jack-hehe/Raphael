/// <reference lib="es2022.intl" />
// Only explicitly delivered dictation seeds learning. Ordinary typing, restored messages and
// clipboard pastes do not. Dictation evidence stays in memory for at most 60 seconds.
import * as api from "./api";
import { t } from "./i18n";
import { toaster } from "./ui";

export interface Correction { word: string; misheard: string }
const letter = (c: string): boolean => /^[\p{L}\p{N}_'-]$/u.test(c) && !/[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}]/u.test(c);

/** One bounded replacement, made into whole Latin words. Ambiguous rewrites fail closed. */
export function composerCorrections(before: string, after: string): Correction[] {
  if (!before || before.length > 20_000 || after.length > 20_000 || before === after) return [];
  const a = Array.from(before), b = Array.from(after);
  let start = 0, endA = a.length, endB = b.length;
  while (start < endA && start < endB && a[start] === b[start]) start++;
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) { endA--; endB--; }
  const changed = Math.max(endA - start, endB - start);
  // Appending a thought is not correcting the dictation.
  if (start === a.length || endA === start && (start === 0 || start === a.length)) return [];
  while (start > 0 && (letter(a[start - 1]) || letter(b[start - 1]))) start--;
  while (endA < a.length && endB < b.length && (letter(a[endA]) || letter(b[endB]))) { endA++; endB++; }
  // Keep a kana name whole without absorbing a hiragana particle beside a katakana name.
  const kana = (c: string) => /[\p{Script=Hiragana}\p{Script=Katakana}ー]/u.test(c);
  const sameKana = (x: string, y: string) => kana(x) && kana(y) && /[\p{Script=Katakana}ー]/u.test(x) === /[\p{Script=Katakana}ー]/u.test(y);
  if (b.slice(start, endB).some(kana)) {
    while (start > 0 && sameKana(b[start - 1], b[start]) && a[start - 1] === b[start - 1]) start--;
    while (endA < a.length && endB < b.length && sameKana(b[endB - 1], b[endB]) && a[endA] === b[endB]) { endA++; endB++; }
  }
  let misheard = a.slice(start, endA).join("").trim(), word = b.slice(start, endB).join("").trim();
  if (Array.from(word).length === 1 && /\p{Script=Han}/u.test(word)) {
    if (typeof Intl.Segmenter !== "function") return [];
    const ranges = (text: string) => [...new Intl.Segmenter("zh-TW", { granularity: "word" }).segment(text)];
    const offset = a.slice(0, start).join("").length, nextOffset = b.slice(0, start).join("").length;
    const old = ranges(before).find(part => part.index <= offset && offset < part.index + part.segment.length);
    const next = ranges(after).find(part => part.index <= nextOffset && nextOffset < part.index + part.segment.length);
    if (!old || !next || old.segment.length !== 1 || next.segment.length < 2) return [];
    const left = nextOffset - next.index, right = next.segment.length - left - word.length;
    misheard = before.slice(offset - left, offset + misheard.length + right);
    word = next.segment;
  }
  const short = (value: string) => value.length <= 40 && !/[\r\n]/.test(value) && value.split(/\s+/).length <= 3 && /^[\p{L}\p{N}\s'_’-]+$/u.test(value);
  const term = /[A-Za-z]{3}/.test(word) || (word.match(/[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}]/gu)?.length ?? 0) >= 2;
  if (!misheard || !word || !term || !short(misheard) || !short(word) || misheard.toLowerCase() === word.toLowerCase()) return [];
  // A short dictated brand/name may itself be most of a short sentence. Keep the general
  // rewrite limit; exempt only a single plausible capitalized compound or name.
  const normalized = (value: string) => value.toLowerCase().replace(/[^a-z0-9]/g, "");
  const oldName = normalized(misheard), newName = normalized(word);
  const row = Array.from({ length: newName.length + 1 }, (_, index) => index);
  for (let i = 0; i < oldName.length; i++) {
    let diagonal = row[0]; row[0] = i + 1;
    for (let j = 0; j < newName.length; j++) {
      const above = row[j + 1];
      row[j + 1] = Math.min(above + 1, row[j] + 1, diagonal + Number(oldName[i] !== newName[j]));
      diagonal = above;
    }
  }
  const name = /^[A-Za-z][A-Za-z0-9'-]{2,39}$/.test(word) && /[A-Z]/.test(word)
    && oldName.length >= 3 && row[newName.length] * 5 <= Math.max(oldName.length, newName.length) * 2;
  if (changed > a.length * 0.3 && !name) return [];
  return [{ word, misheard }];
}

export class ComposerLearning {
  private baseline?: DictationBaseline;
  private timer?: ReturnType<typeof setTimeout>;
  seed(text: string, dictated = text): void {
    const start = text.lastIndexOf(dictated);
    this.clear();
    if (!dictated || start < 0 || text.length > 20_000) return;
    this.restore({ text: dictated, prefix: text.slice(0, start), suffix: text.slice(start + dictated.length), at: Date.now() });
  }
  snapshot(): DictationBaseline | undefined { return this.baseline; }
  restore(baseline?: DictationBaseline): void {
    if (baseline && this.baseline === baseline) return;
    this.clear();
    if (!baseline?.text || baseline.text.length + baseline.prefix.length + baseline.suffix.length > 20_000
      || baseline.at > Date.now() || Date.now() - baseline.at >= 60_000) return;
    this.baseline = baseline;
    this.timer = setTimeout(() => {
      // Retry drafts can hold this object too. Erase their evidence at the same deadline.
      baseline.text = baseline.prefix = baseline.suffix = "";
      if (this.baseline === baseline) { this.baseline = undefined; this.timer = undefined; }
    }, 60_000 - (Date.now() - baseline.at));
  }
  clear(): void {
    clearTimeout(this.timer);
    if (this.baseline) this.baseline.text = this.baseline.prefix = this.baseline.suffix = "";
    this.baseline = undefined; this.timer = undefined;
  }
  take(text: string): Correction[] {
    const baseline = this.baseline;
    // Keep the deadline's erasure alive for any pending-send retry snapshot.
    this.baseline = undefined; this.timer = undefined;
    if (!baseline || Date.now() - baseline.at >= 60_000 || !text.startsWith(baseline.prefix) || !text.endsWith(baseline.suffix)) return [];
    return composerCorrections(baseline.text, text.slice(baseline.prefix.length, text.length - baseline.suffix.length));
  }
}

export function bindComposerLearning(input: HTMLTextAreaElement): ComposerLearning {
  const learning = new ComposerLearning();
  input.addEventListener("dictation-pasted", event => {
    const text: unknown = (event as CustomEvent).detail;
    if (typeof text === "string" && input.value.includes(text)) learning.seed(input.value, text);
  });
  return learning;
}

export interface DictationBaseline { text: string; prefix: string; suffix: string; at: number }

export async function learnComposerCorrections(corrections: Correction[]): Promise<void> {
  if (!corrections.length) return;
  try {
    const result = await api.core<{ learned: string[] }>("dictionary.learn", { corrections });
    if (result.learned.length) toaster.info(t("dictionary.learned", { p0: result.learned.join("、") }));
  } catch { /* Learning never blocks delivery; a failed RPC must not claim a word was added. */ }
}
