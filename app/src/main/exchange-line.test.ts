import { afterEach, describe, expect, test } from "vitest";
import { exchangeLineHTML } from "./exchange-line";
import { setLocale } from "./i18n";
import type { TurnLook } from "./timeline";
import type { AgentTurn } from "./types";

const visible = (html: string): string => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
const summary = (html: string): string => visible(html.slice(html.indexOf("<summary>"), html.indexOf("</summary>")));
const detail = (html: string): string => visible(html.slice(html.indexOf(">", html.indexOf('class="handoff-detail')) + 1));
const NAMES: Record<number, string> = { 0: "拉斐爾", 3: "行銷", 4: "PM" };
const LOOK: TurnLook = { face: () => "", memberName: (id) => NAMES[id] ?? "", memberFace: (id) => `<i class="face-${id}"></i>` };

function asked(id: number, question: string, answer: string, status = "done"): AgentTurn {
  return { id, created_at_ms: id * 1000, question, route: "exchange", prefix: "", text: answer, detail: null, status,
    source: "member", scope: "exchange", member_id: 3, peer_id: 4 };
}

afterEach(() => setLocale("zh-TW"));

describe("what a colleague asked, in the asked member's own chat", () => {
  test("one question is 訊息來自 with the asker's face, opening on the question and the answer, each speaker named once", () => {
    setLocale("zh-TW");
    const html = exchangeLineHTML([asked(5, "新品代號是什麼？", "新品代號是 NX-1。")], LOOK);
    expect(summary(html)).toBe("訊息來自 PM");
    expect(html).toContain('<i class="face-4"></i>');
    expect(detail(html)).toBe("PM 新品代號是什麼？ 行銷 新品代號是 NX-1。");
    expect(html).not.toMatch(/PM: |行銷: /);
  });

  test("several in a row count every message, and a question still being answered has no answer yet", () => {
    setLocale("zh-TW");
    const html = exchangeLineHTML([asked(5, "代號？", "NX-1"), asked(6, "上市日？", "", "running")], LOOK);
    expect(summary(html)).toBe("3 則訊息往來： PM");
    expect(detail(html)).toBe("PM 代號？ 行銷 NX-1 PM 上市日？");
  });

  test("the window's language words the line", () => {
    setLocale("en");
    expect(summary(exchangeLineHTML([asked(5, "a", "b"), asked(6, "c", "d")], LOOK))).toBe("4 messages with PM");
    setLocale("ja");
    expect(summary(exchangeLineHTML([asked(5, "a", "b")], LOOK))).toBe("PM からのメッセージ");
  });
});
