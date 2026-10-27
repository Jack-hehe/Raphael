// @vitest-environment happy-dom
// The permission card and the email card in the 拉斐爾 page (happy-dom), with the app and the core faked: the question is
// answered once, by the card and not also by the gold card; an edit is saved as a version on the digest the card holds, and
// 傳送 sends the digest of the version that results, never an older one.
import { beforeEach, describe, expect, test, vi } from "vitest";
import type { AgentTurn, AppContext, MailDraftCard, MainEvent, PermissionCard } from "../types";

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
  openExternalUrl: vi.fn(),
  permissionAnswer: vi.fn(),
  mailSave: vi.fn(),
  mailSend: vi.fn(),
  mailDiscard: vi.fn(),
  mailHandover: vi.fn(),
  core: vi.fn(),
}));
vi.mock("../api", () => api);

import { Cleared } from "../cleared";
import { createAgent } from "./agent";

const AT = Date.parse("2026-10-20T10:00:00+08:00");

function turn(id: number, more: Partial<AgentTurn> = {}): AgentTurn {
  return {
    id, created_at_ms: AT + id * 1000, question: `問題 ${id}`, route: "answer", prefix: "答", text: `回答 ${id}`, detail: null,
    status: "done", session_id: `type-${id}`, source: "app", member_id: 0, ...more,
  };
}

const permission = (more: Partial<PermissionCard> = {}): PermissionCard => ({
  id: "q1", source: "ask", kind: "tool", service: "Gmail", tool: "send_email", summary: "是否讓「Gmail」執行「send_email」？", reason: null,
  digest: "d".repeat(64), always: true, state: "pending", expires_at_ms: AT + 30_000, ...more,
});

const mail = (more: Partial<MailDraftCard> = {}): MailDraftCard => ({
  id: "dr1", mode: "card", state: "draft", version: 1, digest: "a".repeat(64), from: { key: "mcp:gmail/send_email", name: "Gmail" },
  senders: [{ key: "mcp:gmail/send_email", name: "Gmail" }, { key: "mcp:work/send", name: "Work mail" }], to: "mei@example.com", cc: "",
  subject: "週四會議", body: "您好，週四下午三點可以開會。", offer: null, error: null, holder_id: 0, author_name: "", ...more,
});

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
  for (let i = 0; i < 12; i++) await Promise.resolve();
};

beforeEach(() => {
  document.body.innerHTML = "";
  for (const fn of Object.values(api)) fn.mockReset();
  api.agentQuestionOpen.mockResolvedValue(null);
  api.agentVoice.mockResolvedValue(undefined);
  api.agentQuestionAnswer.mockResolvedValue(undefined);
  api.core.mockResolvedValue(null);
});

const asked = { type: "AgentQuestion", id: "q1", prefix: "問", text: "是否讓「Gmail」執行「send_email」？", tier: "T2", timeout_ms: 30_000, yes: "允許一次", no: "拒絕", always: "一律允許" } as const;

