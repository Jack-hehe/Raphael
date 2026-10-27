import { afterEach, describe, expect, test } from "vitest";
import { setLocale } from "./i18n";
import { permissionCardHTML, permissionCardsHTML, permissionFor, permissionStateText } from "./permission-card";
import { emptyTimelineState, turnHTML, type TurnLook } from "./timeline";
import type { AgentTurn, PermissionCard } from "./types";

afterEach(() => setLocale("zh-TW"));

const card = (over: Partial<PermissionCard> = {}): PermissionCard => ({
  id: "q1", source: "ask", kind: "tool", service: "Gmail", tool: "send_email", summary: "是否讓「Gmail」執行「send_email」（to: a@example.com）？",
  reason: null, digest: "d".repeat(64), always: true, state: "pending", expires_at_ms: 0, ...over,
});

const turn = (over: Partial<AgentTurn> = {}): AgentTurn => ({
  id: 7, created_at_ms: 0, question: "幫我寄信", route: "answer", prefix: "問", text: "", detail: null, status: "confirm", source: "app", steps: [], task: null, ...over,
});

const LOOK: TurnLook = { face: () => "<i></i>" };
// The words as read: a tag is a space, except between the clauses of a line, which a comma joins.
const visible = (html: string): string => html.replace(/<[^>]+>/g, " ").replace(/&#39;/g, "'").replace(/\s+/g, " ").replace(/(?<=[，、]) /g, "").trim();
const actions = (html: string): string[] => [...html.matchAll(/data-act="([^"]+)"/g)].map((match) => match[1]);

describe("a permission card", () => {
  test("says the exact action in plain words and offers 拒絕, 一律允許 and 允許一次, the primary last", () => {
    const html = permissionCardHTML(card());
    expect(visible(html)).toBe("Gmail 是否讓「Gmail」執行「send_email」（to: a@example.com）？ 拒絕 一律允許 允許一次");
    expect(actions(html)).toEqual(["perm-deny", "perm-always", "perm-once"]);
    // Real framed buttons, the primary last, each naming its card and who answers it.
    expect(html).toContain('class="btn sm" data-act="perm-deny" data-card="q1" data-source="ask"');
    expect(html).toContain('class="btn sm pri" data-act="perm-once" data-card="q1" data-source="ask"');
    expect(html.indexOf("perm-deny")).toBeLessThan(html.indexOf("perm-always"));
    expect(html.indexOf("perm-always")).toBeLessThan(html.indexOf("perm-once"));
    expect(html).not.toContain(" disabled");
    expect(html).toContain('data-card-state="pending"');
  });

  test("offers no 一律允許 where it cannot take effect, and says which rule had the question asked", () => {
    const html = permissionCardHTML(card({ always: false, reason: "此操作符合安全規則「寄信前先問」。" }));
    expect(actions(html)).toEqual(["perm-deny", "perm-once"]);
    expect(visible(html)).toContain("此操作符合安全規則「寄信前先問」。");
    expect(visible(html)).not.toContain("一律允許");
  });

  test("its buttons cannot be pressed while the window holds no question to answer it with", () => {
    const html = permissionCardHTML(card(), false);
    expect([...html.matchAll(/<button[^>]*>/g)].every((match) => match[0].includes(" disabled"))).toBe(true);
    // A card of a task that went on has its own door, so the window need not hold a question for it.
    const run = permissionCardsHTML(turn({ permissions: [card({ source: "run" })] }), () => false);
    expect(run).not.toContain(" disabled");
    expect(run).toContain('data-source="run"');
  });

  test("names the computer for a step on it, with the computer's icon", () => {
    const html = permissionCardHTML(card({ kind: "step", service: "", tool: "", summary: "要送出這封信嗎？" }));
    expect(visible(html)).toContain("電腦 要送出這封信嗎？");
    expect(html).toContain("<svg");
  });

  test("each state says what became of the question and keeps the action it was about", () => {
    const outcomes: [PermissionCard["state"], string][] = [
      ["allowed", "已允許一次"], ["always", "已一律允許，之後這個動作不再詢問"], ["denied", "已拒絕，沒有執行"],
      ["expired", "已逾時，沒有執行"], ["cancelled", "已取消，沒有執行"],
    ];
    for (const [state, words] of outcomes) {
      const html = permissionCardHTML(card({ state }));
      expect(visible(html)).toContain(words);
      expect(visible(html)).toContain("是否讓「Gmail」執行「send_email」");
      expect(actions(html)).toEqual([]);
      expect(permissionStateText(card({ state }))).toBe(words);
    }
    expect(permissionCardHTML(card({ state: "allowed" }))).toContain('class="cc-done"');
    expect(permissionCardHTML(card({ state: "denied" }))).not.toContain("cc-done");
  });

  test("speaks only the window's language", () => {
    setLocale("en");
    expect(visible(permissionCardHTML(card({ summary: "Allow Gmail to run send_email?" })))).toBe("Gmail Allow Gmail to run send_email? Deny Always allow Allow once");
    setLocale("ja");
    expect(visible(permissionCardHTML(card({ summary: "Gmail に send_email を実行させますか？" })))).toBe("Gmail Gmail に send_email を実行させますか？ 拒否 常に許可 今回だけ許可");
    expect(visible(permissionCardHTML(card({ state: "denied" })))).toContain("拒否しました。実行していません");
  });

  test("escapes what the core said", () => {
    const html = permissionCardHTML(card({ service: "<b>x</b>", summary: "<script>alert(1)</script>", reason: "<i>r</i>" }));
    expect(html).not.toContain("<script>");
    expect(html).not.toContain("<b>x</b>");
    expect(html).not.toContain("<i>r</i>");
  });

  test("is drawn below the reply of its turn, and a turn with none has none", () => {
    const html = turnHTML(turn({ permissions: [card()] }), emptyTimelineState(), false, LOOK);
    expect(html).toContain('data-permission-card="q1"');
    expect(turnHTML(turn(), emptyTimelineState(), false, LOOK)).not.toContain("permission-card");
  });

  test("finds the card that waits for a question, and none that was answered", () => {
    const turns = [turn({ id: 1, permissions: [card({ id: "old", state: "denied" })] }), turn({ id: 2, permissions: [card({ id: "q2" })] })];
    expect(permissionFor(turns, "q2")?.id).toBe("q2");
    expect(permissionFor(turns, "old")).toBeUndefined();
    expect(permissionFor(turns, "other")).toBeUndefined();
  });
});
