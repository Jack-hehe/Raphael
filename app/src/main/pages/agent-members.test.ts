// @vitest-environment happy-dom
// The 拉斐爾 page with members (pages/agent.ts and members-shell.ts): picking a member loads its own
// thread, asks it and searches it alone; its answers are plain, its composer says "Message <name>"
// and has no voice button; the gold card shows in the thread that asked; Raphael's thread is as it
// was; a turn of another thread does not touch the open one; the slot of the background questions
// sits above the composer; the details panel closes and comes back from the pill.
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import type { AgentTurn, AppContext, MainEvent, Member, MembersList } from "../types";

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
  membersList: vi.fn(),
  membersPresets: vi.fn(),
  membersRead: vi.fn(),
  membersViewing: vi.fn(),
  membersToolChoices: vi.fn(),
  membersUpdate: vi.fn(),
  membersCreate: vi.fn(),
  membersSetAvatar: vi.fn(),
  routinesCreate: vi.fn(),
  toolsetsList: vi.fn(),
  routinesList: vi.fn(),
  modelsCatalog: vi.fn(),
  core: vi.fn(),
}));
vi.mock("../api", () => api);

import { Cleared } from "../cleared";
import { createAgent } from "./agent";

const member = (id: number, more: Partial<Member> = {}): Member => ({
  id, name: `成員${id}`, role: "", instructions: "", avatar: "", group_id: null, model: "", tools: null, position: id, created: 0, unread: false, waiting: false, preview: "", last_at: null, ...more,
});
const LIST: MembersList = {
  members: [
    member(0, { name: "拉斐爾" }),
    member(3, { name: "日文老師", role: "N2 嚴格版", preview: "今天的單字" }),
    member(4, { name: "行程管家", unread: true, preview: "明天 10:00 有會議" }),
  ],
  groups: [],
};
const turn = (id: number, more: Partial<AgentTurn> = {}): AgentTurn => ({
  id, created_at_ms: Date.parse("2026-10-20T10:00:00+08:00") + id * 1000, question: `問題 ${id}`, route: "chat",
  prefix: "答", text: `回答 ${id}`, detail: null, status: "done", source: "app", ...more,
});
const THREADS: Record<number, AgentTurn[]> = {
  0: [turn(2, { source: "voice" }), turn(1, { source: "voice" })],
  3: [turn(31, { member_id: 3, text: "今天的單字" }), turn(30, { member_id: 3, source: "routine", route: "routine", prefix: "", question: "每天的單字", text: "10 題已送出" })],
  4: [turn(41, { member_id: 4, text: "明天 10:00 有會議" })],
};

let send: (event: MainEvent) => void = () => {};
const flush = async (): Promise<void> => {
  for (let i = 0; i < 20; i++) await Promise.resolve();
};

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
const $ = <T extends HTMLElement>(q: string) => document.querySelector<T>(q)!;
const pick = async (id: number) => {
  $(`.mb-col [data-member="${id}"]`).click();
  await flush();
};

beforeEach(() => {
  document.body.innerHTML = "";
  for (const fn of Object.values(api)) fn.mockReset();
  api.agentQuestionOpen.mockResolvedValue(null);
  api.membersList.mockResolvedValue(structuredClone(LIST));
  api.membersPresets.mockResolvedValue({ presets: [] });
  api.membersRead.mockResolvedValue({});
  api.membersViewing.mockResolvedValue({});
  api.membersToolChoices.mockResolvedValue({ tools: ["web_search", "weather"], connections: [] });
  api.toolsetsList.mockResolvedValue({ groups: [] });
  api.routinesList.mockResolvedValue({ routines: [] });
  api.modelsCatalog.mockResolvedValue(null);
  api.agentAskText.mockResolvedValue(undefined);
  api.agentHistoryList.mockImplementation(async (q: { member_id?: number; limit?: number }) => ({ turns: THREADS[q.member_id ?? 0].slice(0, q.limit) }));
});
afterEach(() => vi.useRealTimers());

