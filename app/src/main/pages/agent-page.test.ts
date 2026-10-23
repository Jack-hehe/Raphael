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
  openLink: vi.fn(),
  core: vi.fn(),
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

/** The ids of the turns the thread shows, oldest first. */
const shownIds = (): number[] => [...document.querySelectorAll<HTMLElement>(".tn[data-id]")].map((line) => Number(line.dataset.id));

describe("coming back to the page", () => {
  test("turns written while it was away are paged back to without leaving a gap", async () => {
    // A history of `size` turns, ids 1..size, answered a page at a time as the core does.
    let size = 50;
    api.agentHistoryList.mockImplementation(async (q: { limit: number; before_id?: number }) => {
      const top = Math.min(size, (q.before_id ?? Infinity) - 1);
      return { turns: page(top, Math.max(0, Math.min(q.limit, top))) };
    });
    const agent = mount();
    for (let i = 0; i < 5; i++) await flush();
    expect(shownIds()).toHaveLength(50); // 1..50, and the earlier page said there is nothing before
    agent.hide();
    size = 150; // a hundred turns while the page was not shown
    agent.show();
    for (let i = 0; i < 10; i++) await flush();
    expect(shownIds()).toEqual(Array.from({ length: 150 }, (_, i) => i + 1));
  });
});

