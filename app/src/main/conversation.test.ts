import { afterEach, describe, expect, test } from "vitest";
import { setLocale } from "./i18n";
import {
  TYPED,
  VOICE,
  hasDetail,
  highlightHTML,
  mergeTurns,
  oldestId,
  refreshTurns,
  sourceOf,
  threadItems,
  turnMatches,
} from "./conversation";
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
    session_id: `press-${id}`,
    source: "voice",
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

describe("one thread", () => {
  test("every turn in one thread, oldest first, whatever its source; each day under its heading", () => {
    const items = threadItems(
      [
        turn(4, "2026-10-19T23:58:00+08:00", { source: "telegram", session_id: "chat-7" }),
        ...NEWEST_FIRST.filter((t) => t.id !== 4),
        turn(6, "2026-10-20T09:30:00+08:00", { source: "app" }),
      ],
      now,
    );
    expect(items.map((item) => (item.kind === "day" ? item.label : item.turn.id))).toEqual([
      "2025 年 12 月 31 日", 1, "10 月 17 日", 2, "昨天", 3, 4, "今天", 5, 6,
    ]);
    // the keys say which line is which across draws
    expect(items.filter((item) => item.kind === "turn").map((item) => item.key)).toEqual(["turn:1", "turn:2", "turn:3", "turn:4", "turn:5", "turn:6"]);
    expect(items[0].key).toBe("day:2025 年 12 月 31 日");
  });

  test("turns of the same moment keep the order they were written in, and the list given is not changed", () => {
    const copy = [...NEWEST_FIRST];
    const same = threadItems([turn(9, "2026-10-20T09:00:00+08:00"), turn(8, "2026-10-20T09:00:00+08:00")], now);
    expect(same.flatMap((item) => (item.kind === "turn" ? [item.turn.id] : []))).toEqual([8, 9]);
    threadItems(NEWEST_FIRST, now);
    expect(NEWEST_FIRST).toEqual(copy);
    expect(threadItems([], now)).toEqual([]);
  });

  test("the members asked while a turn was answered go inside it, before the answer", () => {
    const msg = (seq: number, when: string, kind = "reply") => ({ seq, kind, text: `m${seq}`, at_ms: at(when) }) as NonNullable<AgentTurn["messages"]>[number];
    const asked = turn(2, "2026-10-20T09:00:00+08:00", { messages: [msg(1, "2026-10-20T09:00:01+08:00", "progress"), msg(2, "2026-10-20T09:00:30+08:00")] });
    const doctor = turn(4, "2026-10-20T09:00:07+08:00", { route: "exchange", source: "member", peer_id: 3 });
    const teacher = turn(6, "2026-10-20T09:00:14+08:00", { route: "exchange", source: "member", peer_id: 1 });
    const later = turn(8, "2026-10-20T09:05:00+08:00", { route: "exchange", source: "member", peer_id: 1 });
    const items = threadItems([later, teacher, doctor, asked], now);
    const lines = items.flatMap((item) => (item.kind === "turn" ? [[item.turn.id, (item.inner ?? []).map((x) => x.id)]] : []));
    // one asked after the answer is a line of its own
    expect(lines).toEqual([[2, [4, 6]], [8, []]]);
    const running = threadItems([{ ...asked, status: "running" }, later], now);
    expect(running.flatMap((item) => (item.kind === "turn" ? [[item.turn.id, (item.inner ?? []).map((x) => x.id)]] : []))).toEqual([[2, [8]]]);
  });

  test("a turn from a core that keeps no source is a spoken one", () => {
    const old = { ...turn(1, "2026-10-20T09:00:00+08:00") };
    delete old.source;
    expect(sourceOf(old)).toBe(VOICE);
    expect(sourceOf(turn(2, "2026-10-20T09:00:00+08:00", { source: "app" }))).toBe(TYPED);
  });
});

describe("the search", () => {
  test("a turn holds the words in what was asked, answered or said at length; width and case do not matter", () => {
    const t1 = turn(1, "2026-10-20T09:00:00+08:00", { question: "台北天氣", text: "Rainy in TAIPEI", detail: "帶傘" });
    expect(turnMatches(t1, "台北")).toBe(true);
    expect(turnMatches(t1, "taipei")).toBe(true);
    expect(turnMatches(t1, "ＴＡＩＰＥＩ")).toBe(true);
    expect(turnMatches(t1, "傘")).toBe(true);
    expect(turnMatches(t1, "高雄")).toBe(false);
    expect(turnMatches(t1, "  ")).toBe(true);
  });

  test("the words are marked where they are, and everything else is escaped", () => {
    expect(highlightHTML("「台北下雨，台北好冷」", "台北")).toBe("「<mark>台北</mark>下雨，<mark>台北</mark>好冷」");
    expect(highlightHTML("Rain in Taipei <b>", "TAIPEI")).toBe("Rain in <mark>Taipei</mark> &lt;b&gt;");
    expect(highlightHTML("<i>x</i>", "")).toBe("&lt;i&gt;x&lt;/i&gt;");
    expect(highlightHTML("a<b>c", "<b>")).toBe("a<mark>&lt;b&gt;</mark>c");
  });
});

describe("detail", () => {
  test("a turn has a detail when there is more than its line", () => {
    expect(hasDetail(turn(1, "2026-10-20T09:00:00+08:00", { detail: "更長的說明" }))).toBe(true);
    expect(hasDetail(turn(1, "2026-10-20T09:00:00+08:00", { detail: null }))).toBe(false);
    expect(hasDetail(turn(1, "2026-10-20T09:00:00+08:00", { detail: "   " }))).toBe(false);
    expect(hasDetail(turn(1, "2026-10-20T09:00:00+08:00", { detail: " 回答 1 " }))).toBe(false); // the line again
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

  test("the newest page brought again keeps the older turns only when it reaches back to them", () => {
    const span = (from: number, to: number): AgentTurn[] =>
      Array.from({ length: to - from + 1 }, (_, i) => turn(to - i, "2026-10-20T09:00:00+08:00"));
    const ids = (turns: AgentTurn[]): number[] => turns.map((t) => t.id);
    // 1..50 loaded, 51..60 written meanwhile: the page 11..60 overlaps them, so 1..10 stay
    const joined = refreshTurns(span(1, 50), span(11, 60), 50);
    expect(ids(joined.turns)).toEqual(ids(span(1, 60)));
    expect(joined.keptOlder).toBe(true);
    // 1..50 loaded, 51..150 written meanwhile: 101..150 do not reach 1..50, which would leave a gap
    const apart = refreshTurns(span(1, 50), span(101, 150), 50);
    expect(ids(apart.turns)).toEqual(ids(span(101, 150)));
    expect(apart.keptOlder).toBe(false);
    // a page shorter than a page is the whole history; an empty one is no history
    expect(refreshTurns(span(1, 50), span(40, 60), 50)).toEqual({ turns: span(40, 60), keptOlder: false });
    expect(refreshTurns(span(1, 50), [], 50)).toEqual({ turns: [], keptOlder: false });
  });
});
