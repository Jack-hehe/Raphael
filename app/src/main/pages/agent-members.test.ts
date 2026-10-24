// @vitest-environment happy-dom
// The 拉斐爾 page with members (pages/agent.ts and members-shell.ts): picking a member loads its own
// thread, asks it and searches it alone; its answers are plain, its composer says "Message <name>"
// and has the emblem voice button; the gold card shows in the thread that asked; Raphael's thread is as it
// was; a turn of another thread does not touch the open one; the slot of the background questions
// sits above the composer; the details panel closes and comes back from the pill.
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import type { AgentTurn, AppContext, MainEvent, Member, MembersList, Routine } from "../types";

const api = vi.hoisted(() => ({
  agentHistoryList: vi.fn(),
  agentHistoryClear: vi.fn(),
  agentHistorySearch: vi.fn(),
  agentAskText: vi.fn(),
  agentRetract: vi.fn(),
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
  providersList: vi.fn(),
  brainsCli: vi.fn(),
  core: vi.fn(),
  roomsList: vi.fn(),
  roomMessages: vi.fn(),
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

function mount(mode: "hold" | "toggle" = "hold") {
  const app = {
    go: vi.fn(),
    device: () => ({ dictation_mode: mode }),
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

test("files dropped into a member's thread are sent only to that member", async () => {
  const agent = mount(); await flush(); await pick(3);
  const event = new Event("drop", { bubbles: true, cancelable: true });
  Object.defineProperty(event, "dataTransfer", { value: { types: ["Files"], files: [new File(["report"], "report.csv", { type: "text/csv" })] } });
  agent.el.querySelector(".ra-stage")!.dispatchEvent(event);
  await flush(); await new Promise(resolve => setTimeout(resolve, 0)); await flush();
  expect(agent.el.querySelector(".cmp-chip-t")?.textContent).toBe("report.csv");
  agent.el.querySelector<HTMLFormElement>(".composer")!.requestSubmit(); await flush();
  expect(api.agentAskText).toHaveBeenCalledWith("", [{ name: "report.csv", mime: "text/csv", data: btoa("report") }], 3, expect.objectContaining({ requestId: expect.any(String) }));
  agent.hide();
});

test("a routine run link selects its member and loads the linked older turn", async () => {
  const agent = mount(); await flush();
  api.agentHistoryList.mockImplementation(async (q: { member_id?: number; before_id?: number }) => ({ turns: q.before_id === 11 ? [turn(10, { member_id: 3, text: "Linked routine result" })] : THREADS[q.member_id ?? 0] }));
  window.dispatchEvent(new CustomEvent("raphael:open-member", { detail: { memberId: 3, turnId: 10 } }));
  await flush(); await flush();
  expect(api.agentHistoryList).toHaveBeenCalledWith(expect.objectContaining({ member_id: 3, before_id: 11 }));
  expect(agent.el.querySelector('.tn[data-id="10"]')?.textContent).toContain("Linked routine result");
  expect(agent.el.querySelector('.mb-col [data-member="3"]')?.getAttribute("aria-current")).toBe("true");
  agent.hide();
});

beforeEach(() => {
  document.body.innerHTML = "";
  for (const fn of Object.values(api)) fn.mockReset();
  api.roomsList.mockResolvedValue({rooms:[{id:1,name:"Launch",member_ids:[0,3],is_team:0}]});
  api.roomMessages.mockResolvedValue({messages:[{id:1,member_id:3,text:"Ready",created:1,status:"done",turn_id:31}]});
  api.brainsCli.mockResolvedValue({ clis: [] });
  api.providersList.mockResolvedValue({ providers: [] });
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
  api.agentRetract.mockResolvedValue(undefined);
  api.agentVoice.mockResolvedValue(undefined);
  api.agentHistoryList.mockImplementation(async (q: { member_id?: number; limit?: number }) => ({ turns: THREADS[q.member_id ?? 0].slice(0, q.limit) }));
});
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

test("pending owner questions can be cancelled into the same member's composer, without late delivery restoring them", async () => {
  let finish!: () => void;
  api.agentAskText.mockImplementation(() => new Promise<void>(resolve => { finish = resolve; }));
  const agent = mount(); await flush(); await pick(3);
  const input = $<HTMLTextAreaElement>(".cmp-input");
  input.value = "fix this typo"; input.dispatchEvent(new Event("input"));
  $(".cmp-send").click(); await flush();
  const options = api.agentAskText.mock.calls[0][3];
  expect(options.requestId).toEqual(expect.any(String));
  $('.is-pending [data-act="cancel-message"]').click(); await flush();
  expect(api.agentRetract).toHaveBeenCalledWith(options.requestId, 3, undefined);
  expect(input.value).toBe("fix this typo");
  expect(document.activeElement).toBe(input);
  expect(document.querySelector(".is-pending")).toBeNull();
  finish(); await flush();
  expect(input.value).toBe("fix this typo");
  agent.hide();
});

test("editing preserves the old turn until resending the replacement in its own thread", async () => {
  const agent = mount(); await flush(); await pick(3);
  $('[data-id="31"] [data-act="edit-message"]').click(); await flush();
  const input = $<HTMLTextAreaElement>(".cmp-input");
  expect(input.value).toBe("問題 31");
  expect(document.querySelector('[data-id="31"]')).not.toBeNull();
  expect(api.agentRetract).not.toHaveBeenCalled();
  input.value = "corrected"; $(".composer").dispatchEvent(new Event("submit", { cancelable: true })); await flush();
  expect(api.agentAskText).toHaveBeenCalledWith("corrected", [], 3, expect.objectContaining({ replaceTurnId: 31 }));
  agent.hide();
});

test("message actions show an app tooltip on keyboard focus, with no native title", async () => {
  const agent = mount(); await flush();
  const edit = $('[data-id="2"] [data-act="edit-message"]');
  expect(edit.hasAttribute("title")).toBe(false);
  edit.focus();
  expect($('[role="tooltip"]').textContent).toBe("編輯");
  expect($('[role="tooltip"]').hidden).toBe(false);
  $<HTMLTextAreaElement>(".cmp-input").focus();
  expect($('[role="tooltip"]').hidden).toBe(true);
  agent.hide();
});

test("Escape cancels only from an empty composer and restores a running turn", async () => {
  api.agentHistoryList.mockResolvedValue({ turns: [turn(8, { session_id: "running", status: "running", text: "Partial" })] });
  const agent = mount(); await flush();
  const input = $<HTMLTextAreaElement>(".cmp-input");
  input.value = "new draft";
  input.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })); await flush();
  expect(api.agentRetract).not.toHaveBeenCalled();
  input.value = "";
  input.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })); await flush();
  expect(api.agentRetract).toHaveBeenCalledWith("running", 0, 8);
  expect(input.value).toBe("問題 8");
  expect(document.querySelector('[data-id="8"]')).toBeNull();
  agent.hide();
});

