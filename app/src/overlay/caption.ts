// What the corner writes of a line of Raphael's (owner, 2026-10-27): never an address, since the
// details are read in the app, and never more than about two lines at a time. A long line is told
// in pages, one after the other, each cut where a sentence or an item of a numbered list ends.
import type { UiLanguage } from "./overlay";

/** About two lines of the corner (380 px of 14 px type): characters of Chinese and Japanese, letters of English. */
export const PAGE_CHARS: Record<UiLanguage, number> = { "zh-TW": 52, ja: 52, en: 110 };

// A markdown link keeps its words; an address goes with the brackets around it (an address is ASCII, so it ends at the
// first Chinese or Japanese character).
const LINK = /\[([^\]\n]*)\]\((?:https?:\/\/|www\.)[^)\s]*\)/gi;
const ADDRESS = /[（(]?\s*(?:https?:\/\/|www\.)[\x21-\x28\x2a-\x7e]*\s*[)）]?/gi;
const MARKS = /\*\*|__|`+|^#{1,6}\s+|^>\s*/gm;
const SPACE_BEFORE_MARK = /\s+(?=[，。！？、：；）」』》,.!?:;)])/g;

/** `text` with no address, link syntax or markdown mark left, on one line. */
export function captionText(text: string): string {
  return text.replace(LINK, "$1").replace(ADDRESS, " ").replace(MARKS, "").replace(/\s+/g, " ").replace(SPACE_BEFORE_MARK, "").trim();
}

const length = (text: string): number => [...text].length;

/**
 * The pieces of `text`: a sentence each (an English full stop ends one only before a space), and an item of a numbered
 * list ("2. ", "3) ") always starts a piece of its own.
 */
function pieces(text: string): string[] {
  const out: string[] = [];
  let start = 0;
  const cut = (end: number): void => {
    const piece = text.slice(start, end).trim();
    if (piece) out.push(piece);
    start = end;
  };
  for (let i = 0; i < text.length; i++) {
    const char = text[i]!;
    if ("。！？!?".includes(char) || (char === "." && (i + 1 === text.length || text[i + 1] === " ") && !/\d/.test(text[i - 1] ?? ""))) {
      let end = i + 1;
      while (end < text.length && "」』》）)\"'".includes(text[end]!)) end++;
      cut(end);
      i = end - 1;
    } else if (/\s/.test(char) && /^\d{1,2}[.)]\s/.test(text.slice(i + 1, i + 5))) cut(i);
  }
  cut(text.length);
  return out;
}

/** A piece too long for a page, cut after its last comma that fits, else at a space, never inside a word if it can help it. */
function split(piece: string, limit: number): string[] {
  const out: string[] = [];
  let rest = [...piece];
  while (rest.length > limit) {
    const window = rest.slice(0, limit);
    const comma = Math.max(...["，", "、", "；", ",", ";", "："].map((mark) => window.lastIndexOf(mark)));
    const space = window.lastIndexOf(" ");
    const end = comma >= 0 ? comma + 1 : space > 0 ? space : limit;
    out.push(rest.slice(0, end).join("").trim());
    rest = rest.slice(end);
  }
  if (rest.length) out.push(rest.join("").trim());
  return out.filter(Boolean);
}

const LIST_ITEM = /^\d{1,2}[.)]\s/;
/** A page no longer than this (an answer's 答。) never stands alone: it takes the piece after it. */
const SHORT = 6;

/** `text` in pages of at most `limit` characters, whole sentences together as far as they fit; an item of a list starts a page. */
export function pagesOf(text: string, limit: number): string[] {
  const pages: string[] = [];
  let page = "";
  for (const piece of pieces(text).flatMap((piece) => split(piece, limit))) {
    const joined = page ? `${page}${/[\u3000-\u9fff\uff00-\uffef]$/.test(page) ? "" : " "}${piece}` : piece;
    if (page && length(page) > SHORT && (LIST_ITEM.test(piece) || length(joined) > limit)) {
      pages.push(page);
      page = piece;
    } else page = joined;
  }
  if (page) pages.push(page);
  return pages;
}

/**
 * A line as the corner tells it: without addresses, in pages of about two lines. A line in 《》 keeps them, the opening on
 * its first page and the closing on its last (《答。…》 reads as one answer told in turns).
 */
export function captionLines(line: string, language: UiLanguage): string[] {
  const framed = line.startsWith("《") && line.endsWith("》");
  const body = captionText(framed ? line.slice(1, -1) : line);
  const pages = pagesOf(body, PAGE_CHARS[language] - (framed ? 1 : 0));
  if (!framed) return pages;
  if (!pages.length) return [line];
  return pages.map((page, i) => `${i === 0 ? "《" : ""}${page}${i === pages.length - 1 ? "》" : ""}`);
}