describe("the permission card in the page", () => {
  test("the question is answered by the card, and the gold card stays away", async () => {
    api.agentHistoryList.mockResolvedValue({ turns: [turn(1, { status: "confirm", text: "", permissions: [permission()] })] });
    const agent = mount();
    await flush();
    send(asked);
    await flush();
    expect(document.querySelector('[data-permission-card="q1"]')).not.toBeNull();
    expect(document.querySelector<HTMLElement>(".ask-card")!.hidden).toBe(true);
    const once = document.querySelector<HTMLButtonElement>('[data-act="perm-once"]')!;
    expect(once.disabled).toBe(false);
    once.click();
    await flush();
    expect(api.agentQuestionAnswer).toHaveBeenCalledExactlyOnceWith("q1", true);
    agent.hide();
  });

  test("拒絕 says no, and 一律允許 says yes with always", async () => {
    api.agentHistoryList.mockResolvedValue({ turns: [turn(1, { status: "confirm", text: "", permissions: [permission()] })] });
    mount();
    await flush();
    send(asked);
    await flush();
    document.querySelector<HTMLButtonElement>('[data-act="perm-deny"]')!.click();
    await flush();
    expect(api.agentQuestionAnswer).toHaveBeenLastCalledWith("q1", false);
    // The first answer is on its way: no second answer goes while it is.
    document.querySelector<HTMLButtonElement>('[data-act="perm-once"]')?.click();
    await flush();
    expect(api.agentQuestionAnswer).toHaveBeenCalledTimes(1);
  });

  test("a question without a card keeps the gold card, and a card without its question can not be pressed", async () => {
    api.agentHistoryList.mockResolvedValue({ turns: [turn(1, { status: "confirm", text: "" })] });
    mount();
    await flush();
    send({ ...asked, id: "plain", yes: undefined, no: undefined, always: undefined });
    await flush();
    expect(document.querySelector<HTMLElement>(".ask-card")!.hidden).toBe(false);
    document.body.innerHTML = "";
    api.agentHistoryList.mockResolvedValue({ turns: [turn(1, { status: "confirm", text: "", permissions: [permission()] })] });
    mount();
    await flush();
    expect(document.querySelector<HTMLButtonElement>('[data-act="perm-once"]')!.disabled).toBe(true);
  });

  test("the always answer is only sent where the card offers it", async () => {
    api.agentHistoryList.mockResolvedValue({ turns: [turn(1, { status: "confirm", text: "", permissions: [permission()] })] });
    mount();
    await flush();
    send(asked);
    await flush();
    document.querySelector<HTMLButtonElement>('[data-act="perm-always"]')!.click();
    await flush();
    expect(api.agentQuestionAnswer).toHaveBeenCalledExactlyOnceWith("q1", true, true);
  });

  test("a card of a task that went on after the computer was given back answers through its own door, with its digest", async () => {
    api.permissionAnswer.mockResolvedValue({ card: permission({ source: "run", state: "allowed" }), answered: true });
    api.agentHistoryList.mockResolvedValue({ turns: [turn(1, { status: "confirm", text: "", permissions: [permission({ id: "r1", source: "run", kind: "step", service: "", tool: "", always: false })] })] });
    mount();
    await flush();
    const once = document.querySelector<HTMLButtonElement>('[data-act="perm-once"]')!;
    expect(once.disabled).toBe(false); // no question is held for it
    once.click();
    await flush();
    expect(api.permissionAnswer).toHaveBeenCalledExactlyOnceWith("allow", "r1", "d".repeat(64));
    expect(api.agentQuestionAnswer).not.toHaveBeenCalled();
  });
});