describe("Raphael's thread", () => {
  test("is as it was: his turns in 《》, Ask Raphael, his voice button, asked with no member", async () => {
    mount();
    await flush();
    expect(api.agentHistoryList).toHaveBeenCalledWith({ limit: 50 });
    expect($(".thread").textContent).toContain("《答。回答 2》");
    expect($<HTMLTextAreaElement>(".cmp-input").placeholder).toBe("Ask Raphael");
    expect($(".cmp-voice").hidden).toBe(false);
    expect($(".mb-pill-t").textContent).toBe("拉斐爾");
    $<HTMLTextAreaElement>(".cmp-input").value = "你好";
    $<HTMLFormElement>(".composer").requestSubmit();
    await flush();
    expect(api.agentAskText).toHaveBeenCalledWith("你好", []);
  });

  test("the member column lists the members, each with the last line of its thread from the list", async () => {
    mount();
    await flush();
    expect([...document.querySelectorAll(".mb-col .mb-row-t b")].map((b) => b.textContent)).toEqual(["日文老師", "行程管家"]);
    expect($('.mb-col [data-member="3"] .mb-row-p').textContent).toBe("今天的單字");
    // no thread but the open one is read
    expect(api.agentHistoryList.mock.calls.every(([q]) => q.member_id === undefined)).toBe(true);
  });
});

