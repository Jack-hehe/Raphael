import { afterEach, describe, expect, test } from "vitest";
import { setLocale } from "./i18n";
import {
  SESSION_GAP_MS,
  groupSessions,
  hasDetail,
  mergeTurns,
  miniTurnHTML,
  oldestId,
  sessionBusy,
  sessionDays,
  sessionTitle,
  sessionWith,
  turnStamp,
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

describe("conversations", () => {
  test("each press of the key has its own session id, so spoken turns close together are one conversation", () => {
    const sessions = groupSessions([
      turn(3, "2026-10-20T09:20:00+08:00"),
      turn(2, "2026-10-20T09:05:00+08:00"),
      turn(1, "2026-10-20T09:00:00+08:00"),
    ]);
    expect(sessions).toHaveLength(1);
    expect(sessions[0].turns.map((t) => t.id)).toEqual([1, 2, 3]); // oldest first, as it was said
    expect(sessions[0].source).toBe("voice");
  });

  test("half an hour of silence starts a new conversation, and the newest is listed first", () => {
    const sessions = groupSessions([
      turn(3, "2026-10-20T10:00:01+08:00"),
      turn(2, "2026-10-20T09:30:00+08:00"),
      turn(1, "2026-10-20T09:00:00+08:00"),
    ]);
    // 09:00 -> 09:30 is exactly the gap: still the same conversation; 09:30 -> 10:00:01 is not.
    expect(SESSION_GAP_MS).toBe(30 * 60_000);
    expect(sessions.map((s) => s.turns.map((t) => t.id))).toEqual([[3], [1, 2]]);
  });

  test("a chat app's turns are a conversation of their own chat, apart from the spoken ones", () => {
    const sessions = groupSessions([
      turn(4, "2026-10-20T09:04:00+08:00", { source: "telegram", session_id: "chat-7" }),
      turn(3, "2026-10-20T09:03:00+08:00", { source: "telegram", session_id: "chat-9" }),
      turn(2, "2026-10-20T09:02:00+08:00"),
      turn(1, "2026-10-20T09:01:00+08:00", { source: "telegram", session_id: "chat-7" }),
    ]);
    expect(sessions.map((s) => [s.source, s.turns.map((t) => t.id)])).toEqual([
      ["telegram", [1, 4]],
      ["telegram", [3]],
      ["voice", [2]],
    ]);
  });

  test("a turn from a core that keeps no source is a spoken one", () => {
    const old = { ...turn(1, "2026-10-20T09:00:00+08:00") };
    delete old.source;
    delete old.session_id;
    expect(groupSessions([old])[0].source).toBe("voice");
  });

  test("a conversation keeps its key when turns are added at its end", () => {
    const first = groupSessions([turn(1, "2026-10-20T09:00:00+08:00")])[0].key;
    const later = groupSessions([turn(2, "2026-10-20T09:10:00+08:00"), turn(1, "2026-10-20T09:00:00+08:00")])[0].key;
    expect(later).toBe(first);
  });

  test("the list is grouped by the day of each conversation's last turn: today, yesterday, then dates", () => {
    const days = sessionDays(groupSessions(NEWEST_FIRST), now);
    expect(days.map((d) => d.label)).toEqual(["今天", "昨天", "10 月 17 日", "2025 年 12 月 31 日"]);
    // 23:58 and 09:00 of yesterday are far apart: two conversations under the same day
    expect(days[1].sessions.map((s) => s.turns.map((t) => t.id))).toEqual([[4], [3]]);
  });

  test("a conversation is called by its first question; one that was not heard has a word for it", () => {
    const [session] = groupSessions([turn(2, "2026-10-20T09:01:00+08:00"), turn(1, "2026-10-20T09:00:00+08:00", { question: "  幫我\n查   天氣 " })]);
    expect(sessionTitle(session)).toBe("幫我 查 天氣");
    const [silent] = groupSessions([turn(1, "2026-10-20T09:00:00+08:00", { question: " " })]);
    expect(sessionTitle(silent)).toBe("（沒有聽清楚的問題）");
  });

  test("the conversation of a turn is found by the turn, and one with a task under way says so", () => {
    const sessions = groupSessions([
      turn(2, "2026-10-20T09:01:00+08:00", { task: { id: 7, state: "running", started_at_ms: 1, finished_at_ms: null } }),
      turn(1, "2026-10-19T09:00:00+08:00"),
    ]);
    expect(sessionWith(sessions, 1)?.turns[0].id).toBe(1);
    expect(sessionWith(sessions, 99)).toBeUndefined();
    expect(sessionBusy(sessions[0])).toBe(true);
    expect(sessionBusy(sessions[1])).toBe(false);
  });

  test("nothing makes no conversations, and the list that was given is not changed", () => {
    const copy = [...NEWEST_FIRST];
    groupSessions(NEWEST_FIRST);
    expect(NEWEST_FIRST).toEqual(copy);
    expect(groupSessions([])).toEqual([]);
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