describe("the search", () => {
  test("shows what the core found in the whole history without raw steps", async () => {
    vi.useFakeTimers();
    api.agentHistoryList.mockResolvedValueOnce({ turns: page(200, 50) }).mockResolvedValue({ turns: [] });
    const step = { kind: "tool", tool: "search", server: "Raphael", outcome: "ok", ms: 800, reason: null, at_ms: 0 };
    api.agentHistorySearch.mockResolvedValue({ turns: [turn(3, { question: "出差的行程", steps: [step] })] });
    mount();
    await flush();
    document.querySelector<HTMLElement>('[data-act="search"]')!.click();
    const field = document.querySelector<HTMLInputElement>(".ra-find-input")!;
    field.value = "出差";
    field.dispatchEvent(new Event("input"));
    await vi.advanceTimersByTimeAsync(250);
    expect(api.agentHistorySearch).toHaveBeenCalledWith("出差");
    expect(shownIds()).toEqual([3]); // far behind the loaded page
    expect(document.querySelector('.tn[data-id="3"] [data-act="steps"]')).toBeNull();
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

describe("a question of the background", () => {
  test("its gold card is in the slot above the composer, and is answered from here", async () => {
    api.agentHistoryList.mockResolvedValue({ turns: page(10, 2) });
    const question = { id: "q-1", task_id: 4, member_id: 0, prefix: "問", text: "是否讓「Notes」執行「note」？", tier: "T2", created_at_ms: 1, expires_at_ms: 2 };
    let waiting = [question];
    api.core.mockImplementation(async (method: string) => {
      if (method === "background.questions.list") return { questions: waiting };
      if (method === "background.answer") {
        waiting = [];
        return { status: "approved" };
      }
      return {};
    });
    mount();
    await flush();
    expect(api.core).toHaveBeenCalledWith("background.questions.list", { member_id: 0 });
    const card = document.querySelector<HTMLElement>('[data-mount="background-questions"] .bgq-card')!;
    expect(card).not.toBeNull();
    expect(card.textContent).toContain("《問。是否讓「Notes」執行「note」？》");
    card.querySelector<HTMLButtonElement>(".bgq-yes")!.click();
    await flush();
    expect(api.core).toHaveBeenCalledWith("background.answer", { task_id: 4, question_id: "q-1", approved: true });
    expect(document.querySelector<HTMLElement>(".bgq-list")!.hidden).toBe(true);
  });
});

describe("a continuous typed turn", () => {
  test("new progress replaces the running turn and the final reply removes the typing dots", async () => {
    vi.useFakeTimers();
    let live = turn(1, { source: "app", status: "running", text: "", messages: [] });
    api.agentHistoryList.mockImplementation(async () => ({ turns: [live] }));
    mount();
    await flush();
    expect(document.querySelectorAll('.tn[data-id="1"] .think i')).toHaveLength(3);
    live = { ...live, messages: [{ seq: 1, kind: "progress", text: "《告。先確認今天的日期。》", at_ms: 1 }] };
    send({ type: "AgentTurn", turn_id: 1 });
    await vi.advanceTimersByTimeAsync(200);
    expect(document.querySelector('.tn[data-id="1"] .progress')?.textContent).toBe("《告。先確認今天的日期。》");
    live = { ...live, status: "done", text: "今天星期五。", messages: [...live.messages!, { seq: 2, kind: "reply", text: "《答。今天星期五。》", at_ms: 2 }] };
    send({ type: "AgentTurn", turn_id: 1 });
    await vi.advanceTimersByTimeAsync(200);
    expect(document.querySelectorAll('.tn[data-id="1"] .ans')).toHaveLength(2);
    expect(document.querySelector('.tn[data-id="1"] .think')).toBeNull();
  });

  test("the permission card stays inline after another history refresh and remains answerable", async () => {
    vi.useFakeTimers();
    api.agentHistoryList.mockResolvedValue({ turns: [turn(1, { source: "app", status: "confirm", messages: [{ seq: 1, kind: "progress", text: "《告。先存下你的範例。》", at_ms: 1 }] })] });
    api.agentQuestionAnswer.mockResolvedValue(undefined);
    mount();
    await flush();
    send({ type: "AgentQuestion", id: "inline-q", prefix: "問", text: "儲存嗎？", tier: "T2", timeout_ms: 30000 });
    const card = document.querySelector('.turn-question .ask-card');
    expect(card).not.toBeNull();
    send({ type: "AgentTurn", turn_id: 1 });
    await vi.advanceTimersByTimeAsync(200);
    expect(document.querySelector('.turn-question .ask-card')).toBe(card);
    card!.querySelector<HTMLButtonElement>(".ask-yes")!.click();
    await flush();
    expect(api.agentQuestionAnswer).toHaveBeenCalledWith("inline-q", true);
  });

  test("clicking the thinking header expands and collapses the thinking block, and media actions open lightbox, expand PDF, and call openLink", async () => {
    api.openLink.mockResolvedValue(undefined);
    api.agentHistoryList.mockResolvedValue({
      turns: [
        turn(1, {
          source: "app",
          status: "done",
          text: "完成",
          messages: [
            { seq: 1, kind: "thinking", text: "先分析使用者的問題。", at_ms: 1000 },
            {
              seq: 2,
              kind: "media",
              text: JSON.stringify({
                type: "image",
                url: "https://example.com/photo.png",
                title: "東京鐵塔",
                site: "NHK",
              }),
              at_ms: 2000,
            },
            {
              seq: 3,
              kind: "media",
              text: JSON.stringify({
                type: "pdf",
                url: "https://example.com/doc.pdf",
                title: "報告.pdf",
                site: "NHK",
              }),
              at_ms: 3000,
            },
            { seq: 4, kind: "reply", text: "這是你要的圖片與報告。", at_ms: 4000 },
          ],
        }),
      ],
    });
    mount();
    await flush();

    // Thinking block starts collapsed on a done turn, and expands on click
    const thinkBtn = document.querySelector<HTMLButtonElement>('[data-act="thinking"]')!;
    const thinkBody = document.querySelector<HTMLElement>(".think-body")!;
    expect(thinkBtn.getAttribute("aria-expanded")).toBe("false");
    expect(thinkBody.hidden).toBe(true);
    thinkBtn.click();
    expect(thinkBtn.getAttribute("aria-expanded")).toBe("true");
    expect(thinkBody.hidden).toBe(false);
    thinkBtn.click();
    expect(thinkBody.hidden).toBe(true);

    // Clicking image opens in-app lightbox, Escape closes it
    document.querySelector<HTMLButtonElement>('[data-act="lightbox"]')!.click();
    expect(document.querySelector(".media-lightbox")).not.toBeNull();
    expect(document.querySelector<HTMLImageElement>(".media-lightbox .lb-img")!.src).toBe("https://example.com/photo.png");
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    expect(document.querySelector(".media-lightbox")).toBeNull();

    // Clicking PDF expand toggles .is-expanded
    const pdfBtn = document.querySelector<HTMLButtonElement>('[data-act="pdf-expand"]')!;
    pdfBtn.click();
    expect(document.querySelector(".media-pdf")?.classList.contains("is-expanded")).toBe(true);
    expect(pdfBtn.getAttribute("aria-expanded")).toBe("true");

    // Clicking 在網站開啟 calls api.openLink with the validated http(s) URL
    document.querySelector<HTMLButtonElement>('[data-act="open-url"]')!.click();
    expect(api.openLink).toHaveBeenCalledWith("https://example.com/photo.png");
  });
});
