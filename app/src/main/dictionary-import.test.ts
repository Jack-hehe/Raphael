import { describe, expect, test } from "vitest";
import { IMPORT_MAX_BYTES, IMPORT_MAX_LINES, ImportError, countKnown, decodeWordFile, parseWordFile } from "./dictionary-import";

const bytes = (...parts: number[]): ArrayBuffer => new Uint8Array(parts).buffer;
const encode = (text: string): ArrayBuffer => new TextEncoder().encode(text).buffer as ArrayBuffer;

describe("reading the words of a file", () => {
  test("a .txt has one word per line, trimmed, without empty lines", () => {
    expect(parseWordFile("Tailscale\r\n  拉斐爾  \n\n \nOpenRouter\n", "words.txt")).toEqual(["Tailscale", "拉斐爾", "OpenRouter"]);
  });

  test("a .txt line is a word as a whole, commas and all", () => {
    expect(parseWordFile("Hello, world\nfoo", "words.txt")).toEqual(["Hello, world", "foo"]);
  });

  test("a .csv gives its first column and ignores the others", () => {
    expect(parseWordFile("Tailscale,a note\n拉斐爾,拉菲爾,3\n", "words.csv")).toEqual(["Tailscale", "拉斐爾"]);
  });

  test("quoted fields hold commas, doubled quotes and line breaks", () => {
    expect(parseWordFile('"Hello, world",x\n"say ""hi""",y\n"two\nlines",z', "w.csv")).toEqual(["Hello, world", 'say "hi"', "two\nlines"]);
  });

  test.each(["word", "Word", "WORDS", "term", "Term", "詞", "字詞"])("a header row %s is ignored", (header) => {
    expect(parseWordFile(`${header},note\nTailscale\n`, "w.csv")).toEqual(["Tailscale"]);
    expect(parseWordFile(`${header}\nTailscale\n`, "w.txt")).toEqual(["Tailscale"]);
  });

  test("a header-like word further down is a word", () => {
    expect(parseWordFile("Tailscale\nword\n", "w.csv")).toEqual(["Tailscale", "word"]);
  });

  test("repeats within the file are dropped, in any letter case, the first spelling kept", () => {
    expect(parseWordFile("OpenRouter\nopenrouter\nOPENROUTER\nTailscale\nOpenRouter", "w.txt")).toEqual(["OpenRouter", "Tailscale"]);
  });

  test("a file with no words is refused", () => {
    expect(() => parseWordFile("\n \n", "w.txt")).toThrow(expect.objectContaining({ kind: "empty" }));
    expect(() => parseWordFile("word\n", "w.csv")).toThrow(expect.objectContaining({ kind: "empty" }));
    expect(() => parseWordFile(",x\n", "w.csv")).toThrow(ImportError);
  });

  test("more than 5,000 lines is refused", () => {
    const many = (n: number) => Array.from({ length: n }, (_, i) => `w${i}`).join("\n");
    expect(parseWordFile(many(IMPORT_MAX_LINES), "w.txt")).toHaveLength(IMPORT_MAX_LINES);
    expect(() => parseWordFile(many(IMPORT_MAX_LINES + 1), "w.txt")).toThrow(expect.objectContaining({ kind: "big" }));
  });
});

describe("decoding a file", () => {
  test("UTF-8, with or without a BOM", () => {
    expect(decodeWordFile(encode("拉斐爾"))).toBe("拉斐爾");
    expect(decodeWordFile(bytes(0xef, 0xbb, 0xbf, 0x41, 0x42))).toBe("AB");
  });

  test("Big5 when the bytes are not UTF-8", () => {
    // 你好 in Big5
    expect(decodeWordFile(bytes(0xa7, 0x41, 0xa6, 0x6e))).toBe("你好");
  });

  test("a file over 1 MB is refused before it is read", () => {
    expect(IMPORT_MAX_BYTES).toBe(1024 * 1024);
    expect(() => decodeWordFile(new ArrayBuffer(IMPORT_MAX_BYTES + 1))).toThrow(expect.objectContaining({ kind: "big" }));
  });
});

describe("what is new in a file", () => {
  test("counts the words the dictionary has already, in any letter case", () => {
    expect(countKnown(["tailscale", "Kokoro", "拉斐爾"], ["Tailscale", "拉斐爾", "Other"])).toBe(2);
    expect(countKnown(["a"], [])).toBe(0);
  });
});
