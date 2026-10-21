// How the words of a conversation are written, in the main window and on the desktop alike: the
// person's in 「…」, Raphael's in 《prefix。text》, and 《Answer. text》 when the prefix is a word in
// letters. Pure and browser-free, so that both pages and their tests can use it.

/** A prefix written in Chinese or Japanese ends with 。; one written in letters ends with ". ". */
const CJK_PREFIX = /^[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}]/u;
const TRAILING_STOP = /[。.]+$/u;

/** Raphael's words: `replyLine("答", "好的")` is 《答。好的》; an English prefix reads 《Answer. Done》. */
export function replyLine(prefix: string, text: string): string {
  const head = prefix.trim().replace(TRAILING_STOP, "");
  const body = text.trim();
  if (head === "") return `《${body}》`;
  const cjk = CJK_PREFIX.test(head);
  if (body === "") return `《${head}${cjk ? "。" : "."}》`;
  return `《${head}${cjk ? "。" : ". "}${body}》`;
}

/** The person's words, as the anime quotes them. */
export const userLine = (text: string): string => `「${text.trim()}」`;
