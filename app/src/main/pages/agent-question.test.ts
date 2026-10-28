/// <reference types="node" />
import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, describe, expect, test, vi } from "vitest";

const invoke = vi.hoisted(() => vi.fn(async () => undefined));
vi.mock("@tauri-apps/api/core", () => ({ invoke, Channel: class {} }));

import { agentQuestionAnswer } from "../api";
import { setLocale } from "../i18n";
import type { AgentQuestion } from "../types";
import { askLabels, openQuestion } from "./agent";

afterEach(() => {
  setLocale("zh-TW");
  invoke.mockClear();
});

const question = (over: Partial<AgentQuestion> = {}): AgentQuestion => ({
  id: "q1", prefix: "問", text: "要讓我操作 Calculator 嗎？", tier: "T2", timeout_ms: 30_000, ...over,
});
const APP_QUESTION = { yes: "允許一次", no: "不要", always: "一律允許" };

describe("the answer to a question of the 拉斐爾 page", () => {
  test("is sent as it always was: the id and whether it was approved", async () => {
    await agentQuestionAnswer("q1", true);
    await agentQuestionAnswer("q1", false);
    expect(invoke.mock.calls).toEqual([
      ["agent_question_answer", { id: "q1", approved: true }],
      ["agent_question_answer", { id: "q1", approved: false }],
    ]);
  });

  test("the third answer is YES with always, and only it sends always", async () => {
    await agentQuestionAnswer("q1", true, true);
    expect(invoke.mock.calls).toEqual([["agent_question_answer", { id: "q1", approved: true, always: true }]]);
  });

  test("the page wires the button to that answer", () => {
    const page = readFileSync(path.resolve(import.meta.dirname, "agent.ts"), "utf8");
    expect(page).toContain('data-act="always"');
    expect(page).toContain('case "always":');
    expect(page).toContain("void answer(true, true)");
    expect(page).toContain("api.agentQuestionAnswer(question.id, true, true)");
  });
});

describe("the question card", () => {
  test("a question with the third answer has three labels, in the question's words", () => {
    expect(askLabels(openQuestion(question(APP_QUESTION)))).toEqual({ yes: "允許一次", no: "不要", always: "一律允許" });
  });

  test("a plain question keeps the page's 是 and 否 and has no third button", () => {
    expect(askLabels(question())).toEqual({ yes: "是", no: "否", always: null });
    expect(askLabels(question({ yes: "Yes", no: "No" }))).toEqual({ yes: "是", no: "否", always: null });
    expect(askLabels(null)).toEqual({ yes: "是", no: "否", always: null });
    setLocale("en");
    expect(askLabels(question())).toEqual({ yes: "Yes", no: "No", always: null });
  });

  test("a question with the third answer is never held, whatever tier it says", () => {
    expect(openQuestion(question({ ...APP_QUESTION, tier: "T3" })).tier).toBe("T2");
    expect(openQuestion(question({ tier: "T3" })).tier).toBe("T3");
    expect(openQuestion(question({ always: "" })).always).toBeUndefined();
  });

  test("the buttons stand no, always, yes", () => {
    const page = readFileSync(path.resolve(import.meta.dirname, "agent.ts"), "utf8");
    const card = /askCard\.innerHTML =([\s\S]*?);\r?\n/.exec(page)![1];
    expect(card.indexOf("ask-no")).toBeLessThan(card.indexOf("ask-always"));
    expect(card.indexOf("ask-always")).toBeLessThan(card.indexOf("ask-yes"));
    expect(card).toMatch(/ask-always" data-act="always" hidden/);
  });
});
