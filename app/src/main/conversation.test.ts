import { afterEach, describe, expect, test } from "vitest";
import { setLocale } from "./i18n";
import { daysHTML, groupByDay, hasDetail, mergeTurns, miniTurnHTML, oldestId, turnHTML, turnStamp } from "./conversation";
import type { AgentTurn } from "./types";

afterEach(() => setLocale("zh-TW"));

// The tests run in Taipei time (vitest.config.ts).
const at = (iso: string): number => Date.parse(iso);
const now = at("2026-10-20T10:00:00+08:00");

function turn(id: number, when: string, more: Partial<AgentTurn> = {}): AgentTurn {
  return {
    id,
    created_at_ms: at(when),
    question: `問題 ${id}`,
    route: "chat",
    prefix: "答",
    text: `回答 ${id}`,
    detail: null,
    status: "done",
    ...more,
  };
}

const NEWEST_FIRST = [
  turn(5, "2026-10-20T09:12:00+08:00"),
  turn(4, "2026-10-19T23:58:00+08:00"),
  turn(3, "2026-10-19T09:00:00+08:00"),
  turn(2, "2026-10-17T12:00:00+08:00"),
  turn(1, "2025-12-31T12:00:00+08:00"),
];

describe("groupByDay", () => {
  test("the days run oldest to newest and each day oldest to newest: the newest turn is last", () => {
    const groups = groupByDay(NEWEST_FIRST, now);
    expect(groups.map((g) => g.label)).toEqual(["2025 年 12 月 31 日", "10 月 17 日", "昨天", "今天"]);
    expect(groups.map((g) => g.turns.map((t) => t.id))).toEqual([[1], [2], [3, 4], [5]]);
    expect(groups.map((g) => g.key)).toEqual(["2025-12-31", "2026-10-17", "2026-10-19", "2026-10-20"]);
  });

  test("the day is the local one: a turn just after midnight belongs to the new day", () => {
    const groups = groupByDay(
      [turn(2, "2026-10-20T00:00:05+08:00"), turn(1, "2026-10-19T23:59:55+08:00")],
      now,
    );
    expect(groups.map((g) => g.label)).toEqual(["昨天", "今天"]);
  });

  test("turns that came in a different order than their time are laid out by time", () => {
    const groups = groupByDay([turn(1, "2026-10-20T09:00:00+08:00"), turn(2, "2026-10-20T08:00:00+08:00")], now);
    expect(groups[0].turns.map((t) => t.id)).toEqual([2, 1]);
  });

  test("two turns at the same time keep the order of their ids", () => {
    const groups = groupByDay([turn(8, "2026-10-20T09:00:00+08:00"), turn(7, "2026-10-20T09:00:00+08:00")], now);
    expect(groups[0].turns.map((t) => t.id)).toEqual([7, 8]);
  });

  test("the list that was given is not changed, and nothing makes no days", () => {
    const copy = [...NEWEST_FIRST];
    groupByDay(NEWEST_FIRST, now);
    expect(NEWEST_FIRST).toEqual(copy);
    expect(groupByDay([], now)).toEqual([]);
  });

  test("the headings follow the language of the window", () => {
    setLocale("en");
    expect(groupByDay(NEWEST_FIRST, now).map((g) => g.label)).toEqual(["12/31/2025", "10/17", "Yesterday", "Today"]);
  });
});

describe("detail", () => {
  test("a turn has a detail when there is more than its line", () => {
    expect(hasDetail(turn(1, "2026-10-20T09:00:00+08:00", { detail: "更長的說明" }))).toBe(true);
    expect(hasDetail(turn(1, "2026-10-20T09:00:00+08:00", { detail: null }))).toBe(false);
    expect(hasDetail(turn(1, "2026-10-20T09:00:00+08:00", { detail: "   " }))).toBe(false);
    expect(hasDetail(turn(1, "2026-10-20T09:00:00+08:00", { detail: " 回答 1 " }))).toBe(false); // the line again
    expect(hasDetail(turn(1, "2026-10-20T09:00:00+08:00", { detail: undefined as unknown as null }))).toBe(false);
  });
});

describe("paging back", () => {
  test("older turns are added to the loaded ones, newest first, none twice", () => {
    const loaded = [NEWEST_FIRST[0], NEWEST_FIRST[1], NEWEST_FIRST[2]];
    const merged = mergeTurns(loaded, [NEWEST_FIRST[2], NEWEST_FIRST[3], NEWEST_FIRST[4]]);
    expect(merged.map((t) => t.id)).toEqual([5, 4, 3, 2, 1]);
    expect(mergeTurns([], [])).toEqual([]);
  });

  test("the cursor is the oldest id, and there is none without turns", () => {
    expect(oldestId(NEWEST_FIRST)).toBe(1);
    expect(oldestId([NEWEST_FIRST[0], NEWEST_FIRST[2]])).toBe(3);
    expect(oldestId([])).toBeUndefined();
  });
});

