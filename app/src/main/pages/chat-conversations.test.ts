// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
const api = vi.hoisted(() => ({ statusGet: vi.fn(async () => ({ openrouter: { state: "ok" }, spend: { month_usd: 0, limit_usd: 10 } })), core: vi.fn(), agentHistoryList: vi.fn(), agentQuestionOpen: vi.fn(), agentAskText: vi.fn(), membersList: vi.fn(), roomsList: vi.fn(), roomMessages: vi.fn(), membersCommandsList: vi.fn(), membersRead: vi.fn(), roomSend: vi.fn() }));
vi.mock("../api", () => api);
import { createAgent } from "./agent";
import { Cleared } from "../cleared";
import { closeMenu } from "../ui";
import { setLocale } from "../i18n";
import { ico } from "../ui";
import type { AppContext, MainEvent } from "../types";
const flush = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };
afterEach(() => { closeMenu(); document.body.replaceChildren(); vi.clearAllMocks(); vi.useRealTimers(); });

test("the stacked member and conversation title toggles details with one state; the gear still opens Settings", async () => {
  api.agentQuestionOpen.mockResolvedValue(null);
  api.agentHistoryList.mockResolvedValue({ turns: [] });
  api.core.mockImplementation(async method => method === "conversations.list" ? { conversations: [{ id: 1, member_id: 0, is_default: 1, title: "Research" }] } : method === "background.questions.list" ? { questions: [] } : {});
  const go = vi.fn();
  const page = createAgent({ go, device: () => ({}), onEvent: () => () => {}, undo: { begin() {} }, cleared: new Cleared() } as unknown as AppContext);
  document.body.append(page.el); page.show(); await flush();
  const title = page.el.querySelector<HTMLButtonElement>(".conversation-trigger")!;
  expect(title.querySelector(".mb-pill-t")?.textContent).toBe("拉斐爾");
  expect(title.querySelector(".conversation-caption")?.textContent).toBe("Research");
  expect(title.querySelector("button")).toBeNull();
  expect(page.el.querySelector(".conversation-head")?.textContent).not.toContain(" / ");
  expect(page.el.querySelectorAll(".mb-head .ra-tools button")).toHaveLength(1);
  const expectedGear = document.createElement("div"); expectedGear.innerHTML = ico("gear");
  expect(page.el.querySelector('[data-act="settings"] path')?.getAttribute("d")).toBe(expectedGear.querySelector("path")?.getAttribute("d"));
  expect(page.el.querySelector('[data-act="clear"]')).toBeNull();
  expect(title.hasAttribute("aria-haspopup")).toBe(false);
  expect(title.querySelector(".i")).toBeNull();
  const initial = title.getAttribute("aria-expanded") === "true";
  title.click(); await flush();
  expect(title.getAttribute("aria-expanded")).toBe(String(!initial));
  if (initial) { title.click(); await flush(); }
  expect(page.el.querySelector<HTMLElement>(".mb-det")!.hidden).toBe(false);
  expect(document.querySelector("#app-popover")).toBeNull();
  page.el.querySelector<HTMLButtonElement>('[data-det="close"]')!.click(); await flush();
  expect(title.getAttribute("aria-expanded")).toBe("false");
  page.hide(); page.show(); await flush();
  expect(title.getAttribute("aria-expanded")).toBe("false");
  title.click(); await flush();
  expect(title.getAttribute("aria-expanded")).toBe("true");
  page.el.querySelector<HTMLButtonElement>('[data-act="settings"]')!.click();
  expect(go).toHaveBeenCalledWith("settings", "agent");
  page.hide();
});

