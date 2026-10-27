// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { AppContext, Member, MembersList, Page } from "../types";
const api = vi.hoisted(() => ({ membersList: vi.fn(), settingsGet: vi.fn(), membersUpdate: vi.fn(), membersDelete: vi.fn(), membersPresets: vi.fn(), membersSetPrimary: vi.fn(), core: vi.fn() }));
vi.mock("../api", () => api);
import { createTeamPage } from "./team";
import { createMemberColumn } from "../member-column";
import { installContextMenus, uncoveredOverflows } from "../context-menu";
import { setLocale } from "../i18n";
import { closeMenu } from "../ui";
const person = (id: number, patch: Partial<Member> = {}): Member => ({ id, name: `Person ${id}`, role: "Research", instructions: "", avatar: "", group_id: id ? 1 : null, model: "", tools: null, position: id, created: 0, unread: false, waiting: false, preview: "", last_at: null, ...patch });
let list: MembersList, page: Page, go: ReturnType<typeof vi.fn>, stop: () => void;
const flush = async (): Promise<void> => { for (let i = 0; i < 24; i++) await Promise.resolve(); };
const query = <T extends HTMLElement = HTMLElement>(selector: string): T => document.querySelector<T>(selector)!;
const click = (selector: string): void => query(selector).click();
const items = (): string[] => [...document.querySelectorAll<HTMLElement>('#app-popover [data-pick]')].map(node => node.dataset.pick!);
const pointer = (target: Element, type: string, button: number, x: number, y: number): void => {
  target.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true, pointerId: 1, button, buttons: type === "pointerup" ? 0 : button === 2 ? 2 : button === 1 ? 4 : 1, clientX: x, clientY: y }));
};
const drag = (target: Element, button: number, dx: number, dy: number): void => {
  pointer(target, "pointerdown", button, 100, 150); pointer(target, "pointermove", button, 100 + dx, 150 + dy); pointer(target, "pointerup", button, 100 + dx, 150 + dy);
};
const menu = (): void => click('[data-team="member-menu"][data-member="1"]');
const pick = async (id: string): Promise<void> => { menu(); click(`[data-pick="${id}"]`); await flush(); };
const transform = (): number[] => query('.team-board').style.transform.match(/-?[\d.]+/g)!.map(Number);
async function mount(): Promise<void> {
  page = createTeamPage({ go, onEvent: () => () => {} } as unknown as AppContext);
  document.body.append(page.el); page.show(); await flush();
}
beforeEach(async () => {
  document.body.innerHTML = ""; localStorage.clear(); vi.resetAllMocks(); setLocale("en"); go = vi.fn();
  list = { members: [person(0), person(1, { model: "anthropic/own-model", waiting: true }), person(2, { unread: true })], groups: [{ id: 1, name: "Work", position: 0, sources: [] }, { id: 2, name: "Other", position: 1, sources: [] }] };
  api.membersList.mockImplementation(async () => list); api.settingsGet.mockResolvedValue({ settings: { agent_model: "openai/answer-model" } });
  api.membersUpdate.mockResolvedValue({}); api.membersDelete.mockResolvedValue({}); api.membersPresets.mockResolvedValue({ presets: [] }); api.membersSetPrimary.mockResolvedValue({}); api.core.mockResolvedValue({ member: person(9) });
  stop = installContextMenus(); await mount();
});
afterEach(() => { page.hide(); stop(); closeMenu(); document.body.innerHTML = ""; vi.restoreAllMocks(); });

