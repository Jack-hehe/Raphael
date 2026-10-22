import { afterEach, describe, expect, test } from "vitest";
import { setLocale } from "../i18n";
import type { AgentTurn, HistoryItem, MainEvent } from "../types";
import type { LessonId } from "./abilities";
import { CONFLICT_MS, HOLD_MS, type Lesson, type PracticeContext, type PracticeEvent, conflictSuspected, feed, retry, startLesson } from "./practice";

afterEach(() => setLocale("zh-TW"));

const TOGGLE: PracticeContext = { mode: "toggle" };
const HOLD: PracticeContext = { mode: "hold" };

const key = (kind: Extract<MainEvent, { type: "Hotkey" }>["kind"], now: number): PracticeEvent => ({ type: "main", event: { type: "Hotkey", kind }, now });
const phase = (p: Extract<MainEvent, { type: "Dictation" }>["phase"], agent = false, now = 0): PracticeEvent => ({
  type: "main",
  event: { type: "Dictation", phase: p, agent },
  now,
});

const item = (over: Partial<HistoryItem> = {}): HistoryItem => ({
  id: 11,
  created_at_ms: 5_000,
  kind: "dictation",
  raw_text: "呃 明天 那個 開會",
  text: "明天開會。",
  cleaned: true,
  fallback_reason: null,
  target_app: null,
  audio_seconds: 3,
  stt_ms: 400,
  llm_ms: 600,
  model: null,
  cost_usd: 0,
  ...over,
});

const turn = (over: Partial<AgentTurn> = {}): AgentTurn => ({
  id: 7,
  created_at_ms: 5_000,
  question: "q",
  route: "answer",
  prefix: "答",
  text: "明天台北會下雨。",
  detail: null,
  status: "done",
  ...over,
});

const play = (lesson: Lesson, ctx: PracticeContext, ...events: PracticeEvent[]): Lesson => events.reduce((l, e) => feed(l, e, ctx), lesson);
/** A lesson in which the person has begun: a dictation or question started after it did. */
const begin = (id: LessonId, baselineItem: number | null = 10, baselineTurn: number | null = 6): Lesson => ({ ...startLesson(id, 1_000, baselineItem, baselineTurn), started: true });
const fresh = (id: LessonId): Lesson => startLesson(id, 1_000, 10, 6);

describe("the key lesson", () => {
  test("toggle mode: a press and its release are enough", () => {
    const l = play(begin("key"), TOGGLE, key("dictation_down", 2_000));
    expect(l.phase).toBe("pressed");
    expect(play(l, TOGGLE, key("dictation_up", 2_050)).phase).toBe("ok");
  });

  test("hold mode: held for 0.6 s counts", () => {
    const l = play(begin("key"), HOLD, key("dictation_down", 2_000), key("dictation_up", 2_000 + HOLD_MS));
    expect(l.phase).toBe("ok");
  });

  test("hold mode: a tap shorter than 0.6 s is told and does not count", () => {
    const l = play(begin("key"), HOLD, key("dictation_down", 2_000), key("dictation_up", 2_000 + HOLD_MS - 1));
    expect(l.phase).toBe("idle");
    expect(l.miss).toBe("tooShort");
    // and the next, longer one does
    expect(play(l, HOLD, key("dictation_down", 4_000), key("dictation_up", 4_900)).phase).toBe("ok");
  });

  test("a release with no press before it is nothing", () => {
    expect(play(begin("key"), TOGGLE, key("dictation_up", 2_000)).phase).toBe("idle");
  });

  test("the other keys do not count", () => {
    const l = play(begin("key"), TOGGLE, key("agent_down", 2_000), key("paste_last", 2_100), key("cancel", 2_200), phase("listening"));
    expect(l.phase).toBe("idle");
  });
});