test("editing a legacy attachment without a stored copy requires reattachment, without mutating history", async () => {
  const original = turn(8, { session_id: "saved", attachments: [{ name: "original.pdf", mime: "application/pdf" }], quote: { text: "Saved passage", author: "You" } });
  api.agentHistoryList.mockResolvedValue({ turns: [original] });
  const agent = mount(); await flush();
  $('[data-id="8"] [data-act="edit-message"]').click(); await flush();
  expect($(".cmp-quote .quote-text").textContent).toBe("Saved passage");
  expect($<HTMLButtonElement>(".cmp-send").disabled).toBe(true);
  $('[data-act="drop-missing"]').click();
  expect($<HTMLButtonElement>(".cmp-send").disabled).toBe(false);
  expect(original.attachments).toEqual([{ name: "original.pdf", mime: "application/pdf" }]);
  expect(api.agentRetract).not.toHaveBeenCalled();
  agent.hide();
});

test.each(["edit", "cancel", "regenerate"])("%s after reload retrieves stored files and preserves the quote in its member thread", async action => {
  const id = `${"a".repeat(32)}.csv`;
  const quote = { text: "Saved passage", author: "You" };
  const original = turn(8, { member_id: 3, session_id: "saved", status: action === "cancel" ? "running" : "done",
    attachments: [{ id, name: "report.csv", mime: "text/csv", size: 6 }], quote });
  api.agentHistoryList.mockImplementation(async ({ member_id }: { member_id?: number }) => ({ turns: member_id === 3 ? [original] : [] }));
  api.core.mockImplementation(async (method: string) => method === "attachments.get" ? { mime: "text/csv", data: btoa("report") } : { questions: [] });
  const agent = mount(); await flush(); await pick(3);
  $(`[data-id="8"] [data-act="${action === "regenerate" ? action : `${action}-message`}"]`).click(); await flush();
  expect(api.core).toHaveBeenCalledWith("attachments.get", { id });
  if (action !== "regenerate") {
    expect($<HTMLTextAreaElement>(".cmp-input").value).toBe("問題 8");
    expect($(".cmp-chip-t").textContent).toBe("report.csv");
    expect(document.querySelector("[data-missing]")).toBeNull();
    expect($(".cmp-quote .quote-text").textContent).toBe("Saved passage");
    expect($<HTMLButtonElement>(".cmp-send").disabled).toBe(false);
    $(".composer").dispatchEvent(new Event("submit", { cancelable: true })); await flush();
  }
  expect(api.agentAskText).toHaveBeenCalledWith("問題 8", [{ name: "report.csv", mime: "text/csv", data: btoa("report") }], 3,
    expect.objectContaining({ quote, ...(action === "edit" ? { replaceTurnId: 8 } : {}) }));
  expect(original.attachments).toEqual([{ id, name: "report.csv", mime: "text/csv", size: 6 }]);
  agent.hide();
});