test("quota notices stay immediately above the composer and disappear at reset", async () => {
  vi.useFakeTimers(); setLocale("zh-TW");
  vi.setSystemTime(new Date("2026-10-24T14:06:00+08:00"));
  api.agentQuestionOpen.mockResolvedValue(null);
  api.agentHistoryList.mockResolvedValue({ turns: [] });
  api.core.mockImplementation(async method => method === "conversations.list" ? { conversations: [{ id: 1, member_id: 0, is_default: 1, title: "Main" }] } :
    method === "quota.list" ? { quotas: [{ provider: "codex", reset_at_ms: Date.now() + 1000 }] } :
    method === "background.questions.list" ? { questions: [] } : {});
  const page = createAgent({ go() {}, device: () => ({}), onEvent: () => () => {}, undo: { begin() {} }, cleared: new Cleared() } as unknown as AppContext);
  document.body.append(page.el); page.show(); await flush();
  const notice = page.el.querySelector<HTMLElement>(".quota-chips")!;
  expect(notice.nextElementSibling).toBe(page.el.querySelector(".composer"));
  expect(notice.textContent).toBe("Codex 額度已滿，下午 2:06 重設");
  expect(notice.hidden).toBe(false);
  await vi.advanceTimersByTimeAsync(1000);
  expect(notice.hidden).toBe(true);
  expect(notice.textContent).toBe("");
  page.hide();
});

test("switching conversations scopes history, quotes the entire answer and sends the chosen ID", async () => {
  api.agentQuestionOpen.mockResolvedValue(null);
  const conversations = [{ id: 1, member_id: 0, title: "Main", is_default: 1 }, { id: 2, member_id: 0, title: "Research", is_default: 0 }];
  api.core.mockImplementation(async method => method === "conversations.list" ? { conversations } : method === "agent.speech.status" ? { ready: true } : method === "background.questions.list" ? { questions: [] } : {});
  api.agentHistoryList.mockImplementation(async ({ conversation_id }) => ({ turns: [{ id: conversation_id, conversation_id, member_id: 0, question: "Explain", text: "**Entire** answer\n\nSecond paragraph.", prefix: "", created_at_ms: 1000, status: "done", route: "answer", source: "app", detail: null }] }));
  const page = createAgent({ go() {}, device: () => ({}), onEvent: () => () => {}, undo: { begin() {} }, cleared: new Cleared() } as unknown as AppContext);
  document.body.append(page.el); page.show(); await flush();
  if (page.el.querySelector(".conversation-trigger")!.getAttribute("aria-expanded") !== "true") page.el.querySelector<HTMLButtonElement>(".conversation-trigger")!.click(); await flush();
  document.querySelector<HTMLButtonElement>('.mb-det [data-conversation-open="2"]')!.click(); await flush();
  expect(api.agentHistoryList).toHaveBeenLastCalledWith(expect.objectContaining({ conversation_id: 2 }));
  page.el.querySelector<HTMLButtonElement>('.ra [data-act="reply-message"]')!.click();
  expect(page.el.querySelector(".cmp-quote .quote-text")?.textContent).toBe("《Entire answer\nSecond paragraph.》");
  const input = page.el.querySelector<HTMLTextAreaElement>(".cmp-input")!; input.value = "Continue"; input.dispatchEvent(new Event("input"));
  page.el.querySelector(".composer")!.dispatchEvent(new Event("submit", { cancelable: true })); await flush();
  expect(api.agentAskText).toHaveBeenCalledWith("Continue", [], 0, expect.objectContaining({ conversationId: 2, quote: expect.objectContaining({ text: "《Entire answer\nSecond paragraph.》" }) }));
  page.el.querySelector<HTMLButtonElement>('[data-act="more-message"]')!.click(); await flush();
  expect(document.querySelector('[data-pick="speak"]')).not.toBeNull();
  document.querySelector<HTMLButtonElement>('[data-pick="speak"]')!.click(); await flush();
  expect(api.core).toHaveBeenCalledWith("agent.speak", { member_id: 0, conversation_id: 2, turn_id: 2 });
  page.hide();
});