describe("a member's thread", () => {
  test("loads that member's turns alone, plain, with the routine's line", async () => {
    mount();
    await flush();
    await pick(3);
    expect(api.agentHistoryList).toHaveBeenLastCalledWith({ limit: 50, member_id: 3 });
    const thread = $(".thread");
    expect(thread.textContent).not.toContain("回答 2");
    expect(thread.textContent).toContain("今天的單字");
    expect(thread.textContent).not.toContain("《");
    expect(thread.querySelector(".tn-plain .ans")!.textContent).toBe("10 題已送出");
    expect(thread.querySelector(".sys")!.textContent).toBe("例行任務每天的單字");
    // a routine's prompt is no bubble of the owner's
    expect(thread.querySelector('[data-id="30"] .bubble')).toBeNull();
    expect($('.mb-col [data-member="3"]').getAttribute("aria-current")).toBe("true");
  });

  test("its composer says Message and the name, has no voice button, and asks that member", async () => {
    mount();
    await flush();
    await pick(3);
    expect($<HTMLTextAreaElement>(".cmp-input").placeholder).toBe("Message 日文老師");
    expect($(".cmp-voice").hidden).toBe(true);
    expect($(".mb-pill-t").textContent).toBe("日文老師");
    // 清除全部 and Raphael's settings are his thread's
    expect($('[data-act="clear"]').hidden).toBe(true);
    expect($('[data-act="settings"]').hidden).toBe(true);
    $<HTMLTextAreaElement>(".cmp-input").value = "考我";
    $<HTMLFormElement>(".composer").requestSubmit();
    await flush();
    expect(api.agentAskText).toHaveBeenCalledWith("考我", [], 3);
  });

  test("the search looks through the open thread alone", async () => {
    vi.useFakeTimers();
    api.agentHistorySearch.mockResolvedValue({ turns: [] });
    mount();
    await vi.runOnlyPendingTimersAsync();
    $(".mb-col [data-member=\"3\"]").click();
    await vi.runOnlyPendingTimersAsync();
    $('[data-act="search"]').click();
    const input = $<HTMLInputElement>(".ra-find-input");
    input.value = "單字";
    input.dispatchEvent(new Event("input"));
    await vi.advanceTimersByTimeAsync(250);
    expect(api.agentHistorySearch).toHaveBeenCalledWith("單字", 3);
  });

  test("back to Raphael: his own thread again, and his composer", async () => {
    mount();
    await flush();
    await pick(3);
    await pick(0);
    expect(api.agentHistoryList).toHaveBeenLastCalledWith({ limit: 50 });
    expect($(".thread").textContent).toContain("《答。回答 2》");
    expect($(".thread").textContent).not.toContain("今天的單字");
    expect($<HTMLTextAreaElement>(".cmp-input").placeholder).toBe("Ask Raphael");
  });

  test("an unread thread opened is told to the core, and its dot goes", async () => {
    mount();
    await flush();
    expect($('.mb-col [data-member="4"] .mb-dot.is-unread')).not.toBeNull();
    await pick(4);
    expect(api.membersRead).toHaveBeenCalledWith(4);
    expect(document.querySelector('.mb-col [data-member="4"] .mb-dot')).toBeNull();
  });

  test("the gold card shows in the thread whose member asked, and only there", async () => {
    let answered: () => void = () => {};
    api.agentAskText.mockImplementation(() => new Promise<void>((resolve) => (answered = resolve)));
    mount();
    await flush();
    await pick(3);
    $<HTMLTextAreaElement>(".cmp-input").value = "幫我寄信";
    $<HTMLFormElement>(".composer").requestSubmit();
    await flush();
    send({ type: "AgentQuestion", id: "c-1", prefix: "問", text: "寄出這封信？", tier: "T2", timeout_ms: 60_000 });
    expect($(".ask-card").hidden).toBe(false);
    // a member asks in plain words
    expect($(".ask-q").textContent).toBe("寄出這封信？");
    await pick(4);
    expect($(".ask-card").hidden).toBe(true);
    await pick(0);
    expect($(".ask-card").hidden).toBe(true);
    await pick(3);
    expect($(".ask-card").hidden).toBe(false);
    answered();
    await flush();
  });

  test("a question with no typed one on its way is Raphael's (the voice and the overlay are his)", async () => {
    mount();
    await flush();
    await pick(3);
    send({ type: "AgentQuestion", id: "c-2", prefix: "問", text: "要關燈嗎", tier: "T2", timeout_ms: 60_000 });
    expect($(".ask-card").hidden).toBe(true);
    await pick(0);
    expect($(".ask-card").hidden).toBe(false);
    expect($(".ask-q").textContent).toBe("《問。要關燈嗎》");
    // what is typed in a member's thread later does not take Raphael's question along
    api.agentAskText.mockImplementation(() => new Promise<void>(() => {}));
    await pick(3);
    $<HTMLTextAreaElement>(".cmp-input").value = "考我";
    $<HTMLFormElement>(".composer").requestSubmit();
    await flush();
    expect($(".ask-card").hidden).toBe(true);
    await pick(0);
    expect($(".ask-card").hidden).toBe(false);
  });

  test("a turn of another thread reads the list again, not the open thread", async () => {
    vi.useFakeTimers();
    mount();
    await vi.runOnlyPendingTimersAsync();
    $('.mb-col [data-member="3"]').click();
    await vi.runOnlyPendingTimersAsync();
    api.agentHistoryList.mockClear();
    api.membersList.mockClear();
    send({ type: "AgentTurn", turn_id: 50, member_id: 4 });
    await vi.advanceTimersByTimeAsync(1000);
    expect(api.agentHistoryList).not.toHaveBeenCalled();
    expect(api.membersList).toHaveBeenCalled();
    send({ type: "AgentTurn", turn_id: 51, member_id: 3 });
    await vi.advanceTimersByTimeAsync(1000);
    expect(api.agentHistoryList).toHaveBeenCalledWith(expect.objectContaining({ member_id: 3 }));
  });

  test("a routine's run that has not reported yet shows the dots under its line", async () => {
    THREADS[3] = [turn(32, { member_id: 3, source: "routine", route: "routine", prefix: "", question: "每天的單字", text: "", status: "running" })];
    mount();
    await flush();
    await pick(3);
    const run = $('.tn[data-id="32"]');
    expect(run.querySelector(".sys")!.textContent).toBe("例行任務每天的單字");
    expect(run.querySelector(".ans.thinking")).not.toBeNull();
    expect(run.querySelector(".me")).toBeNull();
    THREADS[3] = [turn(31, { member_id: 3, text: "今天的單字" })];
  });

  test("the slot of the questions from the background sits above the composer, in every thread", async () => {
    mount();
    await flush();
    const slot = $('[data-mount="background-questions"]');
    expect(slot.nextElementSibling).toBe($(".composer"));
    await pick(3);
    expect($('[data-mount="background-questions"]').nextElementSibling).toBe($(".composer"));
    expect(document.querySelectorAll('[data-mount="background-questions"]')).toHaveLength(1);
  });

  test("the gold card shows in the thread of its member only: their name and plain words there, Raphael's 《問。》 in his", async () => {
    const asked = (member_id: number, text: string) => ({ id: `q-${member_id}`, task_id: 10 + member_id, member_id, prefix: "問", text, tier: null, created_at_ms: 1, expires_at_ms: 2 });
    const waiting: Record<number, ReturnType<typeof asked>[]> = { 0: [asked(0, "要付款嗎？")], 3: [asked(3, "要寄出信件嗎？")] };
    api.core.mockImplementation(async (method: string, params?: { member_id?: number }) =>
      method === "background.questions.list" ? { questions: waiting[params?.member_id ?? 0] ?? [] } : {},
    );
    mount();
    await flush();
    const slot = (): HTMLElement => $('[data-mount="background-questions"]');
    expect(slot().querySelector(".bgq-card")!.textContent).toContain("《問。要付款嗎？》");
    expect(slot().querySelector(".bgq-name")).toBeNull();
    expect(slot().querySelector(".bgq-card img")!.getAttribute("src")).toContain("/brand/");
    await pick(3);
    await flush();
    expect(api.core).toHaveBeenCalledWith("background.questions.list", { member_id: 3 });
    const cards = slot().querySelectorAll(".bgq-card");
    expect(cards).toHaveLength(1);
    expect(cards[0].querySelector(".bgq-name")!.textContent).toBe("日文老師");
    expect(cards[0].textContent).toContain("要寄出信件嗎？");
    expect(cards[0].textContent).not.toContain("《");
    expect(cards[0].querySelector("img")).toBeNull();
    await pick(0);
    await flush();
    expect(slot().querySelector(".bgq-card")!.textContent).toContain("《問。要付款嗎？》");
    api.core.mockReset();
  });

  test("an exchange between members is one line in the thread, opened on what was asked and answered", async () => {
    THREADS[4] = [turn(42, { member_id: 4, peer_id: 0, route: "exchange", source: "member", question: "主人明天幾點有空？", text: "下午兩點以後" })];
    mount();
    await flush();
    await pick(4);
    const line = $('[data-act="exchange"]');
    expect(line.textContent).toBe("2 則訊息往來：拉斐爾");
    expect(document.querySelector(".x-list")).toBeNull();
    expect(document.querySelector('.tn[data-id="42"] .bubble')).toBeNull();
    line.click();
    expect([...document.querySelectorAll(".x-list li")].map((li) => li.textContent)).toEqual(["「主人明天幾點有空？」", "下午兩點以後"]);
    expect($('[data-act="exchange"]').getAttribute("aria-expanded")).toBe("true");
  });

  test("the core is told which thread is on screen, and none once the page goes", async () => {
    const agent = mount();
    await flush();
    expect(api.membersViewing).toHaveBeenLastCalledWith(0);
    await pick(3);
    expect(api.membersViewing).toHaveBeenLastCalledWith(3);
    agent.hide();
    await flush();
    expect(api.membersViewing).toHaveBeenLastCalledWith(null);
  });

  test("switching threads clears unsent composer text, and an in-flight ask that fails on the previous thread does not overwrite the new thread's composer", async () => {
    let failAsk!: (err: Error) => void;
    api.agentAskText.mockImplementationOnce(() => new Promise<void>((_, rej) => (failAsk = rej)));
    mount();
    await flush();
    await pick(3);
    const input = $<HTMLTextAreaElement>(".cmp-input");
    input.value = "給日文老師的草稿";
    await pick(4);
    expect(input.value).toBe("");

    await pick(3);
    input.value = "考我單字";
    $<HTMLFormElement>(".composer").requestSubmit();
    await pick(4);
    input.value = "給管家的話";
    failAsk(new Error("network down"));
    await flush();
    expect(input.value).toBe("給管家的話");
  });

  test("refocusing the window while scrolled up in a thread does not jump scroll to the bottom", async () => {
    const agent = mount();
    await flush();
    const scroller = $<HTMLElement>(".ra-scroll");
    Object.defineProperty(scroller, "scrollHeight", { value: 1000, configurable: true });
    Object.defineProperty(scroller, "clientHeight", { value: 200, configurable: true });
    scroller.scrollTop = 100;
    agent.show();
    await flush();
    expect(scroller.scrollTop).toBe(100);
  });
});

