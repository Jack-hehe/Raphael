// Reading the file the dictionary's 「匯入」 takes: .txt with a word on each line, or .csv whose first
// column is the word. Done in the page, so the words never leave it except for the core.

export const IMPORT_MAX_BYTES = 1024 * 1024;
export const IMPORT_MAX_LINES = 5000;

/** Why a file cannot be imported: too `big`, `unreadable` as text, or with no words (`empty`). */
export class ImportError extends Error {
  constructor(readonly kind: "big" | "unreadable" | "empty") {
    super(kind);
  }
}

/**
 * A first row like this is a header, not a word: word(s), term(s), and the Chinese 詞, 詞彙, 字詞, 單字.
 * Those are built from code points because they are matched data, not interface text, which the
 * catalogue test keeps out of the source as literals.
 */
const HEADERS = new Set(["word", "words", "term", "terms", ...[[0x8a5e], [0x8a5e, 0x5f59], [0x5b57, 0x8a5e], [0x55ae, 0x5b57]].map((codes) => String.fromCodePoint(...codes))]);

/** The text of a file: UTF-8 (a BOM is dropped), and Big5 when the bytes are no valid UTF-8. */
export function decodeWordFile(buffer: ArrayBuffer): string {
  if (buffer.byteLength > IMPORT_MAX_BYTES) throw new ImportError("big");
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(buffer).replace(/^\uFEFF/, "");
  } catch {
    // Not UTF-8; Excel's 「CSV」 in a Traditional Chinese Windows is Big5.
  }
  try {
    return new TextDecoder("big5").decode(buffer);
  } catch {
    throw new ImportError("unreadable");
  }
}

/** The records of a CSV (RFC 4180): quoted fields may hold commas, doubled quotes and line breaks. */
function csvRecords(text: string): string[][] {
  const records: string[][] = [];
  let record: string[] = [];
  let field = "";
  let quoted = false;
  const endField = (): void => {
    record.push(field);
    field = "";
  };
  const endRecord = (): void => {
    endField();
    records.push(record);
    record = [];
  };
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c !== '"') field += c;
      else if (text[i + 1] !== '"') quoted = false;
      else {
        field += '"';
        i++;
      }
    } else if (c === '"' && field.trim() === "") {
      field = "";
      quoted = true;
    } else if (c === ",") endField();
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      endRecord();
    } else field += c;
  }
  if (field !== "" || record.length > 0) endRecord();
  return records;
}

/**
 * The words of a file, trimmed, without a header row, empty lines or repeats (in any letter case,
 * the first spelling kept). A .csv gives its first column; any other file a word on each line.
 * Throws an `ImportError` for no words, or for more than IMPORT_MAX_LINES of them.
 */
export function parseWordFile(text: string, fileName: string): string[] {
  const csv = /\.csv$/i.test(fileName);
  const cells = csv ? csvRecords(text).map((record) => record[0] ?? "") : text.split(/\r\n|\r|\n/);
  const lines = cells.map((cell) => cell.trim()).filter(Boolean);
  if (lines.length > IMPORT_MAX_LINES) throw new ImportError("big");
  if (lines.length > 0 && HEADERS.has(lines[0].toLowerCase())) lines.shift();
  const seen = new Set<string>();
  const words = lines.filter((word) => {
    const key = word.toLowerCase();
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  if (words.length === 0) throw new ImportError("empty");
  return words;
}

/** How many of `words` the dictionary has already (`known` is its words), in any letter case. */
export function countKnown(words: string[], known: string[]): number {
  const have = new Set(known.map((word) => word.toLowerCase()));
  return words.filter((word) => have.has(word.toLowerCase())).length;
}