describe("the email card in the page", () => {
  const drafted = (more: Partial<MailDraftCard> = {}): AgentTurn => turn(1, { drafts: [mail(more)] });
  const field = (name: string): HTMLInputElement | HTMLTextAreaElement => document.querySelector(`[data-mail-field="${name}"]`)!;
  const type = (name: string, value: string): void => {
    field(name).value = value;
    field(name).dispatchEvent(new Event("change", { bubbles: true }));
  };

  test("an edit is saved as a new version on the digest the card holds, with every field as the owner sees it", async () => {
    api.agentHistoryList.mockResolvedValue({ turns: [drafted()] });
    api.mailSave.mockResolvedValue({ draft: mail({ version: 2, digest: "b".repeat(64), cc: "boss@example.com" }), saved: true });
    mount();
    await flush();
    type("cc", "boss@example.com");
    await flush();
    expect(api.mailSave).toHaveBeenCalledExactlyOnceWith("dr1", "a".repeat(64), {
      to: "mei@example.com", cc: "boss@example.com", subject: "週四會議", body: "您好，週四下午三點可以開會。", via: "mcp:gmail/send_email" });
  });

  test("saves one after the other, each on the digest the one before came back with", async () => {
    api.agentHistoryList.mockResolvedValue({ turns: [drafted()] });
    api.mailSave
      .mockResolvedValueOnce({ draft: mail({ version: 2, digest: "b".repeat(64) }), saved: true })
      .mockResolvedValueOnce({ draft: mail({ version: 3, digest: "c".repeat(64) }), saved: true });
    mount();
    await flush();
    type("subject", "週四會議（改）");
    type("cc", "boss@example.com");
    await flush();
    expect(api.mailSave.mock.calls.map((call) => call[1])).toEqual(["a".repeat(64), "b".repeat(64)]);
    expect(api.mailSave.mock.calls[1][2]).toMatchObject({ subject: "週四會議（改）", cc: "boss@example.com" });
  });

  test("傳送 first saves what is typed and sends the digest of the version that results, once", async () => {
    api.agentHistoryList.mockResolvedValue({ turns: [drafted()] });
    api.mailSave.mockResolvedValue({ draft: mail({ version: 2, digest: "b".repeat(64) }), saved: true });
    api.mailSend.mockResolvedValue({ draft: mail({ state: "sent" }), sent: true });
    mount();
    await flush();
    field("subject").value = "週四會議（改）"; // typed, not yet left: no change event has come
    document.querySelector<HTMLButtonElement>('[data-act="mail-send"]')!.click();
    await flush();
    expect(api.mailSave).toHaveBeenCalledExactlyOnceWith("dr1", "a".repeat(64), expect.objectContaining({ subject: "週四會議（改）" }));
    expect(api.mailSend).toHaveBeenCalledExactlyOnceWith("dr1", "b".repeat(64));
    expect(api.mailSave.mock.invocationCallOrder[0]).toBeLessThan(api.mailSend.mock.invocationCallOrder[0]);
  });

  test("an untouched card sends the digest it holds without saving", async () => {
    api.agentHistoryList.mockResolvedValue({ turns: [drafted()] });
    api.mailSend.mockResolvedValue({ draft: mail({ state: "sent" }), sent: true });
    mount();
    await flush();
    document.querySelector<HTMLButtonElement>('[data-act="mail-send"]')!.click();
    await flush();
    expect(api.mailSave).not.toHaveBeenCalled();
    expect(api.mailSend).toHaveBeenCalledExactlyOnceWith("dr1", "a".repeat(64));
  });

  test("a draft that moved on under the card is not sent: the owner looks it over first", async () => {
    api.agentHistoryList.mockResolvedValue({ turns: [drafted()] });
    // The member revised it meanwhile: the core refuses the edit on the old digest and answers with the current draft.
    api.mailSave.mockResolvedValue({ draft: mail({ version: 2, digest: "e".repeat(64), subject: "新的主旨" }), saved: false });
    mount();
    await flush();
    field("body").value = "我改了內文。";
    document.querySelector<HTMLButtonElement>('[data-act="mail-send"]')!.click();
    await flush();
    expect(api.mailSave).toHaveBeenCalledTimes(1);
    expect(api.mailSend).not.toHaveBeenCalled();
    expect(document.querySelector(".toast")?.textContent ?? "").toContain("草稿剛被更新");
  });

  test("捨棄 discards and sends nothing; the answer to the choice card hands over and sends nothing", async () => {
    api.agentHistoryList.mockResolvedValue({ turns: [drafted({ offer: { member_id: 2, name: "焦媽媽" } })] });
    api.mailDiscard.mockResolvedValue({ draft: mail({ state: "discarded" }), discarded: true });
    api.mailHandover.mockResolvedValue({ draft: mail(), handed: true });
    mount();
    await flush();
    document.querySelector<HTMLButtonElement>('[data-act="mail-handover-yes"]')!.click();
    await flush();
    expect(api.mailHandover).toHaveBeenCalledExactlyOnceWith("dr1", "yes");
    document.querySelector<HTMLButtonElement>('[data-act="mail-discard"]')!.click();
    await flush();
    expect(api.mailDiscard).toHaveBeenCalledExactlyOnceWith("dr1");
    expect(api.mailSend).not.toHaveBeenCalled();
    expect(api.mailSave).not.toHaveBeenCalled();
  });

  test("the sender is chosen from a dropdown and saved as a new version", async () => {
    api.agentHistoryList.mockResolvedValue({ turns: [drafted()] });
    api.mailSave.mockResolvedValue({ draft: mail({ version: 2, digest: "b".repeat(64), from: { key: "mcp:work/send", name: "Work mail" } }), saved: true });
    mount();
    await flush();
    document.querySelector<HTMLButtonElement>('[data-act="mail-from"]')!.click();
    await flush();
    const pick = document.querySelector<HTMLButtonElement>('#app-popover [data-value="mcp:work/send"]');
    expect(pick).not.toBeNull();
    pick!.click();
    await flush();
    expect(api.mailSave).toHaveBeenCalledExactlyOnceWith("dr1", "a".repeat(64), expect.objectContaining({ via: "mcp:work/send" }));
  });

  test("a card drawn again after an unrelated change keeps what the owner was typing", async () => {
    api.agentHistoryList.mockResolvedValue({ turns: [drafted()] });
    mount();
    await flush();
    field("body").value = "還沒存的內文";
    // Another turn arrives: the list is loaded again with a changed card (a colleague's offer came in).
    api.agentHistoryList.mockResolvedValue({ turns: [drafted({ offer: { member_id: 2, name: "焦媽媽" } })] });
    send({ type: "AgentTurn", turn_id: 1, member_id: 0 });
    await new Promise((resolve) => setTimeout(resolve, 250));
    await flush();
    expect(document.querySelector('[data-mail-offer="dr1"]')).not.toBeNull();
    expect(field("body").value).toBe("還沒存的內文");
  });
});