describe("a new member", () => {
  const OFFER = { title: "每天早上的進度", prompt: "看進度", schedule: { kind: "daily" as const, time: "09:00" } };

  async function create(offer: typeof OFFER | null): Promise<void> {
    const made = member(7, { name: "進度官", group_id: null });
    api.membersCreate.mockResolvedValue({ member: made, routine_offer: offer });
    api.routinesCreate.mockResolvedValue({ routine: {} });
    api.membersList.mockResolvedValue(structuredClone(LIST));
    mount();
    await flush();
    api.membersList.mockResolvedValue({ ...structuredClone(LIST), members: [...structuredClone(LIST).members, made] });
    $('[data-col="add"]').click();
    $('#app-popover [data-pick="member"]').click();
    $<HTMLInputElement>("#mb-new-name").value = "進度官";
    $('[data-dlg="create"]').click();
    await flush();
  }

  test("is made in the language of the window and opened; the routine the core offers is asked, 加入 makes it", async () => {
    vi.useFakeTimers();
    await create(OFFER);
    expect(api.membersCreate).toHaveBeenCalledWith({ name: "進度官", role: "", instructions: "", group_id: null, ui_language: "zh-TW" });
    expect($(".mb-pill-t").textContent).toBe("進度官");
    expect(api.routinesCreate).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(10);
    expect($(".mb-dialog h2").textContent).toBe("加入例行任務？");
    $('[data-dlg="yes"]').click();
    await vi.advanceTimersByTimeAsync(10);
    expect(api.routinesCreate).toHaveBeenCalledWith({ member_id: 7, ...OFFER });
  });

  test("不用 leaves the routine out, and no offer asks nothing", async () => {
    vi.useFakeTimers();
    await create(OFFER);
    await vi.advanceTimersByTimeAsync(10);
    $('[data-dlg="no"]').click();
    await vi.advanceTimersByTimeAsync(10);
    expect(api.routinesCreate).not.toHaveBeenCalled();
    document.body.innerHTML = "";
    await create(null);
    await vi.advanceTimersByTimeAsync(10);
    expect(document.querySelector(".mb-dialog")).toBeNull();
  });

  test("opened from the narrow-window picker closes the menu first and returns focus to the picker on Escape", async () => {
    mount();
    await flush();
    const picker = $<HTMLButtonElement>(".mb-pick");
    picker.click();
    expect(document.getElementById("app-popover")).not.toBeNull();
    $<HTMLElement>('#app-popover [data-action="0"]').click();
    expect(document.getElementById("app-popover")).toBeNull();
    expect(document.querySelector(".mb-dialog")).not.toBeNull();
    expect(document.activeElement?.id).toBe("mb-new-name");
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    expect(document.querySelector(".mb-scrim")).toBeNull();
    expect(document.activeElement).toBe(picker);
  });

  test("retries loading presets after a transient failure on startup", async () => {
    const url = "data:image/png;base64,iVBORw0KGgo=";
    api.membersPresets.mockRejectedValueOnce(new Error("offline")).mockResolvedValue({
      presets: [{ avatar: "preset:a", name: "a", url }],
    });
    mount();
    await flush();
    send({ type: "AgentTurn", turn_id: 99, member_id: 4 });
    await new Promise((r) => setTimeout(r, 350));
    await flush();
    $('[data-col="add"]').click();
    $('#app-popover [data-pick="member"]').click();
    $('[data-dlg="avatar"]').click();
    expect(document.querySelector('#app-popover [data-preset="preset:a"]')).not.toBeNull();
  });
});

