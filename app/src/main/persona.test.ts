import { afterEach, describe, expect, test } from "vitest";
import { personalize } from "./categories";
import { setLocale } from "./i18n";
import { categoryName, personaHTML, personaSentence } from "./persona";
import type { AppUsage } from "./types";

afterEach(() => setLocale("zh-TW"));

const row = (app: string, words: number): AppUsage => ({ app, words, dictations: 1 });
const card = (rows: AppUsage[]): string => personaHTML(personalize(rows)!);

const ROWS = [
  row("Claude.exe", 5000),
  row("ChatGPT.exe", 3000),
  row("Code.exe", 4000),
  row("WindowsTerminal.exe", 1000),
  row("WINWORD.EXE", 1000),
  row("chrome.exe", 500),
  row("unknown", 500),
];

describe("the card", () => {
  test("a heading, the range, the rose and a table", () => {
    const html = card(ROWS);
    expect(html).toContain('<h2 id="persona-h">個人化</h2>');
    expect(html).toContain('<span class="card-note">最近 30 天・依 App 類型</span>');
    expect(html).toContain('<svg class="rose"');
    expect(html).toContain('aria-label="最近 30 天依 App 類型的聽寫比例"');
    expect(html).toContain('<table class="persona-table">');
  });

  test("the table lists the kinds that have something, the biggest first, with the programs and the share", () => {
    const html = card(ROWS);
    const kinds = [...html.matchAll(/<tr data-kind="(\w+)">/g)].map((m) => m[1]);
    expect(kinds).toEqual(["ai", "dev", "docs", "web", "other"]);
    expect(html).toContain('<span class="sw p-ai"></span>AI 助手<span class="apps">Claude、ChatGPT</span></td><td class="num">54%</td>');
    expect(html).toContain('<span class="sw p-dev"></span>開發工具<span class="apps">VS Code、Windows Terminal</span></td><td class="num">33%</td>');
    expect(html).not.toContain('data-kind="chat"');
    // the history not knowing a program is "other" without a name to show
    expect(html).toContain('<span class="sw p-other"></span>其他</td><td class="num">3%</td>');
  });

  test("the rose has a petal for each kind with something, and a title that says its share", () => {
    const html = card(ROWS);
    expect(html.match(/<path class="rose-petal p-/g)).toHaveLength(5);
    expect(html).toContain("<title>AI 助手: 54%</title>");
  });

  test("a kind with a tiny share reads under 1%, not 0%", () => {
    const html = card([row("Claude.exe", 100_000), row("Slack.exe", 1)]);
    expect(html).toContain('<td class="num">&lt;1%</td>');
    expect(html).toContain("<title>通訊: &lt;1%</title>");
  });

  test("the sentence of Raphael's is in 《解。…》 and says the biggest kind", () => {
    expect(card(ROWS)).toContain("<p class=\"why\">《答。你有一半以上的聽寫都在「AI 助手」（54%）。》</p>");
  });

  test("program names from the history are escaped", () => {
    const html = card([row("<img src=x onerror=alert(1)>.exe", 100), row("Claude.exe", 100)]);
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;img src=x onerror=alert(1)&gt;");
  });
});

describe("what Raphael says", () => {
  test("most, half and spread, in Chinese", () => {
    expect(personaSentence({ kind: "most", id: "dev", percent: 41 })).toBe("《答。你最常在「開發工具」聽寫，佔 41%。》");
    expect(personaSentence({ kind: "half", id: "ai", percent: 54 })).toBe("《答。你有一半以上的聽寫都在「AI 助手」（54%）。》");
    expect(personaSentence({ kind: "spread" })).toBe("《答。你的聽寫分散在各種 App，沒有特別偏重哪一類。》");
  });

  test("in Japanese", () => {
    setLocale("ja");
    expect(personaSentence({ kind: "most", id: "dev", percent: 41 })).toBe("《解。最も多く音声入力しているのは「開発ツール」で、41% です。》");
    expect(personaSentence({ kind: "half", id: "chat", percent: 52 })).toBe("《解。音声入力の半分以上が「メッセージ」です（52%）。》");
    expect(personaSentence({ kind: "spread" })).toBe("《解。音声入力はさまざまなアプリに分散しています。》");
  });

  test("in English, as 《Answer. …》", () => {
    setLocale("en");
    expect(personaSentence({ kind: "most", id: "dev", percent: 41 })).toBe("《Answer. You dictate most in Development tools: 41%.》");
    expect(personaSentence({ kind: "half", id: "web", percent: 60 })).toBe("《Answer. More than half of your dictation is in Browsers: 60%.》");
    expect(personaSentence({ kind: "spread" })).toBe("《Answer. Your dictation is spread across many kinds of apps.》");
  });

  test("the whole card is in the language of the window and nothing else", () => {
    setLocale("en");
    const html = card(ROWS);
    expect(html).toContain("Personalization");
    expect(html).toContain("AI assistants");
    expect(html).toContain("Claude, ChatGPT");
    expect(html).not.toMatch(/[぀-ヿ㐀-鿿]/);
  });
});

describe("the names of the kinds", () => {
  test("every kind has one in every language", () => {
    for (const locale of ["zh-TW", "en", "ja"] as const) {
      setLocale(locale);
      const names = (["ai", "dev", "docs", "chat", "web", "other"] as const).map(categoryName);
      expect(new Set(names).size).toBe(6);
      expect(names.every((name) => name.length > 0)).toBe(true);
    }
  });
});
