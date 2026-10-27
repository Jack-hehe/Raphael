// @vitest-environment happy-dom
import { expect, test, vi } from "vitest";
import { columnHTML, createMemberColumn } from "./member-column";
import { closeMenu } from "./ui";
import type { Member } from "./types";

const member = (id: number, patch: Partial<Member> = {}): Member => ({ id, name: `Member ${id}`, role: "", instructions: "", avatar: "", group_id: null, model: "", tools: null, position: id, created: 0, unread: false, waiting: false, preview: "", last_at: null, ...patch });
test("hidden members stay out of navigation and pinned members precede all groups", () => {
  const el = document.createElement("div");
  el.innerHTML = columnHTML({ list: { members: [member(0), member(1, { hidden: true }), member(2), member(3, { pinned: true, group_id: 4 })], groups: [{ id: 4, name: "Work", position: 0, sources: [] }] }, current: 0, previews: new Map(), query: "", folded: new Set(), fresh: new Set() });
  expect([...el.querySelectorAll<HTMLElement>(".mb-row")].map(row => row.dataset.member)).toEqual(["0", "3", "2"]);
  expect(el.querySelector(".mb-pinned [data-member='3']")).not.toBeNull();
});
test("the sidebar exposes new conversations and member management actions", () => {
  const action = vi.fn();
  const column = createMemberColumn({ view: () => ({ list: { members: [member(0), member(1)], groups: [] }, current: 0, previews: new Map(), fresh: new Set() }), select: action, newMember: action, newGroup: action, moveMember: action, deleteMember: action, renameGroup: action, editSources: action, deleteGroup: action, newConversation: action, memberAction: action });
  document.body.append(column.el); column.draw();
  column.el.querySelector<HTMLElement>("[data-col=add]")!.click();
  expect(document.querySelector("[data-pick=conversation]")).not.toBeNull();
  closeMenu();
  column.el.querySelector<HTMLElement>("[data-member-more='1']")!.click();
  expect([...document.querySelectorAll<HTMLElement>("#app-popover [data-pick]")].map(el => el.dataset.pick)).toEqual(["pin", "move", "primary", "unread", "read-all", "edit", "duplicate", "clear", "hide"]);
  document.querySelector<HTMLElement>("[data-pick=hide]")!.click();
  expect(action).toHaveBeenCalledWith(1, "hide");
});