test("an unavailable stored copy falls back to reattachment", async () => {
  const id = `${"b".repeat(32)}.pdf`;
  api.agentHistoryList.mockResolvedValue({ turns: [turn(8, { attachments: [{ id, name: "lost.pdf", mime: "application/pdf" }] })] });
  api.core.mockImplementation(async (method: string) => { if (method === "attachments.get") throw new Error("unavailable"); return { questions: [] }; });
  const agent = mount(); await flush();
  $('[data-id="8"] [data-act="edit-message"]').click(); await flush();
  expect(api.core).toHaveBeenCalledWith("attachments.get", { id });
  expect($("[data-missing]").textContent).toContain("lost.pdf");
  expect($<HTMLButtonElement>(".cmp-send").disabled).toBe(true);
  agent.hide();
});

test("a stored-file read finishing after a thread switch cannot fill the new composer", async () => {
  let finish!: (file: { mime: string; data: string }) => void;
  api.agentHistoryList.mockResolvedValue({ turns: [turn(8, { attachments: [{ id: `${"c".repeat(32)}.csv`, name: "old.csv", mime: "text/csv" }] })] });
  api.core.mockImplementation((method: string) => method === "attachments.get" ? new Promise(resolve => { finish = resolve; }) : Promise.resolve({ questions: [] }));
  const agent = mount(); await flush();
  $('[data-id="8"] [data-act="edit-message"]').click(); await flush();
  await pick(3);
  finish({ mime: "text/csv", data: btoa("old") }); await flush();
  expect($<HTMLTextAreaElement>(".cmp-input").value).toBe("");
  expect(document.querySelector(".cmp-chip")).toBeNull();
  agent.hide();
});

test("a selected member reply travels separately, renders on the pending bubble, and returns on cancel", async () => {
  api.agentAskText.mockImplementation(() => new Promise<void>(() => {}));
  const agent = mount(); await flush(); await pick(3);
  const message = $('[data-id="31"] .message-text');
  const range = document.createRange(); range.selectNodeContents(message);
  range.getClientRects = () => [{ left: 100, right: 200, top: 100, bottom: 120, width: 100, height: 20 }] as unknown as DOMRectList;
  const selection = document.getSelection()!; selection.removeAllRanges(); selection.addRange(range);
  document.dispatchEvent(new Event("selectionchange"));
  $(".quote-pill").click();
  expect($(".cmp-quote .quote-author").textContent).toBe("日文老師");
  const input = $<HTMLTextAreaElement>(".cmp-input");
  expect(document.activeElement).toBe(input);
  input.value = "Explain"; $(".composer").dispatchEvent(new Event("submit", { cancelable: true })); await flush();
  expect(api.agentAskText).toHaveBeenCalledWith("Explain", [], 3, expect.objectContaining({ quote: { text: "今天的單字", author: "日文老師" } }));
  expect($(".is-pending .quote-text").textContent).toBe("今天的單字");
  expect($(".cmp-quote").hidden).toBe(true);
  $('.is-pending [data-act="cancel-message"]').click(); await flush();
  expect($(".cmp-quote .quote-text").textContent).toBe("今天的單字");
  $('[data-act="remove-quote"]').click();
  expect($(".cmp-quote").hidden).toBe(true);
  agent.hide();
});