describe("the dictation lesson", () => {
  test("goes listening, processing, and is done when the text was pasted", () => {
    let l = play(begin("dictate"), TOGGLE, phase("listening"));
    expect(l.phase).toBe("listening");
    l = play(l, TOGGLE, phase("processing"));
    expect(l.phase).toBe("processing");
    expect(play(l, TOGGLE, phase("pasted")).phase).toBe("ok");
  });

  test("a cancelled dictation is back at the start, with the reason", () => {
    const l = play(begin("dictate"), TOGGLE, phase("listening"), phase("cancelled"));
    expect(l.phase).toBe("idle");
    expect(l.miss).toBe("cancelled");
  });

  test("a failed one is told", () => {
    const l = play(begin("dictate"), TOGGLE, phase("listening"), phase("failed"));
    expect(l.phase).toBe("failed");
    expect(l.miss).toBe("error");
  });

  test("a newer history item is enough, even if the event was missed", () => {
    const l = play(begin("dictate"), TOGGLE, { type: "item", item: item({ id: 11 }) });
    expect(l.phase).toBe("ok");
    expect(l.item?.id).toBe(11);
  });

  test("an old history item does not count", () => {
    expect(play(begin("dictate"), TOGGLE, { type: "item", item: item({ id: 10 }) }).phase).toBe("idle");
    expect(play(begin("dictate"), TOGGLE, { type: "item", item: null }).phase).toBe("idle");
  });

  test("questions are not dictations", () => {
    expect(play(begin("dictate"), TOGGLE, phase("listening", true), phase("pasted", true)).phase).toBe("idle");
  });
});

describe("the cleanup lesson", () => {
  test("pasting is not enough: it waits for the cleaned history item", () => {
    const l = play(begin("cleanup"), TOGGLE, phase("listening"), phase("processing"), phase("pasted"));
    expect(l.phase).toBe("processing");
    const ok = play(l, TOGGLE, { type: "item", item: item() });
    expect(ok.phase).toBe("ok");
    expect(ok.item?.raw_text).toBe("呃 明天 那個 開會");
  });

  test("a cleanup that fell back says so and does not count", () => {
    const l = play(begin("cleanup"), TOGGLE, { type: "item", item: item({ cleaned: false, fallback_reason: "budget" }) });
    expect(l.phase).toBe("failed");
    expect(l.miss).toBe("fallback");
    expect(l.item?.fallback_reason).toBe("budget");
  });

  test("text that was not cleaned is the same as a fallback", () => {
    expect(play(begin("cleanup"), TOGGLE, { type: "item", item: item({ cleaned: false }) }).miss).toBe("fallback");
  });

  test("an item from before the lesson never counts", () => {
    expect(play(begin("cleanup"), TOGGLE, { type: "item", item: item({ id: 10 }) }).phase).toBe("idle");
  });

  test("another try after a fallback starts afresh and sees only newer items", () => {
    const failed = play(begin("cleanup"), TOGGLE, { type: "item", item: item({ id: 11, cleaned: false, fallback_reason: "x" }) });
    const again = retry(failed, 9_000);
    expect(again.phase).toBe("idle");
    const begun = play(again, TOGGLE, phase("listening"));
    expect(play(begun, TOGGLE, { type: "item", item: item({ id: 11 }) }).phase).toBe("listening");
    expect(play(begun, TOGGLE, { type: "item", item: item({ id: 12 }) }).phase).toBe("ok");
  });

  test("a lesson that knew no baseline goes by time", () => {
    const l = { ...startLesson("cleanup", 1_000), started: true };
    expect(play(l, TOGGLE, { type: "item", item: item({ id: 1, created_at_ms: 999 }) }).phase).toBe("idle");
    expect(play(l, TOGGLE, { type: "item", item: item({ id: 1, created_at_ms: 1_000 }) }).phase).toBe("ok");
  });
});

describe("what an earlier session finishes", () => {
  test("a dictation that began before the lesson does not count for it", () => {
    // 「跳過」 while a dictation was processing: its pasted event and newer item arrive in the next lesson
    const next = fresh("cleanup");
    const l = play(next, TOGGLE, phase("processing"), phase("pasted"), { type: "item", item: item({ id: 11 }) });
    expect(l.phase).toBe("idle");
    expect(l.item).toBeNull();
    expect(play(fresh("dictate"), TOGGLE, phase("pasted")).phase).toBe("idle");
    expect(play(fresh("dictate"), TOGGLE, { type: "item", item: item({ id: 11 }) }).phase).toBe("idle");
  });

  test("nor does a question that began before the lesson", () => {
    const l = play(fresh("ask"), TOGGLE, phase("processing", true), { type: "turn", turn: turn({ id: 7 }) });
    expect(l.phase).toBe("idle");
    expect(play(fresh("act"), TOGGLE, { type: "turn", turn: turn({ id: 7, route: "command", prefix: "了" }) }).phase).toBe("idle");
  });

  test("one that the lesson saw begin does", () => {
    const l = play(fresh("cleanup"), TOGGLE, phase("listening"), phase("processing"), phase("pasted"), { type: "item", item: item({ id: 11 }) });
    expect(l.phase).toBe("ok");
  });
});