test("two conversations of one member retain independent approval cards", async () => {
  api.agentQuestionOpen.mockResolvedValue(null);
  api.agentHistoryList.mockResolvedValue({ turns: [] });
  const conversations = [{ id: 1, member_id: 0, title: "Main", is_default: 1 }, { id: 2, member_id: 0, title: "Research", is_default: 0 }];
  api.core.mockImplementation(async method => method === "conversations.list" ? { conversations } : method === "background.questions.list" ? { questions: [] } : {});
  let emit: (event: MainEvent) => void = () => {};
  const page = createAgent({ go() {}, device: () => ({}), onEvent(handler: typeof emit) { emit = handler; return () => {}; }, undo: { begin() {} }, cleared: new Cleared() } as unknown as AppContext);
  document.body.append(page.el); page.show(); await flush();
  for (const id of [1, 2]) emit({ type: "AgentQuestion", id: `q-${id}`, member_id: 0, conversation_id: id, text: `儲存摘要 ${id}？`, prefix: "問", tier: "T2", timeout_ms: 60_000 });
  expect(page.el.querySelector<HTMLElement>(".ask-card")!.dataset.id).toBe("q-1");
  if (page.el.querySelector(".conversation-trigger")!.getAttribute("aria-expanded") !== "true") page.el.querySelector<HTMLButtonElement>(".conversation-trigger")!.click(); await flush();
  document.querySelector<HTMLButtonElement>('.mb-det [data-conversation-open="2"]')!.click(); await flush();
  expect(page.el.querySelector<HTMLElement>(".ask-card")!.dataset.id).toBe("q-2");
  emit({ type: "AgentQuestionClosed", id: "q-1" });
  expect(page.el.querySelector<HTMLElement>(".ask-card")!.dataset.id).toBe("q-2");
  document.querySelector<HTMLButtonElement>('.mb-det [data-conversation-open="1"]')!.click(); await flush();
  expect(page.el.querySelector<HTMLElement>(".ask-card")!.hidden).toBe(true);
  page.hide();
});

test("rooms hide the member picker and keep replies outside private conversations", async () => {
  const people = { members: [{ id: 0, name: "Raphael", avatar: "", role: "", instructions: "", group_id: null }], groups: [] };
  const conversations = [{ id: 1, member_id: 0, title: "Main", is_default: 1 }, { id: 2, member_id: 0, title: "Research" }];
  api.membersList.mockResolvedValue(people);
  api.membersRead.mockResolvedValue({});
  api.membersCommandsList.mockResolvedValue({ commands: [] });
  api.roomsList.mockResolvedValue({ rooms: [{ id: 7, name: "Planning room", member_ids: [0] }] });
  api.roomMessages.mockResolvedValue({ messages: [{ id: 9, member_id: 0, text: "Room answer", created: 1000, turn_id: 2 }] });
  api.agentQuestionOpen.mockResolvedValue(null);
  api.core.mockImplementation(async method => method === "conversations.list" ? { conversations } : method === "background.questions.list" ? { questions: [] } : {});
  api.agentHistoryList.mockImplementation(async params => ({ turns: params.before_id || params.conversation_id === 2
    ? [{ id: 2, member_id: 0, conversation_id: 2, question: "Research", text: "Named answer", status: "done", created_at_ms: 1000, source: "app", route: "answer", prefix: "" }] : [] }));
  const page = createAgent({ go() {}, device: () => ({}), onEvent: () => () => {}, undo: { begin() {} }, cleared: new Cleared() } as unknown as AppContext);
  document.body.append(page.el); page.show(); await flush();
  window.dispatchEvent(new CustomEvent("raphael:open-room", { detail: { roomId: 7 } })); await flush();
  expect(page.el.querySelector<HTMLElement>(".mb-chat:not(.au-room-pane)")!.hidden).toBe(true);
  expect(page.el.querySelector<HTMLElement>(".au-room-pane")!.hidden).toBe(false);
  expect(page.el.querySelector(".au-room-pane .conversation-trigger")).toBeNull();
  expect(page.el.querySelector(".au-room-pane [data-act='reply-message']")).toBeNull();
  expect(page.el.querySelector('[data-room-act="turn"]')).toBeNull();
  expect(page.el.querySelector<HTMLElement>(".au-room-pane")!.hidden).toBe(false);
  page.hide();
});