test.each(["finish", "switch", "search", "reduced"])("a live member burst reveals with typing dots; %s completes or flushes it", async mode => {
  vi.useFakeTimers();
  let reduce = false;
  let preferenceChanged: () => void = () => {};
  vi.spyOn(window, "matchMedia").mockImplementation(() => ({ get matches() { return reduce; }, addEventListener: (_: string, listener: () => void) => { preferenceChanged = listener; } }) as unknown as MediaQueryList);
  let running = true;
  api.agentHistoryList.mockImplementation(async (query: { member_id?: number }) => ({ turns: query.member_id === 3 ? [turn(50, {
    member_id: 3, session_id: "burst", status: running ? "running" : "done", text: running ? "" : "All replies",
    messages: running ? [] : ["First", "Second", "Third"].map((text, index) => ({ seq: index + 1, kind: "reply", text, at_ms: Date.now() })),
  })] : [] }));
  const agent = mount(); await flush(); await pick(3);
  running = false; send({ type: "AgentTurn", turn_id: 50, member_id: 3 });
  await vi.advanceTimersByTimeAsync(150); await flush();
  const replies = () => [...document.querySelectorAll('[data-id="50"] .message-text')].map(element => element.textContent);
  expect(replies()).toEqual(["First"]);
  expect(document.querySelector('[data-id="50"] .thinking')).not.toBeNull();
  if (mode === "finish") await vi.advanceTimersByTimeAsync(2000);
  if (mode === "switch") { await pick(4); await pick(3); }
  if (mode === "search") $('[data-act="search"]').click();
  if (mode === "reduced") {
    reduce = true;
    preferenceChanged();
  }
  expect(replies()).toEqual(["First", "Second", "Third"]);
  expect(document.querySelector('[data-id="50"] .thinking')).toBeNull();
  agent.hide();
});

test("regenerating the latest member reply asks the same question in that member's thread", async () => {
  const agent = mount(); await flush(); await pick(3);
  expect(document.querySelectorAll('[data-act="regenerate"]')).toHaveLength(1);
  document.querySelector<HTMLButtonElement>('[data-act="regenerate"]')!.click();
  await flush();
  expect(api.agentAskText).toHaveBeenCalledExactlyOnceWith("問題 31", [], 3, expect.objectContaining({ requestId: expect.any(String) }));
  agent.hide();
});

test("routine notifications refresh the open panel immediately and keep the routine being edited", async () => {
  mount();
  await flush();
  await pick(3);
  $(".mb-pill").click();
  const routine: Routine = { id: 9, member_id: 3, title: "Daily review", prompt: "Review", schedule: { kind: "daily", time: "09:00" }, enabled: true, next_run: null, last_run: null };
  api.routinesList.mockResolvedValue({ routines: [routine] });
  send({ type: "RoutinesChanged", member_id: 3 });
  await flush();
  expect($(".mb-rts").textContent).toContain("Daily review");
  $('[data-det="routine-edit"]').click();
  const title = document.querySelector<HTMLInputElement>("#au-title")!;
  title.focus();
  title.value = "My unsaved title";
  title.setSelectionRange(3, 5);
  const scroll = $(".mb-det-scroll");
  scroll.scrollTop = 84;
  api.routinesList.mockResolvedValue({ routines: [routine, { ...routine, id: 10, title: "New routine" }] });
  send({ type: "RoutinesChanged", member_id: 3 });
  await flush();
  expect($(".mb-rts").textContent).toContain("New routine");
  expect(document.querySelector<HTMLInputElement>("#au-title")!.value).toBe("My unsaved title");
  expect(document.activeElement).toBe(document.querySelector("#au-title"));
  expect(document.querySelector<HTMLInputElement>("#au-title")!.selectionStart).toBe(3);
  expect(scroll.scrollTop).toBe(84);
});