describe("a turn as HTML", () => {
  test("the time, the person's words in 「」 and Raphael's in 《》", () => {
    const html = turnHTML(turn(5, "2026-10-20T09:12:00+08:00", { question: "今天台北天氣如何", text: "台北現在 24 度。" }), false);
    expect(html).toContain('<div class="turn-time num">09:12</div>');
    expect(html).toContain('<div class="turn-q">「今天台北天氣如何」</div>');
    expect(html).toContain('<div class="turn-a">《答。台北現在 24 度。》</div>');
    expect(html).toContain('data-id="5"');
    expect(html).toContain('data-status="done"');
    expect(html).not.toContain("turn-detail");
    expect(html).not.toContain("data-act=");
  });

  test("an English turn reads 《Answer. …》", () => {
    const html = turnHTML(turn(5, "2026-10-20T09:12:00+08:00", { question: "what time is it", prefix: "Answer", text: "It is 9:12." }), false);
    expect(html).toContain("「what time is it」");
    expect(html).toContain("《Answer. It is 9:12.》");
  });

  test("a turn with a detail has a closed detail and a button to open it", () => {
    const html = turnHTML(turn(5, "2026-10-20T09:12:00+08:00", { detail: "第一行\n第二行" }), false);
    expect(html).toContain('<div class="turn-detail" id="turn-detail-5" hidden>第一行\n第二行</div>');
    expect(html).toContain('data-act="detail" aria-expanded="false" aria-controls="turn-detail-5"');
    expect(html).toContain('aria-label="詳細"');
  });

  test("an open detail is shown and says so", () => {
    const html = turnHTML(turn(5, "2026-10-20T09:12:00+08:00", { detail: "更多" }), true);
    expect(html).toContain('<div class="turn-detail" id="turn-detail-5">更多</div>');
    expect(html).toContain('aria-expanded="true"');
  });

  test("a turn without a question shows only the answer", () => {
    const html = turnHTML(turn(5, "2026-10-20T09:12:00+08:00", { question: "  " }), false);
    expect(html).not.toContain("turn-q");
    expect(html).toContain("turn-a");
  });

  test("a missing prefix leaves 《text》", () => {
    expect(turnHTML(turn(5, "2026-10-20T09:12:00+08:00", { prefix: "" }), false)).toContain("《回答 5》");
  });

  test("everything that came from outside is escaped", () => {
    const evil = '<img src=x onerror="alert(1)">';
    const html = turnHTML(
      turn(5, "2026-10-20T09:12:00+08:00", { question: evil, prefix: "<b>", text: evil, detail: evil, status: '"><script>' }),
      true,
    );
    expect(html).not.toContain("<img");
    expect(html).not.toContain("<b>");
    expect(html).not.toContain("<script>");
    expect(html).toContain("&lt;img src=x onerror=&quot;alert(1)&quot;&gt;");
    expect(html).toContain('data-status="&quot;&gt;&lt;script&gt;"');
  });

  test("the days: a heading and a card of turns for each, with the open ones open", () => {
    const html = daysHTML(groupByDay(NEWEST_FIRST.map((t) => ({ ...t, detail: `d${t.id}` })), now), new Set([4]));
    expect(html.match(/<section class="group day">/g)).toHaveLength(4);
    expect(html).toContain("<h3>昨天</h3>");
    expect(html).toContain('id="turn-detail-4">d4</div>');
    expect(html).toContain('id="turn-detail-3" hidden>d3</div>');
    expect(html.indexOf("今天")).toBeGreaterThan(html.indexOf("昨天")); // today is below yesterday
    expect(daysHTML([], new Set())).toBe("");
  });
});

describe("the small list of the home page", () => {
  test("today shows the time, yesterday and earlier the day", () => {
    expect(turnStamp(at("2026-10-20T09:12:00+08:00"), now)).toBe("09:12");
    expect(turnStamp(at("2026-10-19T09:12:00+08:00"), now)).toBe("昨天");
    expect(turnStamp(at("2026-10-17T09:12:00+08:00"), now)).toBe("10 月 17 日");
  });

  test("a mini turn has the stamp, the words and the answer, escaped", () => {
    const html = miniTurnHTML(turn(5, "2026-10-20T09:12:00+08:00", { question: "<i>", text: "好" }), now);
    expect(html).toContain('<div class="mini-time num">09:12</div>');
    expect(html).toContain('<div class="mini-q">「&lt;i&gt;」</div>');
    expect(html).toContain('<div class="mini-a">《答。好》</div>');
    expect(miniTurnHTML(turn(5, "2026-10-20T09:12:00+08:00", { question: "" }), now)).not.toContain("mini-q");
  });
});
