// @vitest-environment happy-dom
// The 拉斐爾 page as a whole, in a page of its own (happy-dom), with the app and the core faked: what
// it asks the app when keys and events come, and what it shows.
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import type { AgentTurn, AppContext, MainEvent } from "../types";

const api = vi.hoisted(() => ({
  agentHistoryList: vi.fn(),
  agentHistoryClear: vi.fn(),
  agentHistorySearch: vi.fn(),
  agentAskText: vi.fn(),
  agentQuestionOpen: vi.fn(),
  agentQuestionAnswer: vi.fn(),
  agentVoice: vi.fn(),
  workerCancel: vi.fn(),
  workerRerun: vi.fn(),
}));
vi.mock("../api", () => api);

import { Cleared } from "../cleared";
import { createAgent } from "./agent";

function turn(id: number, more: Partial<AgentTurn> = {}): AgentTurn {
  return {
    id,
    created_at_ms: Date.parse("2026-10-20T10:00:00+08:00") + id * 1000,
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

/** `count` turns, newest first, the newest with id `top`. */
const page = (top: number, count: number): AgentTurn[] => Array.from({ length: count }, (_, i) => turn(top - i));

/** A promise and the hands that settle it. */
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (err: unknown) => void;
  const promise = new Promise<T>((res, rej) => ((resolve = res), (reject = rej)));
  return { promise, resolve, reject };
}

let send: (event: MainEvent) => void = () => {};

function mount() {
  const app = {
    go: vi.fn(),
    device: () => ({ dictation_mode: "hold" }),
    onEvent(handler: (event: MainEvent) => void) {
      send = handler;
      return () => {};
    },
    undo: { begin: vi.fn() },
    cleared: new Cleared(),
  } as unknown as AppContext;
  const agent = createAgent(app);
  document.body.append(agent.el);
  agent.show();
  return agent;
}

const flush = async (): Promise<void> => {
  for (let i = 0; i < 10; i++) await Promise.resolve();
};

beforeEach(() => {
  document.body.innerHTML = "";
  for (const fn of Object.values(api)) fn.mockReset();
  api.agentQuestionOpen.mockResolvedValue(null);
  api.agentVoice.mockResolvedValue(undefined);
});

afterEach(() => {
  vi.useRealTimers();
});

describe("the voice button held down with a key", () => {
  test("letting the key go ends the recording even when the focus has left the button meanwhile", async () => {
    api.agentHistoryList.mockResolvedValue({ turns: [] });
    mount();
    await flush();
    const button = document.querySelector<HTMLButtonElement>(".cmp-voice")!;
    button.focus();
    button.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    expect(api.agentVoice).toHaveBeenCalledWith("down");
    // the picture went up and the button lost the focus: the release comes to the page itself
    button.blur();
    document.body.dispatchEvent(new KeyboardEvent("keyup", { key: "Enter", bubbles: true }));
    expect(api.agentVoice.mock.calls).toEqual([["down"], ["up"]]);
    // another key's release, or a second one, sends nothing more
    document.body.dispatchEvent(new KeyboardEvent("keyup", { key: "Enter", bubbles: true }));
    expect(api.agentVoice).toHaveBeenCalledTimes(2);
  });

  test("a release on the button itself is sent once", async () => {
    api.agentHistoryList.mockResolvedValue({ turns: [] });
    mount();
    await flush();
    const button = document.querySelector<HTMLButtonElement>(".cmp-voice")!;
    button.dispatchEvent(new KeyboardEvent("keydown", { key: " ", bubbles: true }));
    button.dispatchEvent(new KeyboardEvent("keyup", { key: " ", bubbles: true }));
    expect(api.agentVoice.mock.calls).toEqual([["down"], ["up"]]);
  });
});

describe("loading the earlier turns", () => {
  test("an earlier page that fails after a newer refresh began does not keep paging up blocked", async () => {
    vi.useFakeTimers();
    const earlier = deferred<{ turns: AgentTurn[] }>();
    const newest = deferred<{ turns: AgentTurn[] }>();
    api.agentHistoryList
      .mockResolvedValueOnce({ turns: page(200, 50) }) // the first page; it does not fill the window, so the earlier one is asked at once
      .mockReturnValueOnce(earlier.promise)
      .mockReturnValueOnce(newest.promise)
      .mockResolvedValue({ turns: page(150, 50) });
    mount();
    await flush();
    expect(api.agentHistoryList).toHaveBeenCalledTimes(2);
    expect(api.agentHistoryList.mock.calls[1][0]).toEqual({ limit: 50, before_id: 151 });
    // a turn is written: the newest turns are brought again while the earlier ones are on their way
    send({ type: "AgentTurn", turn_id: 201 });
    await vi.advanceTimersByTimeAsync(200);
    expect(api.agentHistoryList).toHaveBeenCalledTimes(3);
    earlier.reject(new Error("the core went away"));
    await flush();
    newest.resolve({ turns: page(201, 50) });
    await flush();
    // the page can page up again: the earlier ones are asked once more
    const scroller = document.querySelector<HTMLElement>(".ra-scroll")!;
    scroller.dispatchEvent(new Event("scroll"));
    await flush();
    expect(api.agentHistoryList.mock.calls.some((call, i) => i > 2 && call[0]?.before_id !== undefined)).toBe(true);
  });
});

describe("a question said aloud", () => {
  test("its 「正在辨識」 line goes after the longest wait even when nothing more comes from the core", async () => {
    vi.useFakeTimers();
    api.agentHistoryList.mockResolvedValue({ turns: page(10, 3) });
    mount();
    await flush();
    send({ type: "Dictation", phase: "processing", agent: true });
    expect(document.querySelector('[data-key="hearing"]')).not.toBeNull();
    // the core crashed: no turn, no last phase, no refresh
    await vi.advanceTimersByTimeAsync(120_000 + 10);
    expect(document.querySelector('[data-key="hearing"]')).toBeNull();
  });
});
