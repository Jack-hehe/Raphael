import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { AgentTurn } from "./types";
import { ReplyPacing } from "./reply-pacing";

const NOW = Date.parse("2026-10-24T03:15:00Z");
const turn = (more: Partial<AgentTurn> = {}): AgentTurn => ({ id: 1, session_id: "one", question: "Question", route: "chat", prefix: "", text: "Final", detail: null, status: "done", created_at_ms: NOW, messages: [], ...more });
const burst = () => turn({ messages: ["First", "Second", "x".repeat(300)].map((text, index) => ({ seq: index + 1, kind: "reply", text, at_ms: NOW })) });
beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(NOW); });
afterEach(() => vi.useRealTimers());

test("a live reply burst shows the first immediately then length-based pauses and typing", () => {
  const changed = vi.fn(), pacing = new ReplyPacing(changed, () => false);
  const next = burst();
  pacing.observe([turn({ status: "running" })], [next], true);
  expect(pacing.view(next).messages?.map(message => message.text)).toEqual(["First"]);
  expect(pacing.waiting(1)).toBe(true);
  vi.advanceTimersByTime(557); expect(changed).not.toHaveBeenCalled();
  vi.advanceTimersByTime(1);
  expect(pacing.view(next).messages).toHaveLength(2);
  vi.advanceTimersByTime(2199); expect(pacing.waiting(1)).toBe(true);
  vi.advanceTimersByTime(1); expect(pacing.view(next).messages).toHaveLength(3);
  expect(pacing.waiting(1)).toBe(false);
});

test("history, old completions, search and reduced motion reveal immediately", () => {
  for (const [previous, next, live, reduced] of [
    [[], burst(), false, false],
    [[turn({ status: "done" })], burst(), true, false],
    [[turn({ status: "running" })], burst(), false, false],
    [[turn({ status: "running" })], burst(), true, true],
    [[turn({ status: "running" })], turn({ ...burst(), messages: burst().messages!.map(message => ({ ...message, at_ms: NOW - 6000 })) }), true, false],
  ] as const) {
    const pacing = new ReplyPacing(vi.fn(), () => reduced);
    pacing.observe(previous, [next], live);
    expect(pacing.view(next).messages).toHaveLength(3);
    expect(pacing.waiting(1)).toBe(false);
  }
});

test("switching away flushes timers and returning cannot replay completed messages", () => {
  const changed = vi.fn(), pacing = new ReplyPacing(changed, () => false), next = burst();
  pacing.observe([turn({ status: "running" })], [next], true);
  pacing.flush();
  expect(pacing.view(next).messages).toHaveLength(3);
  vi.runAllTimers(); expect(changed).not.toHaveBeenCalled();
  pacing.observe([], [next], false);
  expect(pacing.waiting(1)).toBe(false);
});

test("a submitted request can arrive as a complete burst between refreshes", () => {
  const pacing = new ReplyPacing(vi.fn(), () => false), next = burst();
  pacing.observe([], [next], true, new Set(["one"]));
  expect(pacing.view(next).messages).toHaveLength(1);
  pacing.flush();
});

test("a replacement reusing a deleted turn id cannot inherit the old reveal timer", () => {
  const pacing = new ReplyPacing(vi.fn(), () => false), old = burst();
  pacing.observe([turn({ status: "running" })], [old], true);
  const replacement = turn({ session_id: "replacement", messages: [{ seq: 1, kind: "reply", text: "Corrected answer", at_ms: NOW }] });
  pacing.observe([old], [replacement], true, new Set(["replacement"]));
  expect(pacing.waiting(1)).toBe(false);
  expect(pacing.view(replacement)).toEqual(replacement);
});