test("a chat event refreshes routines even when its explicit change notification was missed", async () => {
  vi.useFakeTimers();
  mount();
  await flush();
  await pick(3);
  $(".mb-pill").click();
  const routine = { id: 9, member_id: 3, title: "Created from chat", prompt: "Review", schedule: { kind: "daily", time: "09:00" }, enabled: true, next_run: null, last_run: null };
  api.routinesList.mockResolvedValue({ routines: [routine] });
  api.agentHistoryList.mockResolvedValue({ turns: [turn(99, { member_id: 3, messages: [{ seq: 1, kind: "event", text: "Routine created", at_ms: 1 }] })] });
  send({ type: "AgentTurn", turn_id: 99, member_id: 3 });
  await vi.advanceTimersByTimeAsync(350);
  await flush();
  expect($(".mb-rts").textContent).toContain("Created from chat");
});

test("member notifications refresh names, groups, unread dots and the open header", async () => {
  mount();
  await flush();
  await pick(3);
  const next = structuredClone(LIST);
  next.members[1] = { ...next.members[1], name: "Updated name", role: "Updated role", group_id: 5 };
  next.members[2] = { ...next.members[2], waiting: true, unread: true, preview: "New preview" };
  next.groups = [{ id: 5, name: "New group", position: 0, sources: [] }];
  api.membersList.mockResolvedValue(next);
  send({ type: "MembersChanged" });
  await flush();
  expect($(".mb-pill-t").textContent).toBe("Updated name");
  expect($<HTMLInputElement>("#mb-det-name").value).toBe("Updated name");
  expect($(".mb-col").textContent).toContain("New group");
  expect($('.mb-col [data-member="4"]').textContent).toContain("New preview");
  expect($('.mb-col [data-member="4"] .mb-dot')).not.toBeNull();
});