describe("the question lessons", () => {
  test("ask is done by a newer turn with a reply", () => {
    let l = play(begin("ask"), TOGGLE, phase("listening", true), phase("processing", true));
    expect(l.phase).toBe("processing");
    l = play(l, TOGGLE, { type: "turn", turn: turn({ id: 7 }) });
    expect(l.phase).toBe("ok");
    expect(l.turn?.text).toContain("下雨");
  });

  test("an old turn does not count", () => {
    expect(play(begin("ask"), TOGGLE, { type: "turn", turn: turn({ id: 6 }) }).phase).toBe("idle");
    expect(play(begin("ask"), TOGGLE, { type: "turn", turn: null }).phase).toBe("idle");
  });

  test("a turn from Telegram is not the person's voice", () => {
    expect(play(begin("ask"), TOGGLE, { type: "turn", turn: turn({ source: "telegram" }) }).phase).toBe("idle");
  });

  test("a turn that waits for a yes or no is not over", () => {
    expect(play(begin("ask"), TOGGLE, { type: "turn", turn: turn({ status: "confirm" }) }).phase).toBe("idle");
  });

  test("a turn without words is a miss", () => {
    expect(play(begin("ask"), TOGGLE, { type: "turn", turn: turn({ text: " " }) }).miss).toBe("noText");
  });

  test("act needs a command that was done", () => {
    const ok = play(begin("act"), TOGGLE, { type: "turn", turn: turn({ route: "command", prefix: "了", text: "已開啟小算盤。" }) });
    expect(ok.phase).toBe("ok");
  });

  test("act: an answer to a question is the wrong route, and says so", () => {
    const l = play(begin("act"), TOGGLE, { type: "turn", turn: turn({ route: "answer" }) });
    expect(l.phase).toBe("failed");
    expect(l.miss).toBe("notCommand");
  });

  test("act: the word of a refusal is the one of the language of the window", () => {
    setLocale("en");
    const l = play(begin("act"), TOGGLE, { type: "turn", turn: turn({ route: "command", prefix: "Negative", text: "I could not find it." }) });
    expect(l.miss).toBe("refused");
    expect(play(begin("act"), TOGGLE, { type: "turn", turn: turn({ route: "command", prefix: "Understood", text: "Done." }) }).phase).toBe("ok");
  });

  test("act: a command that Raphael refused is not done", () => {
    const l = play(begin("act"), TOGGLE, { type: "turn", turn: turn({ route: "command", prefix: "否", text: "找不到這個程式。" }) });
    expect(l.miss).toBe("refused");
  });

  test("the question lessons ignore dictations", () => {
    expect(play(begin("ask"), TOGGLE, phase("listening", false)).phase).toBe("idle");
  });

  test("a lesson that is done stays done", () => {
    const l = play(begin("ask"), TOGGLE, { type: "turn", turn: turn() }, phase("cancelled", true), { type: "turn", turn: turn({ id: 8, text: "" }) });
    expect(l.phase).toBe("ok");
  });
});

describe("a key that another program holds", () => {
  const combo = (now: number): PracticeEvent => ({ type: "combo", now });

  test("is suspected when the window saw the combination and the app did not hear it for 3 s", () => {
    const l = play(begin("ask"), TOGGLE, combo(2_000));
    expect(conflictSuspected(l, 2_000 + CONFLICT_MS - 1)).toBe(false);
    expect(conflictSuspected(l, 2_000 + CONFLICT_MS)).toBe(true);
  });

  test("is not suspected when the app heard the key", () => {
    const l = play(begin("ask"), TOGGLE, combo(2_000), key("agent_down", 2_100));
    expect(conflictSuspected(l, 9_000)).toBe(false);
    // and a key heard before the window saw it clears it for good
    expect(conflictSuspected(play(begin("act"), TOGGLE, key("agent_down", 1_500), combo(2_000)), 9_000)).toBe(false);
  });

  test("only the question lessons look for it", () => {
    expect(conflictSuspected(play(begin("dictate"), TOGGLE, combo(2_000)), 9_000)).toBe(false);
  });

  test("a retry keeps what is known about the key", () => {
    const heard = play(begin("ask"), TOGGLE, key("agent_down", 1_500));
    expect(retry(heard, 3_000).agentHeard).toBe(true);
  });
});