test.each([0, 1, 2])("button %s pans the empty canvas without a menu", button => {
  const viewport = query('.team-board-scroll'); drag(viewport, button, 50, -30);
  expect(transform()).toEqual([50, -30, 1]);
  viewport.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, cancelable: true, button, clientX: 150, clientY: 120 }));
  expect(document.querySelector('#app-popover')).toBeNull();
});
test("right drag on a card suppresses its menu even when it returns to its starting point", () => {
  const card = query('[data-team-card="1"]'); pointer(card, "pointerdown", 2, 100, 150); pointer(card, "pointermove", 2, 180, 150); pointer(card, "pointermove", 2, 100, 150); pointer(card, "pointerup", 2, 100, 150);
  card.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, cancelable: true, button: 2, clientX: 100, clientY: 150 }));
  expect(document.querySelector('#app-popover')).toBeNull();
  pointer(card, "pointerdown", 2, 100, 150); pointer(card, "pointerup", 2, 100, 150);
  card.dispatchEvent(new MouseEvent("contextmenu", { bubbles: true, cancelable: true, button: 2, clientX: 100, clientY: 150 }));
  expect(items()).toContain("delete"); expect(uncoveredOverflows(page.el)).toEqual([]);
});
test("left drag on a card does not pan and dragging an action does not move a group", () => {
  drag(query('[data-team-card="1"]'), 0, 30, 40); expect(transform()).toEqual([0, 0, 1]);
  drag(query('[data-team="group-menu"]'), 0, 30, 40); expect(query('[data-team-group="1"]').style.transform).toBe("");
});
test("wheel pans; Ctrl+wheel preserves the canvas point under the pointer and clamps zoom", () => {
  const viewport = query('.team-board-scroll'); vi.spyOn(viewport, 'getBoundingClientRect').mockReturnValue({ left: 20, top: 30 } as DOMRect);
  viewport.dispatchEvent(new WheelEvent("wheel", { deltaX: 12, deltaY: 24, bubbles: true, cancelable: true })); expect(transform()).toEqual([-12, -24, 1]);
  const [x, y, z] = transform(), point = { x: (200 - x) / z, y: (100 - y) / z };
  const wheel = new WheelEvent("wheel", { ctrlKey: true, deltaY: -80, bubbles: true, cancelable: true });
  Object.defineProperties(wheel, { ctrlKey: { value: true }, clientX: { value: 220 }, clientY: { value: 130 } }); viewport.dispatchEvent(wheel);
  const [nx, ny, nz] = transform(); expect((200 - nx) / nz).toBeCloseTo(point.x); expect((100 - ny) / nz).toBeCloseTo(point.y);
  for (const deltaY of [-100000, 100000]) { const wheel = new WheelEvent("wheel", { deltaY, bubbles: true, cancelable: true }); Object.defineProperties(wheel, { ctrlKey: { value: true }, clientX: { value: 220 }, clientY: { value: 130 } }); viewport.dispatchEvent(wheel); expect(transform()[2]).toBe(deltaY < 0 ? 1.5 : 0.5); }
});
test("fit centers every visible panel in the viewport within the zoom range", () => {
  const viewport = query('.team-board-scroll'); Object.defineProperties(viewport, { clientWidth: { value: 800 }, clientHeight: { value: 600 } });
  vi.spyOn(viewport, 'getBoundingClientRect').mockReturnValue({ left: 0, top: 0 } as DOMRect);
  for (const node of page.el.querySelectorAll<HTMLElement>('.team-chief, [data-team-group]')) vi.spyOn(node, 'getBoundingClientRect').mockReturnValue({ left: 100, top: 100, right: 700, bottom: 500 } as DOMRect);
  click('[data-team="fit"]'); const [x, y, z] = transform(); expect(z).toBeCloseTo(1.28); expect(x + 400 * z).toBeCloseTo(400); expect(y + 300 * z).toBeCloseTo(300);
});
test("group header drag saves positions by id, restores them on another page, and reset restores auto layout", async () => {
  drag(query('[data-team-group="1"] > header'), 0, 140, 60);
  expect(JSON.parse(localStorage.getItem('raphael.team.positions')!)).toEqual({ "1": { x: 140, y: 60 } });
  page.hide(); page.el.remove(); await mount(); expect(query('[data-team-group="1"]').style.transform).toBe('translate(140px, 60px)');
  click('[data-team="new"]'); click('[data-pick="reset-layout"]'); expect(query('[data-team-group="1"]').style.transform).toBe(''); expect(transform()).toEqual([0, 0, 1]); expect(JSON.parse(localStorage.getItem('raphael.team.positions')!)).toEqual({});
});
test("group movement uses board coordinates after zoom and pointer cancellation ends movement", () => {
  click('[data-team="zoom-in"]'); drag(query('[data-team-group="1"] > header'), 0, 110, 55);
  const position = JSON.parse(localStorage.getItem('raphael.team.positions')!)["1"]; expect(position.x).toBeCloseTo(100); expect(position.y).toBeCloseTo(50);
  const viewport = query('.team-board-scroll'); pointer(viewport, 'pointerdown', 1, 1, 1); pointer(viewport, 'pointercancel', 1, 1, 1); pointer(viewport, 'pointermove', 1, 100, 100); expect(transform()[0]).toBe(0);
});
test.each(['invalid', '{"1":{"x":"bad","y":2}}', '[]'])('bad saved layout %s falls back to automatic placement', async saved => {
  page.hide(); page.el.remove(); localStorage.setItem('raphael.team.positions', saved); await mount(); expect(query('[data-team-group="1"]').style.transform).toBe('');
});
test("unavailable storage still permits dragging and reset", async () => {
  page.hide(); page.el.remove(); vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw Error('unavailable'); }); vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw Error('unavailable'); });
  await mount(); drag(query('[data-team-group="1"] > header'), 0, 30, 60); expect(query('[data-team-group="1"]').style.transform).toBe('translate(30px, 60px)'); click('[data-team="new"]'); click('[data-pick="reset-layout"]'); expect(query('[data-team-group="1"]').style.transform).toBe('');
});
test("cards show actual models, waiting before unread, and double click opens that conversation", () => {
  expect(query('[data-team-card="1"] .team-model').textContent).toContain('own-model'); expect(query('[data-team-card="2"] .team-model').textContent).toContain('answer-model');
  expect(query('[data-team-card="1"] .is-wait')).not.toBeNull(); expect(query('[data-team-card="2"] .is-unread')).not.toBeNull();
  const listener = vi.fn(); window.addEventListener('raphael:open-member', listener); query('[data-team-card="1"]').dispatchEvent(new MouseEvent('dblclick', { bubbles: true })); expect(go).toHaveBeenCalledWith('agent'); expect(listener.mock.calls[0][0].detail.memberId).toBe(1); window.removeEventListener('raphael:open-member', listener);
});
test("card overflow has exactly the column's labels, icons and items, including delete", () => {
  const column = createMemberColumn({ view: () => ({ list, current: 0, previews: new Map(), fresh: new Set() }), select: vi.fn(), newMember: vi.fn(), newGroup: vi.fn(), moveMember: vi.fn(), deleteMember: vi.fn(), renameGroup: vi.fn(), editSources: vi.fn(), deleteGroup: vi.fn() });
  document.body.append(column.el); column.draw(); column.menu(1, query('[data-member-more="1"]')); const expected = query('#app-popover').innerHTML; closeMenu(); menu(); expect(query('#app-popover').innerHTML).toBe(expected); expect(items()).toEqual(['pin', 'move', 'primary', 'unread', 'read-all', 'edit', 'duplicate', 'clear', 'hide', 'delete']);
});
test.each([['pin', { pinned: true }], ['hide', { hidden: true }]])('%s updates the same member fields', async (action, patch) => { await pick(action); expect(api.membersUpdate).toHaveBeenCalledWith(1, patch); });
test.each([['unread', 'members.unread', { id: 1 }], ['read-all', 'members.mark_all_read', {}]])('%s uses the column RPC', async (action, method, args) => { await pick(action); expect(api.core).toHaveBeenCalledWith(method, args); expect(go).not.toHaveBeenCalled(); });
test("move chooses a destination through the shared submenu", async () => { await pick('move'); click('#app-popover [data-value="2"]'); await flush(); expect(api.membersUpdate).toHaveBeenCalledWith(1, { group_id: 2 }); });
test("primary opens the shared picker and applies the choice", async () => { await pick('primary'); expect(query('.mb-primary-picker [data-id="1"]').getAttribute('aria-checked')).toBe('true'); click('[data-dlg="set"]'); await flush(); expect(api.membersSetPrimary).toHaveBeenCalledWith(1); });
test("duplicate opens the newly created member's conversation", async () => { await pick('duplicate'); expect(api.core).toHaveBeenCalledWith('members.duplicate', { id: 1 }); expect(go).toHaveBeenCalledWith('agent'); });
test("edit opens the shared profile and saves", async () => { await pick('edit'); query<HTMLInputElement>('#mb-new-name').value = 'Updated'; click('[data-dlg="save"]'); await flush(); expect(api.membersUpdate).toHaveBeenCalledWith(1, expect.objectContaining({ name: 'Updated' })); });
test("clear and delete require their existing confirmations", async () => {
  await pick('clear'); expect(api.core).not.toHaveBeenCalled(); click('[data-dlg="cancel"]'); expect(api.core).not.toHaveBeenCalled();
  await pick('clear'); click('[data-dlg="clear"]'); await flush(); expect(api.core).toHaveBeenCalledWith('agent.history.clear', { member_id: 1 });
  await pick('delete'); expect(api.membersDelete).not.toHaveBeenCalled(); click('[data-dlg="cancel"]'); await pick('delete'); click('[data-dlg="delete"]'); await flush(); expect(api.membersDelete).toHaveBeenCalledExactlyOnceWith(1);
});
test("drag target lights up with the member preview, clears on leave/end/drop, and moves only members", async () => {
  const card = query('[data-team-member="1"]'), target = query('[data-team-group="2"]'); const transfer = { setData: vi.fn(), getData: () => '1', types: ['application/x-raphael-member'] };
  const event = (node: Element, name: string): void => { const e = new Event(name, { bubbles: true, cancelable: true }); Object.defineProperty(e, 'dataTransfer', { value: transfer }); node.dispatchEvent(e); };
  event(card, 'dragstart'); expect(transfer.setData).toHaveBeenCalledWith('application/x-raphael-member', '1'); event(target, 'dragover'); expect(target.classList.contains('is-drop-target')).toBe(true); expect(target.querySelector('.team-drop-preview')?.textContent).toContain('Person 1'); expect(uncoveredOverflows(page.el)).toEqual([]);
  event(target, 'dragleave'); expect(target.querySelector('.team-drop-preview')).toBeNull(); event(target, 'dragover'); event(target, 'drop'); await flush(); expect(api.membersUpdate).toHaveBeenCalledWith(1, { group_id: 2 }); expect(page.el.querySelector('.is-drop-target, .team-drop-preview, .is-dragging')).toBeNull();
});
