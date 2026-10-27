// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
const core = vi.hoisted(() => vi.fn());
vi.mock("./api", () => ({ core }));
import { mountConversationList } from "./conversation-list";
import { closeMenu } from "./ui";

afterEach(() => { closeMenu(); document.body.replaceChildren(); vi.clearAllMocks(); });
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };

test("quick questions stay first with their localized title and open the persistent conversation", async () => {
  core.mockResolvedValue({ conversations: [
    { id: 2, member_id: 0, title: "Project", updated_at_ms: Date.now() },
    { id: 9, member_id: 0, title: "An old question", kind: "quick", updated_at_ms: 1 },
  ] });
  const host = document.createElement("div"); document.body.append(host);
  const onOpen = vi.fn();
  const list = mountConversationList(host, { memberId: 0, onOpen }); await flush();
  const first = host.querySelector<HTMLButtonElement>("[data-conversation-open]")!;
  expect(first.dataset.conversationOpen).toBe("9");
  expect(first.textContent).toContain("快速提問");
  expect(first.closest(".conversation-row")!.querySelector("[data-conversation-more]")).toBeNull();
  first.click(); expect(onOpen).toHaveBeenCalledWith(9);
  list.destroy();
});

test("searching and opening a conversation keeps its identity; plus creates for this member", async () => {
  core.mockImplementation(async (method: string) => method === "conversations.create" ? { conversation: { id: 9 } } : {
    conversations: [{ id: 2, member_id: 7, title: "Cobalt notes", created_at_ms: 1, updated_at_ms: Date.now() },
                    { id: 3, member_id: 7, title: "Travel", created_at_ms: 2, updated_at_ms: Date.now() }],
  });
  const host = document.createElement("div"); document.body.append(host);
  const onOpen = vi.fn();
  const list = mountConversationList(host, { memberId: 7, onOpen });
  await flush();
  const input = host.querySelector<HTMLInputElement>('input[type="search"]')!;
  input.value = "Cobalt"; input.dispatchEvent(new Event("input"));
  expect(host.querySelectorAll("[data-conversation-open]")).toHaveLength(1);
  host.querySelector<HTMLButtonElement>("[data-conversation-open]")!.click();
  expect(onOpen).toHaveBeenCalledWith(2);
  host.querySelector<HTMLButtonElement>("[data-conversation-new]")!.click();
  await flush();
  expect(core).toHaveBeenCalledWith("conversations.create", { member_id: 7 });
  expect(onOpen).toHaveBeenLastCalledWith(9);
  list.destroy();
});

test("rename saves the selected row and delete waits for confirmation", async () => {
  core.mockResolvedValue({ conversations: [{ id: 2, member_id: 7, title: "Cobalt notes", created_at_ms: 1, updated_at_ms: Date.now() }] });
  const host = document.createElement("div"); document.body.append(host);
  const list = mountConversationList(host, { memberId: 7, onOpen() {} });
  await flush();
  host.querySelector<HTMLButtonElement>("[data-conversation-more]")!.click();
  document.querySelector<HTMLButtonElement>('[data-pick="rename"]')!.click();
  const input = document.querySelector<HTMLInputElement>(".conversation-name")!; input.value = "Renamed";
  document.querySelector<HTMLButtonElement>('[data-dlg="save"]')!.click(); await flush();
  expect(core).toHaveBeenCalledWith("conversations.rename", { id: 2, title: "Renamed" });
  expect(document.querySelector(".mb-dialog")).toBeNull();
  host.querySelector<HTMLButtonElement>("[data-conversation-more]")!.click();
  document.querySelector<HTMLButtonElement>('[data-pick="delete"]')!.click();
  expect(core).not.toHaveBeenCalledWith("conversations.delete", expect.anything());
  document.querySelector<HTMLButtonElement>('[data-dlg="delete"]')!.click(); await flush();
  expect(core).toHaveBeenCalledWith("conversations.delete", { id: 2 });
  expect(document.querySelector(".toasts")?.textContent).toContain("已刪除「Cobalt notes」");
  list.destroy();
});