test("memory notifications refresh the visible own and shared lists with correct scope", async () => {
  api.core.mockImplementation(async (method: string) => method === "memory.list" ? { memories: [], summary: null } : { questions: [] });
  mount();
  await flush();
  await pick(3);
  $(".mb-pill").click();
  $('[data-tab="memory"]').click();
  await flush();
  api.core.mockClear();
  send({ type: "MemoryChanged", member_id: 4, space: "own" });
  await flush();
  expect(api.core).not.toHaveBeenCalled();
  const note = { name: "reference-live", description: "Fresh note", type: "reference", origin: "told", updated: "", expires: null };
  api.core.mockImplementation(async (method: string) => method === "memory.list" ? { memories: [note], summary: null } : { questions: [] });
  send({ type: "MemoryChanged", member_id: 3, space: "own" });
  await flush();
  expect(api.core).toHaveBeenLastCalledWith("memory.list", { member_id: 3, space: "own" });
  expect($('[data-mem-space="own"]').textContent).toContain("Fresh note");
  send({ type: "MemoryChanged", member_id: 0, space: "shared" });
  await flush();
  expect(api.core).toHaveBeenLastCalledWith("memory.list", { space: "shared" });
  expect($('[data-mem-space="shared"]').textContent).toContain("Fresh note");
});

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
    expect(api.agentAskText).toHaveBeenCalledWith("你好", [], 0, expect.objectContaining({ requestId: expect.any(String) }));
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
  test("every composer has a send button immediately before voice, enabled by its draft", async () => {
    const agent = mount(); await flush();
    for (const id of [0, 3, 4]) {
      await pick(id);
      const sendButton = $<HTMLButtonElement>(".cmp-send"), input = $<HTMLTextAreaElement>(".cmp-input");
      expect(sendButton.nextElementSibling).toBe($(".cmp-voice"));
      expect(sendButton.disabled).toBe(true);
      input.value = "Send this"; input.dispatchEvent(new Event("input"));
      expect(sendButton.disabled).toBe(false);
      sendButton.click(); await flush();
      expect(api.agentAskText.mock.calls[api.agentAskText.mock.calls.length - 1]?.[0]).toBe("Send this");
      expect(sendButton.disabled).toBe(true);
    }
    agent.hide();
  });
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

  test("its composer says Message and the name, has the voice button, and asks that member", async () => {
    mount();
    await flush();
    await pick(3);
    expect($<HTMLTextAreaElement>(".cmp-input").placeholder).toBe("Message 日文老師");
    expect($(".cmp-voice").hidden).toBe(false);
    expect($(".mb-pill-t").textContent).toBe("日文老師");
    // 清除全部 and Raphael's settings are his thread's
    expect($('[data-act="clear"]').hidden).toBe(true);
    expect($('[data-act="settings"]').hidden).toBe(true);
    $<HTMLTextAreaElement>(".cmp-input").value = "考我";
    $<HTMLFormElement>(".composer").requestSubmit();
    await flush();
    expect(api.agentAskText).toHaveBeenCalledWith("考我", [], 3, expect.objectContaining({ requestId: expect.any(String) }));
  });

  test("a member's voice button sends its thread and keeps the pending voice bubble there", async () => {
    vi.useFakeTimers();
    const agent = mount(); await flush(); await pick(3);
    const button = $(".cmp-voice");
    const emblem = button.innerHTML;
    button.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    expect(api.agentVoice).toHaveBeenLastCalledWith("down", 3);
    send({ type: "Dictation", phase: "listening", agent: true, member_id: 3 });
    // Releasing after changing threads must keep the original press's target.
    await pick(4);
    document.body.dispatchEvent(new KeyboardEvent("keyup", { key: "Enter", bubbles: true }));
    expect(api.agentVoice).toHaveBeenLastCalledWith("up", 3);
    send({ type: "Dictation", phase: "processing", agent: true, member_id: 3 });
    expect(document.querySelector('[data-key="hearing"]')).toBeNull();
    await pick(0);
    expect($(".cmp-voice").innerHTML).toBe(emblem);
    expect(document.querySelector('[data-key="hearing"]')).toBeNull();
    await pick(3);
    expect(document.querySelector('[data-key="hearing"]')).not.toBeNull();
    api.agentHistoryList.mockImplementation(async (q: { member_id?: number }) => ({ turns: q.member_id === 3
      ? [turn(50, { member_id: 3, source: "voice", question: "My spoken question", text: "My member reply" }), ...THREADS[3]]
      : THREADS[q.member_id ?? 0] }));
    send({ type: "AgentTurn", turn_id: 50, member_id: 3 });
    await vi.advanceTimersByTimeAsync(200); await flush();
    expect(document.querySelector('[data-key="hearing"]')).toBeNull();
    expect($('[data-id="50"] .bubble').textContent).toContain("My spoken question");
    expect(document.querySelector('[data-id="50"] .me-src')).toBeNull();
    expect($('.thread').textContent).toContain("My member reply");
    await pick(0);
    expect($('.thread').textContent).not.toContain("My spoken question");
    agent.hide();
  });

  test("a restored member voice confirmation stays in its own thread", async () => {
    api.agentQuestionOpen.mockResolvedValue({ id: "voice-confirm", member_id: 3, prefix: "問", text: "Send it?", tier: "T2", timeout_ms: 60_000 });
    const agent = mount(); await flush();
    expect($(".ask-card").hidden).toBe(true);
    await pick(3);
    expect($(".ask-card").hidden).toBe(false);
    expect($(".ask-q").textContent).toBe("Send it?");
    await pick(4);
    expect($(".ask-card").hidden).toBe(true);
    agent.hide();
  });

  test.each(["hold", "toggle"] as const)("a member shares the listening emblem in %s mode, while the global key belongs to Raphael", async mode => {
    vi.stubGlobal("matchMedia", () => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
    vi.spyOn(HTMLElement.prototype, "getClientRects").mockReturnValue([{}] as unknown as DOMRectList);
    api.agentHistoryList.mockImplementation(async (q: { member_id?: number }) => ({ turns: q.member_id === 3 ? [] : THREADS[q.member_id ?? 0] }));
    const agent = mount(mode); await flush(); await pick(3);
    const button = $(".cmp-voice");
    button.dispatchEvent(new KeyboardEvent("keydown", { key: " ", bubbles: true }));
    send({ type: "Dictation", phase: "listening", agent: true, member_id: 3 });
    expect(document.querySelector(".ra-flyer .av")).not.toBeNull();
    expect(button.classList.contains("vm-away")).toBe(true);
    send({ type: "VoiceLevel", value: 0.6 });
    expect($(".ra-flyer").style.getPropertyValue("--lvl")).toBe("0.60");
    await pick(4);
    expect(document.querySelector(".ra-flyer")).toBeNull();
    await pick(3);
    expect(document.querySelector(".ra-flyer")).not.toBeNull();
    agent.hide(); agent.show(); await flush();
    expect(document.querySelector(".ra-flyer")).not.toBeNull();
    document.body.dispatchEvent(new KeyboardEvent("keyup", { key: " ", bubbles: true }));
    if (mode === "toggle") { $(".ra-flyer").click(); await flush(); }
    expect(api.agentVoice.mock.calls).toEqual(mode === "toggle" ? [["down", 3], ["up", 3], ["down", 3], ["up", 3]] : [["down", 3], ["up", 3]]);
    send({ type: "Dictation", phase: "processing", agent: true, member_id: 3 });
    expect(document.querySelector(".ra-flyer")).toBeNull();
    expect(document.querySelector('[data-key="hearing"]')).not.toBeNull();
    send({ type: "Dictation", phase: "failed", agent: true });
    expect($(".ra-empty").hidden).toBe(false);
    // A global press from a member's thread never animates that member's voice button.
    send({ type: "Dictation", phase: "listening", agent: true });
    expect(document.querySelector(".ra-flyer")).toBeNull();
    await pick(0);
    expect(document.querySelector(".ra-flyer .av")).not.toBeNull();
    send({ type: "Dictation", phase: "processing", agent: true });
    expect(document.querySelector('[data-key="hearing"]')).not.toBeNull();
    await pick(3);
    expect(document.querySelector('[data-key="hearing"]')).toBeNull();
    agent.hide();
  });

  test("processing first seen after reopening does not settle against an older voice turn", async () => {
    api.agentHistoryList.mockImplementation(async (q: { member_id?: number }) => ({ turns: q.member_id === 3
      ? [turn(31, { member_id: 3, source: "voice" })] : THREADS[q.member_id ?? 0] }));
    const agent = mount(); await flush(); await pick(3);
    send({ type: "Dictation", phase: "processing", agent: true, member_id: 3 });
    await pick(4); await pick(3);
    expect(document.querySelector('[data-key="hearing"]')).not.toBeNull();
    agent.hide();
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

  test("a question without a composer request belongs to Raphael's global hotkey", async () => {
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
    expect(api.membersCreate).toHaveBeenCalledWith({ name: "進度官", role: "", personality: "", instructions: "", group_id: null, ui_language: "zh-TW" });
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
    $<HTMLElement>('#app-popover [data-action="1"]').click();
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


test("rooms replace the main conversation pane and member selection returns to its thread", async()=>{
 const agent=mount();await flush();const original=agent.el.querySelector<HTMLElement>(".mb-chat")!;
 window.dispatchEvent(new CustomEvent("raphael:open-room",{detail:{roomId:1}}));await flush();
 expect(original.hidden).toBe(true);expect(agent.el.querySelector<HTMLElement>(".au-room-pane")!.hidden).toBe(false);expect(agent.el.querySelector(".au-room-heading")!.textContent).toContain("Launch");expect(document.querySelector('[role="dialog"]')).toBeNull();
 await pick(3);expect(original.hidden).toBe(false);expect(agent.el.querySelector<HTMLElement>(".au-room-pane")!.hidden).toBe(true);expect(agent.el.querySelector(".thread")!.textContent).toContain("10");
});
