// @vitest-environment happy-dom
// The member column (member-column.ts): Raphael at the top, the groups and 未分組, the search, and
// how a row shows a question that waits and a thread with news.
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { type ColumnHost, columnHTML, createMemberColumn, memberRowHTML } from "./member-column";
import { openNewGroupChat } from "./group-chats";
import { installContextMenus } from "./context-menu";
vi.mock("./group-chats", () => ({ createRoomsSection: () => document.createElement("section"), openNewGroupChat: vi.fn() }));
import type { Member, MembersList } from "./types";

const member = (id: number, more: Partial<Member> = {}): Member => ({
  id, name: `成員${id}`, role: "", instructions: "", avatar: "", group_id: null, model: "", tools: null, position: id, created: 0, unread: false, waiting: false, preview: "", last_at: null, ...more,
});

const LIST: MembersList = {
  members: [
    member(0, { name: "拉斐爾" }),
    member(1, { name: "進度官", role: "Raphael 專案", group_id: 10 }),
    member(2, { name: "文書", role: "資料整理", group_id: 10, waiting: true, preview: "移動 12 個檔案" }),
    member(3, { name: "日文老師", role: "N2 嚴格版", group_id: 20 }),
    member(4, { name: "行程管家", role: "信箱與行事曆", unread: true }),
  ],
  groups: [
    { id: 10, name: "專案", position: 0, sources: [] },
    { id: 20, name: "學習", position: 1, sources: [] },
  ],
};
const previews = new Map([[1, "PR #17 已開"], [2, "移動 12 個檔案"], [3, "今天的 10 題單字已送出"], [4, "明天 10:00 有會議"]]);
const view = (over: Partial<Parameters<typeof columnHTML>[0]> = {}) => ({
  list: LIST, current: 0, previews, query: "", folded: new Set<number>(), fresh: new Set<number>(), ...over,
});
const dom = (html: string): HTMLElement => {
  const div = document.createElement("div");
  div.innerHTML = html;
  return div;
};

