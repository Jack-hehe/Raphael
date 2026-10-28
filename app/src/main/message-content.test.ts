// @vitest-environment happy-dom
import { expect, test } from "vitest";
import { messageHTML, messagePlainText, messageUrls, shortUrl } from "./message-content";

test("URLs stop at CJK punctuation and retain complete targets behind short labels", () => {
  const url = "https://www.cwa.gov.tw/V8/C/P/Typhoon/TY_WARN.html?lang=zh-TW&full=1";
  const text = `《答。${url}（颱風警報）。「https://example.com/a_(b)」》`;
  expect(messageUrls(text)).toEqual([url, "https://example.com/a_(b)"]);
  const el = document.createElement("div"); el.innerHTML = messageHTML(text);
  expect([...el.querySelectorAll("a")].map(a => a.dataset.url)).toEqual(messageUrls(text));
  expect(el.querySelector("a")!.textContent).toContain("www.cwa.gov.tw/");
  expect(el.querySelector("a")!.textContent!.length).toBeLessThan(url.length);
  expect(el.querySelector("a")!.textContent).toBe("www.cwa.gov.tw/V8/C/P/Typhoon/T…");
  expect(el.querySelector("a")!.getAttribute("href")).toBe(url);
  expect(el.querySelector("a")!.hasAttribute("title")).toBe(false);
  expect(el.querySelector("a")!.hasAttribute("aria-label")).toBe(false);
  expect(el.textContent).toContain("（颱風警報）。");
});

test("visible URL labels contain only host and path, capped at 32 characters, while copying retains the complete URL", () => {
  expect(shortUrl("https://example.com/forecast?city=Taipei#today")).toBe("example.com/forecast");
  expect(shortUrl("https://example.com/?city=Taipei#today")).toBe("example.com");
  const url = "https://example.com/" + "a".repeat(60) + "?secret=full#target";
  expect(shortUrl(url)).toBe("example.com/" + "a".repeat(19) + "…");
  expect(shortUrl(url)).toHaveLength(32);
  expect(messagePlainText(`See ${url}（資料）`, true)).toBe(`See ${url}（資料）`);
});

test("reply markdown renders lists, emphasis, breaks and copyable code without interpreting HTML", () => {
  const el = document.createElement("div");
  el.innerHTML = messageHTML('**bold** and *italic* with `a < b`\nnext\n\n- one\n- two\n\n1. first\n2. second\n\n```js\n<script>alert(1)</script>\n```\n<img src=x onerror=alert(1)>\n[bad](javascript:alert(1))\n[site](https://example.com/a?x=1&y=2)', "", true);
  expect(el.querySelector("strong")?.textContent).toBe("bold");
  expect(el.querySelector("em")?.textContent).toBe("italic");
  expect(el.querySelectorAll("ul li")).toHaveLength(2);
  expect(el.querySelectorAll("ol li")).toHaveLength(2);
  expect(el.querySelector("br")).not.toBeNull();
  expect(el.querySelector("pre code")?.textContent).toBe("<script>alert(1)</script>");
  expect(el.querySelector('[data-act="copy-code"]')).not.toBeNull();
  expect(el.querySelector("script,img,[onerror]")).toBeNull();
  expect(el.querySelectorAll("a")).toHaveLength(1);
  expect(el.querySelector("a")?.dataset.url).toBe("https://example.com/a?x=1&y=2");
  expect(messageUrls('`https://code.example`\n```\nhttps://code.example\n```\n[site](https://example.com)')).toEqual(["https://example.com/"]);
});
