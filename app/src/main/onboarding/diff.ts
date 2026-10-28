// The difference between what was said and what the cleanup made of it, for lesson 3: the words
// that were taken away are struck out in what was said, and the words that were added or changed
// are marked in the result. Characters of Chinese and Japanese are one token each; letters and
// digits go by the word. Pure, so it can be tested without a window.

export interface Piece {
  text: string;
  /** In the spoken text: this was taken away. In the cleaned text: this is new. */
  changed: boolean;
}

/** More tokens than this and the texts are shown as they are: the table below grows with their product. */
const MAX_TOKENS = 700;

const tokens = (text: string): string[] => text.match(/[A-Za-z0-9_']+|\s+|[\s\S]/gu) ?? [];

/** Joins neighbours that have the same mark into one piece. */
function merge(pieces: Piece[]): Piece[] {
  const out: Piece[] = [];
  for (const piece of pieces) {
    const last = out[out.length - 1];
    if (last && last.changed === piece.changed) last.text += piece.text;
    else out.push({ ...piece });
  }
  return out;
}

/**
 * `spoken`, cut into pieces with the taken-away ones marked, and `cleaned`, cut into pieces with the
 * new ones marked.
 */
export function textDiff(spoken: string, cleaned: string): { spoken: Piece[]; cleaned: Piece[] } {
  const a = tokens(spoken);
  const b = tokens(cleaned);
  if (a.length > MAX_TOKENS || b.length > MAX_TOKENS) {
    return { spoken: [{ text: spoken, changed: false }], cleaned: [{ text: cleaned, changed: false }] };
  }
  // The length of the longest common run of tokens from i and j on.
  const table: number[][] = Array.from({ length: a.length + 1 }, () => new Array<number>(b.length + 1).fill(0));
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      table[i][j] = a[i] === b[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
    }
  }
  const spokenPieces: Piece[] = [];
  const cleanedPieces: Piece[] = [];
  let i = 0;
  let j = 0;
  while (i < a.length || j < b.length) {
    if (i < a.length && j < b.length && a[i] === b[j]) {
      spokenPieces.push({ text: a[i], changed: false });
      cleanedPieces.push({ text: b[j], changed: false });
      i++;
      j++;
    } else if (j >= b.length || (i < a.length && table[i + 1][j] >= table[i][j + 1])) {
      spokenPieces.push({ text: a[i++], changed: true });
    } else {
      cleanedPieces.push({ text: b[j++], changed: true });
    }
  }
  return { spoken: merge(spokenPieces), cleaned: merge(cleanedPieces) };
}

/** How many separate stretches were taken away or changed: the number the lesson can tell. */
export const changeCount = (diff: { spoken: Piece[]; cleaned: Piece[] }): number =>
  Math.max(diff.spoken.filter((p) => p.changed && p.text.trim()).length, diff.cleaned.filter((p) => p.changed && p.text.trim()).length);