describe("the column, drawn", () => {
  test("rows show valid last-message times and omit missing or invalid timestamps", () => {
    const time = new Date(2026, 9, 24, 13, 45).getTime();
    const row = dom(memberRowHTML(member(1, { last_at: time }), view()));
    expect(row.querySelector("time")?.textContent).toBe("13:45");
    expect(row.querySelector("time")?.getAttribute("datetime")).toBe(new Date(time).toISOString());
    for (const last_at of [null, NaN, 1e20]) expect(dom(memberRowHTML(member(1, { last_at }), view())).querySelector("time")).toBeNull();
  });

  test("search matching only Raphael does not show a contradictory empty result", () => {
    const el = dom(columnHTML(view({ query: LIST.members[0].name })));
    expect(el.querySelectorAll(".mb-row")).toHaveLength(1);
    expect(el.querySelector(".mb-none")).toBeNull();
  });
  test("Raphael retains his original first row with a bare 64 px emblem", () => {
    const el = dom(columnHTML(view()));
    const chief = el.querySelector<HTMLElement>(".mb-chief")!;
    expect(el.firstElementChild).toBe(chief);
    expect(chief.classList.contains("mb-row")).toBe(true);
    expect(chief.querySelector(".mb-pin, time, .mb-row-p")).toBeNull();
    expect(chief.dataset.member).toBe("0");
    expect(chief.textContent).toBe("拉斐爾");
    expect(chief.querySelector(".av")!.getAttribute("style")).toBe("--av:64px");
    expect(chief.innerHTML).not.toMatch(/管理員|mb-av|badge/);
    expect(chief.getAttribute("aria-current")).toBe("true");
  });

  test("the primary swaps with Raphael in its group, with default and missing choices unchanged", () => {
    const ids = (el: HTMLElement) => [...el.querySelectorAll<HTMLElement>("[data-member]")].map(row => Number(row.dataset.member));
    const roster = (primary_id: number): MembersList => ({ ...LIST, team_context: { primary_id } as MembersList["team_context"] });
    for (const primary of [0, 99]) expect(ids(dom(columnHTML(view({ list: roster(primary) }))))).toEqual([0, 1, 2, 3, 4]);
    const el = dom(columnHTML(view({ list: roster(3) })));
    expect(ids(el)).toEqual([3, 1, 2, 0, 4]);
    expect(el.querySelector('[data-group="20"] [data-member="0"] .av')).not.toBeNull();
    expect(el.querySelector('[data-group="20"] [data-member="0"] .mb-av')).toBeNull();
    expect(el.querySelector('[data-member-more="0"]')).toBeNull();
    expect(el.querySelector('.mb-chief')?.getAttribute('data-member')).toBe('3');
    expect(el.querySelector('.mb-chief .av')?.getAttribute('style')).toBe('--av:64px');
    expect(el.querySelector('[data-member-more="3"]')).not.toBeNull();
    expect(el.querySelector('.mb-star')).toBeNull();
    expect(LIST.members[3].group_id).toBe(20);
  });

  test("a primary's pinned slot goes to Raphael, and searching finds each member only in its new place", () => {
    const list = { ...LIST, members: LIST.members.map(m => m.id === 1 ? { ...m, pinned: true } : m), team_context: { primary_id: 1 } } as MembersList;
    const el = dom(columnHTML(view({ list })));
    expect(el.querySelector('.mb-pinned [data-member="0"]')).not.toBeNull();
    expect(el.querySelector('.mb-chief')?.getAttribute('data-member')).toBe('1');
    const search = dom(columnHTML(view({ list, query: '拉斐爾' })));
    expect(search.querySelectorAll('[data-member]')).toHaveLength(1);
    expect(search.querySelector('.mb-none')).toBeNull();
    expect(search.querySelector('[data-member="0"] .mb-av')).toBeNull();
  });

  test("the face strip puts the primary first, keeps dots and identifies it without a star", () => {
    const list = { ...LIST, team_context: { primary_id: 4 } } as unknown as MembersList;
    const strip = dom(columnHTML(view({ list, collapsed: true })));
    expect([...strip.querySelectorAll<HTMLElement>('[data-member]')].map(row => row.dataset.member)).toEqual(['4', '1', '2', '3', '0']);
    const primary = strip.querySelector<HTMLElement>('[data-member="4"]')!;
    expect(primary.querySelector('.mb-dot.is-unread')).not.toBeNull();
    expect(primary.getAttribute('aria-label')).toBe('行程管家 · 主要聯絡人');
    expect(primary.dataset.tooltip).toBe(primary.getAttribute('aria-label'));
    expect(strip.querySelector<HTMLElement>('[data-member="0"]')!.dataset.tooltip).toBe('拉斐爾');
    expect(strip.querySelector('[data-member="0"] .mb-av')).toBeNull();
    expect(strip.querySelector('.mb-star')).toBeNull();
  });

  test("the groups under their headers in their order, then 未分組", () => {
    const el = dom(columnHTML(view()));
    expect([...el.querySelectorAll(".mb-gname")].map((n) => n.textContent)).toEqual(["專案", "學習", "未分組"]);
    const ids = (group: string) => [...el.querySelectorAll(`[data-group="${group}"] .mb-row`)].map((n) => (n as HTMLElement).dataset.member);
    expect(ids("10")).toEqual(["1", "2"]);
    expect(ids("20")).toEqual(["3"]);
    expect(ids("none")).toEqual(["4"]);
    // 未分組 has no menu, the groups have one
    expect(el.querySelector('[data-group="none"] [data-group-more]')).toBeNull();
    expect(el.querySelector('[data-group="10"] [data-group-more]')).not.toBeNull();
  });

  test("a row: the picture, the name, the role in small words and the last line", () => {
    const row = dom(columnHTML(view())).querySelector<HTMLElement>('[data-member="1"]')!;
    expect(row.querySelector(".mb-row-t b")!.textContent).toBe("進度官");
    expect(row.querySelector(".mb-row-t i")!.textContent).toBe("Raphael 專案");
    expect(row.querySelector(".mb-row-p")!.textContent).toBe("PR #17 已開");
    expect(row.querySelector(".mb-row-p")!.classList.contains("is-wait")).toBe(false);
    expect(row.querySelector(".mb-dot")).toBeNull();
    expect(row.querySelector(".mb-av-placeholder svg")).not.toBeNull();
  });

  test("a question that waits: the line in gold, 「等你處理：…」, and a gold dot", () => {
    const row = dom(columnHTML(view())).querySelector<HTMLElement>('[data-member="2"]')!;
    const line = row.querySelector(".mb-row-p")!;
    expect(line.textContent).toBe("等你處理：移動 12 個檔案");
    expect(line.classList.contains("is-wait")).toBe(true);
    expect(row.querySelector(".mb-dot.is-wait")!.getAttribute("aria-label")).toBe("等你處理");
  });

  test("a thread with news: a dot of the accent", () => {
    const row = dom(columnHTML(view())).querySelector<HTMLElement>('[data-member="4"]')!;
    expect(row.querySelector(".mb-dot.is-unread")!.getAttribute("aria-label")).toBe("有新訊息");
    expect(row.querySelector(".mb-row-p")!.classList.contains("is-wait")).toBe(false);
  });

  test("the open member's row is the current one; a new member's row fades in", () => {
    const el = dom(columnHTML(view({ current: 3, fresh: new Set([3]) })));
    expect(el.querySelector('[data-member="3"]')!.getAttribute("aria-current")).toBe("true");
    expect(el.querySelector('[data-member="0"]')!.getAttribute("aria-current")).toBe("false");
    expect(el.querySelector('[data-member-item="3"]')!.classList.contains("is-new")).toBe(true);
    expect(el.querySelector('[data-member-item="1"]')!.classList.contains("is-new")).toBe(false);
  });

  test("a folded group keeps its header, its body folded and out of the keyboard's way", () => {
    const el = dom(columnHTML(view({ folded: new Set([10]) })));
    const section = el.querySelector<HTMLElement>('[data-group="10"]')!;
    expect(section.dataset.open).toBe("false");
    expect(section.querySelector(".mb-gbody")!.hasAttribute("inert")).toBe(true);
    expect(section.querySelector("[data-group-fold]")!.getAttribute("aria-expanded")).toBe("false");
  });

  test("a search shows only what matches, folded groups opened, or says nothing matches", () => {
    const el = dom(columnHTML(view({ query: "老師", folded: new Set([20]) })));
    expect([...el.querySelectorAll(".mb-row")].map((n) => (n as HTMLElement).dataset.member)).toEqual(["3"]);
    expect(el.querySelector<HTMLElement>('[data-group="20"]')!.dataset.open).toBe("true");
    const none = dom(columnHTML(view({ query: "沒有這個人" })));
    expect(none.querySelector(".mb-none")!.textContent).toBe("沒有相符的成員");
    expect(none.querySelector(".mb-chief")).toBeNull();
  });
});