describe("the details panel", () => {
  test("its × closes it, and the pill brings it back", async () => {
    vi.useFakeTimers();
    mount();
    await vi.runOnlyPendingTimersAsync();
    const panel = $(".mb-det");
    const open = panel.hidden === false;
    if (!open) $(".mb-pill").click();
    expect($(".mb-det").hidden).toBe(false);
    expect($(".page-agent").dataset.det).toBe("open");
    $('[data-det="close"]').click();
    await vi.advanceTimersByTimeAsync(200);
    expect($(".mb-det").hidden).toBe(true);
    expect($(".page-agent").dataset.det).toBeUndefined();
    $(".mb-pill").click();
    expect($(".mb-det").hidden).toBe(false);
  });

  test("renaming the open member in the details panel updates the composer placeholder and thread faces", async () => {
    mount();
    await flush();
    await pick(3);
    if ($(".mb-det").hidden) $(".mb-pill").click();
    api.membersUpdate.mockResolvedValue({ member: { ...LIST.members[1], name: "嚴格老師" } });
    api.membersList.mockResolvedValue({
      ...structuredClone(LIST),
      members: LIST.members.map((m) => (m.id === 3 ? { ...m, name: "嚴格老師" } : m)),
    });
    const name = $<HTMLInputElement>("#mb-det-name");
    name.focus();
    name.value = "嚴格老師";
    name.dispatchEvent(new FocusEvent("focusout", { bubbles: true }));
    await flush();
    expect($<HTMLTextAreaElement>(".cmp-input").placeholder).toBe("Message 嚴格老師");
    expect($(".thread .mb-av-letter").textContent).toBe("嚴");
  });
});

test("a core without the members: Raphael alone, and the page works as before", async () => {
  api.membersList.mockRejectedValue("mock: members.list");
  mount();
  await flush();
  expect(document.querySelectorAll(".mb-col .mb-row")).toHaveLength(0);
  expect($(".mb-col .mb-chief").textContent).toBe("拉斐爾");
  expect($(".thread").textContent).toContain("《答。回答 2》");
});
