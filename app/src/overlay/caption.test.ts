import { expect, test } from "vitest";
import { PAGE_CHARS, captionLines, captionText, pagesOf } from "./caption";

// The answer the owner saw run out of the top of the window on 2026-10-27.
const COMPOSIO = "1. 建立帳號並登入控制台。 2. 若設定只要求金鑰，前往「Settings → Project Settings → API Keys」取得。" +
  "[官方說明](https://docs.composio.dev/reference/authenticating-to-composio) 3. 將金鑰填入設定頁，再回到 Gmail 連結卡完成 Google 授權。" +
  "金鑰請只填入專用欄位，不要貼到聊天中。";
const chars = (text: string): number => [...text].length;

test("the corner writes no address: a link keeps its words, an address goes with its brackets", () => {
  const text = captionText(COMPOSIO);
  expect(text).not.toMatch(/https?:|www\.|\]\(|composio\.dev/);
  expect(text).toContain("取得。官方說明 3. 將金鑰");
  expect(captionText("請看說明（https://example.com/a?b=1）。")).toBe("請看說明。");
  expect(captionText("Done. See (https://example.com) for more.")).toBe("Done. See for more.");
  expect(captionText("**重點**：`settings`")).toBe("重點：settings");
});

test("a long line is told in pages of about two lines, cut where a sentence or an item ends, and nothing is lost", () => {
  const text = captionText(COMPOSIO);
  const pages = pagesOf(text, PAGE_CHARS["zh-TW"]);
  expect(pages.length).toBeGreaterThan(1);
  for (const page of pages) expect(chars(page)).toBeLessThanOrEqual(PAGE_CHARS["zh-TW"]);
  expect(pages.join("").replace(/\s/g, "")).toBe(text.replace(/\s/g, ""));
  // each item of the list starts a page, and no word is cut in two
  expect(pages.filter((page) => /^\d\.\s/.test(page)).map((page) => page[0])).toEqual(["1", "2", "3"]);
  expect(pages.join("|")).toContain("API Keys」");
});

test("a sentence too long for a page is cut after a comma", () => {
  const sentence = `${"甲".repeat(30)}，${"乙".repeat(30)}。`;
  expect(pagesOf(sentence, 52)).toEqual([`${"甲".repeat(30)}，`, `${"乙".repeat(30)}。`]);
});

test("Raphael's 《》 open on the first page and close on the last", () => {
  const pages = captionLines(`《答。${COMPOSIO}》`, "zh-TW");
  expect(pages[0]!.startsWith("《答。1. 建立帳號")).toBe(true);
  expect(pages.at(-1)!.endsWith("》")).toBe(true);
  for (const page of pages.slice(1)) expect(page.startsWith("《")).toBe(false);
  for (const page of pages.slice(0, -1)) expect(page.endsWith("》")).toBe(false);
  expect(pages.join("")).not.toMatch(/https?:/);
});

test("a short line is one page, as it was", () => {
  expect(captionLines("《答。台北現在二十四度，多雲。》", "zh-TW")).toEqual(["《答。台北現在二十四度，多雲。》"]);
  expect(captionLines("《Answer. Hello.》", "en")).toEqual(["《Answer. Hello.》"]);
  expect(captionLines("Remembered.", "en")).toEqual(["Remembered."]);
});

test("English pages join their sentences with a space and break only at a full stop before a space", () => {
  const pages = pagesOf("Version 3.5 is out. It fixes the menu. ".repeat(4).trim(), 60);
  for (const page of pages) expect(page.length).toBeLessThanOrEqual(60);
  expect(pages.join(" ")).toContain("Version 3.5 is out. It fixes the menu.");
});