describe("the column, used", () => {
  let host: ColumnHost & Record<string, ReturnType<typeof vi.fn>>;
  let stopContextMenus: () => void;
  beforeEach(() => {
    stopContextMenus = installContextMenus(document);
    document.body.innerHTML = "";
    localStorage.clear();
    host = {
      view: () => ({ list: LIST, current: 0, previews, fresh: new Set() }),
      select: vi.fn(), newMember: vi.fn(), newGroup: vi.fn(), moveMember: vi.fn(), deleteMember: vi.fn(),
      renameGroup: vi.fn(), editSources: vi.fn(), deleteGroup: vi.fn(), newConversation: vi.fn(), memberAction: vi.fn(),
    } as never;
  });
  afterEach(() => stopContextMenus());
  const mount = () => {
    const column = createMemberColumn(host);
    document.body.append(column.el);
    column.draw();
    return column;
  };

  test("collapse retains every face, state and selection and persists across recreation", () => {
    const column = mount();
    column.el.querySelector<HTMLElement>('[data-group-fold="10"]')!.click();
    const toggle = column.el.querySelector<HTMLElement>('[data-col="collapse"]')!;
    toggle.click();
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(localStorage.getItem("raphael.members.collapsed")).toBe("true");
    expect(column.el.classList.contains("is-collapsed")).toBe(true);
    expect([...column.el.querySelectorAll<HTMLElement>("[data-member]")].map(row => row.dataset.member)).toEqual(["0", "1", "2", "3", "4"]);
    expect(column.el.querySelector('[data-member="2"] .is-wait')).not.toBeNull();
    expect(column.el.querySelector('[data-member="4"] .is-unread')).not.toBeNull();
    const face = column.el.querySelector<HTMLElement>('[data-member="3"]')!;
    expect(face.dataset.tooltip).toBe(LIST.members[3].name);
    face.click();
    expect(host.select).toHaveBeenCalledWith(3);
    const restored = mount();
    expect(restored.el.classList.contains("is-collapsed")).toBe(true);
    toggle.click();
    expect(localStorage.getItem("raphael.members.collapsed")).toBe("false");
    expect(column.el.querySelector('[data-group="10"]')?.getAttribute("data-open")).toBe("false");
  });

  test("column toggle remains usable when storage reads and writes throw", () => {
    const read = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("Storage unavailable"); });
    const write = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("Storage unavailable"); });
    try {
      const column = mount();
      const before = column.el.classList.contains("is-collapsed");
      column.el.querySelector<HTMLElement>('[data-col="collapse"]')!.click();
      expect(column.el.classList.contains("is-collapsed")).toBe(!before);
    } finally { read.mockRestore(); write.mockRestore(); }
  });

  test("a click on a row opens that member's thread", () => {
    const column = mount();
    column.el.querySelector<HTMLElement>('[data-member="3"] .mb-row-t')!.click();
    expect(host.select).toHaveBeenCalledWith(3);
    column.el.querySelector<HTMLElement>(".mb-chief")!.click();
    expect(host.select).toHaveBeenLastCalledWith(0);
  });

  test("the search narrows the rows as it is typed, and Escape closes it", () => {
    const column = mount();
    column.el.querySelector<HTMLElement>('[data-col="search"]')!.click();
    const input = column.el.querySelector<HTMLInputElement>(".mb-find-input")!;
    expect(column.el.querySelector<HTMLElement>(".mb-find")!.hidden).toBe(false);
    input.value = "信箱";
    input.dispatchEvent(new Event("input"));
    expect([...column.el.querySelectorAll(".mb-row")].map((n) => (n as HTMLElement).dataset.member)).toEqual(["4"]);
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    expect(column.query()).toBe("");
    expect(column.el.querySelectorAll(".mb-row")).toHaveLength(5);
  });

  test("the narrow-window search opens a member and closes its popover", () => {
    const column = mount(), anchor = document.createElement("button");
    document.body.append(anchor); column.openSearch(anchor);
    expect(document.activeElement).toBe(document.querySelector("#app-popover input"));
    document.querySelector<HTMLButtonElement>('#app-popover [data-member="3"]')!.click();
    expect(host.select).toHaveBeenCalledWith(3);
    expect(document.getElementById("app-popover")).toBeNull();
  });

  test("＋ offers 新增成員 and 新增分組", () => {
    const column = mount();
    column.el.querySelector<HTMLElement>('[data-col="add"]')!.click();
    const items = [...document.querySelectorAll<HTMLElement>("#app-popover [data-pick]")];
    expect(items.map(b => b.dataset.pick)).toEqual(["conversation", "member", "group", "group-chat"]);
    items[0].click();
    expect(host.newConversation).toHaveBeenCalled();
  });

  test("the plus menu retains group creation and opens the shared group chat creator", () => {
    const column = mount();
    column.el.querySelector<HTMLElement>('[data-col="add"]')!.click();
    document.querySelector<HTMLElement>('#app-popover [data-pick="group"]')!.click();
    expect(host.newGroup).toHaveBeenCalled();
    column.el.querySelector<HTMLElement>('[data-col="add"]')!.click();
    document.querySelector<HTMLElement>('#app-popover [data-pick="group-chat"]')!.click();
    expect(openNewGroupChat).toHaveBeenCalled();
  });

  test("a row's more gives 移到分組 and 刪除; a right click gives the same", () => {
    const column = mount();
    column.el.querySelector<HTMLElement>('[data-member-more="2"]')!.click();
    expect([...document.querySelectorAll<HTMLElement>("#app-popover [data-pick]")].map(b => b.dataset.pick)).toEqual(["pin", "move", "primary", "unread", "read-all", "edit", "duplicate", "clear", "hide", "delete"]);
    document.querySelector<HTMLElement>('#app-popover [data-pick="primary"]')!.click();
    expect(host.memberAction).toHaveBeenCalledWith(2, "primary");
    column.el.querySelector<HTMLElement>('[data-member-more="2"]')!.click();
    document.querySelector<HTMLElement>('#app-popover [data-pick="delete"]')!.click();
    expect(host.deleteMember).toHaveBeenCalledWith(2);
    column.el.querySelector<HTMLElement>('[data-member-more="2"]')!.click();
    document.querySelector<HTMLElement>('#app-popover [data-pick="hide"]')!.click();
    expect(host.memberAction).toHaveBeenCalledWith(2, "hide");
    column.el.querySelector('[data-member="1"]')!.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, cancelable: true, button: 2, clientX: 40, clientY: 50 }));
    document.querySelector<HTMLElement>('#app-popover [data-pick="move"]')!.click();
    expect(host.moveMember).toHaveBeenCalledWith(1, null, expect.any(HTMLElement));
  });

  test("a group's menu gives 重新命名, 看的地方 and 刪除", () => {
    const column = mount();
    column.el.querySelector<HTMLElement>('[data-group-more="10"]')!.click();
    expect([...document.querySelectorAll("#app-popover [data-pick]")].map((b) => b.textContent)).toEqual(["重新命名", "看的地方", "刪除"]);
    document.querySelector<HTMLElement>('#app-popover [data-pick="rename"]')!.click();
    expect(host.renameGroup).toHaveBeenCalledWith(10);
  });

  test("a group folds and opens where it is", () => {
    const column = mount();
    const fold = column.el.querySelector<HTMLElement>('[data-group-fold="10"]')!;
    fold.click();
    expect(column.el.querySelector<HTMLElement>('[data-group="10"]')!.dataset.open).toBe("false");
    column.draw();
    expect(column.el.querySelector<HTMLElement>('[data-group="10"]')!.dataset.open).toBe("false");
    column.el.querySelector<HTMLElement>('[data-group-fold="10"]')!.click();
    expect(column.el.querySelector<HTMLElement>('[data-group="10"]')!.dataset.open).toBe("true");
  });
});
