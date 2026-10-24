// @vitest-environment happy-dom
// The details panel (member-details.ts): what it shows for a member and for Raphael, and the calls
// its edits make: the name, the role and the instructions saved when they lose the focus, the group,
// the routines, the tools, and the two memories with the move between them.
import { type Mock, beforeEach, describe, expect, test, vi } from "vitest";
import type { AppContext, Member, MemberPatch, MembersList, Routine } from "./types";

const api = vi.hoisted(() => ({
  core: vi.fn(),
  routinesList: vi.fn(),
  routinesCreate: vi.fn(),
  routinesUpdate: vi.fn(),
  routinesDelete: vi.fn(),
  avatarPick: vi.fn(),
  avatarSave: vi.fn(),
  avatarClear: vi.fn(),
  avatarGet: vi.fn(),
}));
vi.mock("./api", () => api);

import { createDetails } from "./member-details";

const member = (id: number, more: Partial<Member> = {}): Member => ({
  id, name: `成員${id}`, role: "", instructions: "", avatar: "", group_id: null, model: "", tools: null, position: id, created: 0, unread: false, waiting: false, preview: "", last_at: null, ...more,
});
const LIST: MembersList = {
  members: [member(0, { name: "拉斐爾" }), member(3, { name: "日文老師", role: "N2 嚴格版", group_id: 20, instructions: "每天出 10 題" }), member(4, { name: "行程管家" })],
  groups: [{ id: 20, name: "學習", position: 0, sources: [{ kind: "github", ref: "example/app" }] }, { id: 21, name: "專案", position: 1, sources: [] }],
};
const ROUTINE: Routine = { id: 9, member_id: 3, title: "每天的單字", prompt: "出題", schedule: { kind: "daily", time: "08:00" }, enabled: true, last_run: null, next_run: null };
const flush = async (): Promise<void> => {
  for (let i = 0; i < 10; i++) await Promise.resolve();
};

let current = 3;
let host: Parameters<typeof createDetails>[0] & { update: Mock<(id: number, patch: MemberPatch) => Promise<boolean>>; close: Mock<() => void> };

function mount() {
  host = {
    app: { undo: { begin: vi.fn() } } as unknown as AppContext,
    member: () => LIST.members.find((m) => m.id === current)!,
    list: () => LIST,
    presets: () => [],
    update: vi.fn(async (_id: number, _patch: MemberPatch) => true),
    setPicture: vi.fn(async () => {}),
    close: vi.fn<() => void>(),
  };
  const panel = createDetails(host);
  document.body.append(panel.el);
  panel.showMember();
  return panel;
}

beforeEach(() => {
  document.body.innerHTML = "";
  current = 3;
  for (const fn of Object.values(api)) fn.mockReset();
  api.routinesList.mockResolvedValue({ routines: [ROUTINE] });
  api.routinesCreate.mockResolvedValue({});
  api.routinesUpdate.mockResolvedValue({});
  api.core.mockImplementation(async (method: string, params: { space?: string }) => {
    if (method === "memory.list") {
      const name = params?.space === "shared" ? "user-city" : "ongoing-words";
      return { memories: [{ name, description: params?.space === "shared" ? "主人住台北" : "在背單字", type: "user", origin: "told", updated: "", expires: null }], summary: null };
    }
    return {};
  });
});

describe("a member's panel", () => {
  test("a personality draft survives redraws and is saved to its original member on switching", async () => {
    const panel = mount();
    await flush();
    const box = panel.el.querySelector<HTMLTextAreaElement>("#mb-det-personality")!;
    box.focus();
    box.value = "嚴厲但耐心";
    panel.draw();
    expect(panel.el.querySelector<HTMLTextAreaElement>("#mb-det-personality")!.value).toBe("嚴厲但耐心");
    expect(host.update).not.toHaveBeenCalled();
    current = 4;
    panel.showMember();
    await flush();
    expect(host.update).toHaveBeenCalledWith(3, { personality: "嚴厲但耐心" });
    expect(panel.el.querySelector<HTMLTextAreaElement>("#mb-det-personality")!.value).toBe("");
  });

  test("stored personality is escaped, unchanged text is not saved, and clearing is saved", async () => {
    const before = LIST.members[1];
    LIST.members[1] = { ...before, personality: '</textarea><script>privateStyle()</script>' };
    try {
      const panel = mount();
      await flush();
      const box = panel.el.querySelector<HTMLTextAreaElement>("#mb-det-personality")!;
      expect(box.value).toBe(LIST.members[1].personality);
      expect(panel.el.querySelector("script")).toBeNull();
      box.dispatchEvent(new FocusEvent("focusout", { bubbles: true }));
      expect(host.update).not.toHaveBeenCalled();
      box.value = " ";
      box.dispatchEvent(new FocusEvent("focusout", { bubbles: true }));
      await flush();
      expect(host.update).toHaveBeenCalledWith(3, { personality: "" });
    } finally {
      LIST.members[1] = before;
    }
  });

  test("personality is separate from instructions and saved on blur", async () => {
    const panel = mount();
    await flush();
    // closed, a long field is its name and its first line behind a button; open, the box
    const head = panel.el.querySelector<HTMLButtonElement>('[data-field="mb-det-personality"]')!;
    expect(head.getAttribute("aria-expanded")).toBe("false");
    expect(head.textContent).toContain("個性");
    expect(panel.el.querySelector<HTMLElement>("#mb-det-personality-body")!.hidden).toBe(true);
    head.click();
    const box = panel.el.querySelector<HTMLTextAreaElement>("#mb-det-personality");
    expect(panel.el.querySelector<HTMLElement>("#mb-det-personality-body")!.hidden).toBe(false);
    expect(document.activeElement).toBe(box);
    expect(box!.maxLength).toBe(2000);
    box!.value = "  嚴厲但耐心。\n直接稱呼你。  ";
    box!.dispatchEvent(new FocusEvent("focusout", { bubbles: true }));
    await flush();
    expect(host.update).toHaveBeenCalledWith(3, { personality: "嚴厲但耐心。\n直接稱呼你。" });
    expect(panel.el.querySelector<HTMLTextAreaElement>("#mb-det-instr")!.value).toBe("每天出 10 題");
    current = 0;
    panel.showMember();
    await flush();
    expect(panel.el.querySelector("#mb-det-personality")).toBeNull();
  });

  test("its picture, its name and role to change in place, and the tabs", async () => {
    const panel = mount();
    await flush();
    expect(panel.el.querySelector<HTMLInputElement>("#mb-det-name")!.value).toBe("日文老師");
    expect(panel.el.querySelector<HTMLInputElement>("#mb-det-role")!.value).toBe("N2 嚴格版");
    expect(panel.el.querySelector(".mb-avbtn .mb-av-letter")!.textContent).toBe("日");
    expect([...panel.el.querySelectorAll(".mb-tabs .tab")].map((b) => b.textContent)).toEqual(["詳細資訊", "記憶"]);
  });

  test("the name is saved when it loses the focus; a blank or a taken one goes back", async () => {
    const panel = mount();
    await flush();
    const name = panel.el.querySelector<HTMLInputElement>("#mb-det-name")!;
    name.focus();
    name.value = "老師";
    name.dispatchEvent(new FocusEvent("focusout", { bubbles: true }));
    await flush();
    expect(host.update).toHaveBeenCalledWith(3, { name: "老師" });
    host.update.mockClear();
    name.value = "行程管家";
    name.dispatchEvent(new FocusEvent("focusout", { bubbles: true }));
    name.value = " ";
    name.dispatchEvent(new FocusEvent("focusout", { bubbles: true }));
    await flush();
    expect(host.update).not.toHaveBeenCalled();
    expect(name.value).toBe("日文老師");
  });

  test("the instructions are saved on blur, only when they changed", async () => {
    const panel = mount();
    await flush();
    const box = panel.el.querySelector<HTMLTextAreaElement>("#mb-det-instr")!;
    expect(box.value).toBe("每天出 10 題");
    box.dispatchEvent(new FocusEvent("focusout", { bubbles: true }));
    expect(host.update).not.toHaveBeenCalled();
    box.value = "每天出 20 題";
    box.dispatchEvent(new FocusEvent("focusout", { bubbles: true }));
    await flush();
    expect(host.update).toHaveBeenCalledWith(3, { instructions: "每天出 20 題" });
  });

  test("the group is a dropdown: 未分組, then the groups", async () => {
    const panel = mount();
    await flush();
    panel.el.querySelector<HTMLElement>('[data-det="group"]')!.click();
    const items = [...document.querySelectorAll<HTMLElement>("#app-popover [data-value]")];
    expect(items.map((b) => b.textContent)).toEqual(["未分組", "學習", "專案"]);
    items[2].click();
    expect(host.update).toHaveBeenCalledWith(3, { group_id: 21 });
  });

  test("no member has a model of its own, and the group's places are chips", async () => {
    const panel = mount();
    await flush();
    expect(panel.el.querySelector(".mb-model, .mp-trigger, .mb-brain")).toBeNull();
    expect([...panel.el.querySelectorAll(".mb-chips .mb-chip")].map((c) => c.textContent)).toEqual(["example/app"]);
  });

  test("its routines: a row with its schedule and switch; the switch turns it off", async () => {
    const panel = mount();
    await flush();
    expect(api.routinesList).toHaveBeenCalledWith(3);
    const row = panel.el.querySelector<HTMLElement>('[data-routine="9"]')!;
    expect(row.textContent).toContain("每天的單字");
    expect(row.textContent).toContain("每天 08:00");
    const toggle = row.querySelector<HTMLElement>('[data-det="routine-switch"]')!;
    expect(toggle.getAttribute("aria-checked")).toBe("true");
    toggle.click();
    await flush();
    expect(api.routinesUpdate).toHaveBeenCalledWith(9, { enabled: false });
  });

  test("a new routine: title, a schedule from the dropdown, what to do; saved with routines.create", async () => {
    const panel = mount();
    await flush();
    panel.el.querySelector<HTMLElement>('[data-det="routine-add"]')!.click();
    document.querySelector<HTMLInputElement>("#au-title")!.value = "每週回顧";
    document.querySelector<HTMLTextAreaElement>("#au-prompt")!.value = "整理這週";
    document.querySelector<HTMLElement>('[data-dlg="kind"][data-kind="every"]')!.click();
    // the title and the prompt typed are kept across the drawing
    expect(document.querySelector<HTMLInputElement>("#au-title")!.value).toBe("每週回顧");
    document.querySelector<HTMLElement>('[data-dlg="hours"]')!.click();
    document.querySelector<HTMLElement>('#app-popover [data-value="6"]')!.click();
    document.querySelector<HTMLElement>('[data-dlg="save"]')!.click();
    await flush();
    expect(api.routinesCreate).toHaveBeenCalledWith({ member_id: 3, title: "每週回顧", prompt: "整理這週", schedule: expect.objectContaining({ kind: "every", hours: 6 }) });
  });

  test("a routine without a title is not sent", async () => {
    const panel = mount();
    await flush();
    panel.el.querySelector<HTMLElement>('[data-det="routine-add"]')!.click();
    document.querySelector<HTMLElement>('[data-dlg="save"]')!.click();
    await flush();
    expect(api.routinesCreate).not.toHaveBeenCalled();
  });

  test("記憶: 共同記憶 and the member's own, each read with its space", async () => {
    const panel = mount();
    await flush();
    panel.el.querySelector<HTMLElement>('[data-tab="memory"]')!.click();
    await flush();
    expect(api.core).toHaveBeenCalledWith("memory.list", { space: "shared" });
    expect(api.core).toHaveBeenCalledWith("memory.list", { space: "own", member_id: 3 });
    const sections = [...panel.el.querySelectorAll<HTMLElement>("[data-mem-section]")];
    expect(sections.map((s) => [s.dataset.memSpace, s.getAttribute("aria-label")])).toEqual([["shared", "共同記憶"], ["own", "日文老師 的記憶"]]);
    expect(sections[0].textContent).toContain("主人住台北");
    expect(sections[1].textContent).toContain("在背單字");
  });

  test("a note's more moves it to the other memory, and both lists are read again", async () => {
    const panel = mount();
    await flush();
    panel.el.querySelector<HTMLElement>('[data-tab="memory"]')!.click();
    await flush();
    panel.el.querySelector<HTMLElement>('[data-mem-space="own"] [data-mem="more"]')!.click();
    const item = document.querySelector<HTMLElement>("#app-popover [data-mem-move]")!;
    expect(item.textContent).toBe("移到共同記憶");
    api.core.mockClear();
    item.click();
    await flush();
    expect(api.core).toHaveBeenCalledWith("memory.move", { name: "ongoing-words", to_space: "shared", member_id: 3 });
    expect(api.core).toHaveBeenCalledWith("memory.list", { space: "own", member_id: 3 });
    expect(api.core).toHaveBeenCalledWith("memory.list", { space: "shared" });
    panel.el.querySelector<HTMLElement>('[data-mem-space="shared"] [data-mem="more"]')!.click();
    expect(document.querySelector("#app-popover [data-mem-move]")!.textContent).toBe("移到 日文老師 的記憶");
  });

  test("× closes it", () => {
    const panel = mount();
    panel.el.querySelector<HTMLElement>('[data-det="close"]')!.click();
    expect(host.close).toHaveBeenCalled();
  });

  test("switching members while instructions are focused saves to the previous member and draws the new member", async () => {
    const panel = mount();
    await flush();
    const box = panel.el.querySelector<HTMLTextAreaElement>("#mb-det-instr")!;
    box.focus();
    box.value = "改成每天 5 題";
    current = 4;
    panel.showMember();
    await flush();
    expect(host.update).toHaveBeenCalledWith(3, { instructions: "改成每天 5 題" });
    expect(panel.el.querySelector<HTMLTextAreaElement>("#mb-det-instr")!.value).toBe("");
  });

  test("renaming a member while tabbing to role updates the face initial in the header", async () => {
    const panel = mount();
    await flush();
    const name = panel.el.querySelector<HTMLInputElement>("#mb-det-name")!;
    const role = panel.el.querySelector<HTMLInputElement>("#mb-det-role")!;
    name.focus();
    name.value = "老師";
    host.update.mockImplementationOnce(async (_id, patch) => {
      await Promise.resolve();
      LIST.members[1] = { ...LIST.members[1], ...patch };
      panel.draw();
      return true;
    });
    name.dispatchEvent(new FocusEvent("focusout", { bubbles: true }));
    role.focus();
    await flush();
    expect(panel.el.querySelector(".mb-avbtn .mb-av-letter")!.textContent).toBe("老");
    LIST.members[1] = { ...LIST.members[1], name: "日文老師" };
  });

  test("double-clicking routine save while create is in flight sends only one request", async () => {
    let finish!: () => void;
    api.routinesCreate.mockImplementation(() => new Promise<Record<string, never>>((r) => (finish = () => r({}))));
    const panel = mount();
    await flush();
    panel.el.querySelector<HTMLElement>('[data-det="routine-add"]')!.click();
    document.querySelector<HTMLInputElement>("#au-title")!.value = "早報";
    document.querySelector<HTMLTextAreaElement>("#au-prompt")!.value = "摘要";
    document.querySelector<HTMLElement>('[data-dlg="kind"][data-kind="daily"]')!.click();
    const save = document.querySelector<HTMLElement>('[data-dlg="save"]')!;
    save.click();
    save.click();
    expect(api.routinesCreate).toHaveBeenCalledTimes(1);
    finish();
    await flush();
  });
});

describe("Raphael's panel", () => {
  test("his picture and his name only, and his routines", async () => {
    current = 0;
    const panel = mount();
    await flush();
    expect(panel.el.querySelector("#mb-det-name")).toBeNull();
    expect(panel.el.querySelector(".mb-who-name")!.textContent).toBe("拉斐爾");
    expect(panel.el.querySelector(".mb-avbtn .av")!.innerHTML).toContain("app-emblem");
    expect(panel.el.querySelector("#mb-det-instr")).toBeNull();
    expect(panel.el.querySelector('[data-det="group"]')).toBeNull();
    expect(api.routinesList).toHaveBeenCalledWith(0);
  });

  test("his picture is changed here: from the PC, nothing of presets", async () => {
    current = 0;
    const panel = mount();
    await flush();
    panel.el.querySelector<HTMLElement>('[data-det="avatar"]')!.click();
    expect([...document.querySelectorAll("#app-popover button")].map((b) => b.textContent)).toEqual(["從電腦選…"]);
    api.avatarPick.mockResolvedValue(null);
    document.querySelector<HTMLElement>("#app-popover button")!.click();
    await flush();
    expect(api.avatarPick).toHaveBeenCalled();
  });

  test("his memory: 共同記憶 and 拉斐爾 的記憶", async () => {
    current = 0;
    const panel = mount();
    await flush();
    panel.el.querySelector<HTMLElement>('[data-tab="memory"]')!.click();
    await flush();
    expect(api.core).toHaveBeenCalledWith("memory.list", { space: "own", member_id: 0 });
    expect([...panel.el.querySelectorAll("[data-mem-section]")].map((s) => s.getAttribute("aria-label"))).toEqual(["共同記憶", "拉斐爾 的記憶"]);
  });
});

test("live routines update while instructions retain their node, draft and caret", async () => {
  const panel = mount();
  await flush();
  const box = panel.el.querySelector<HTMLTextAreaElement>("#mb-det-instr")!;
  box.focus();
  box.value = "Unsaved instructions";
  box.setSelectionRange(2, 6);
  api.routinesList.mockResolvedValue({ routines: [{ ...ROUTINE, title: "Changed live" }] });
  panel.refreshRoutines();
  await flush();
  expect(panel.el.querySelector(".mb-rts")!.textContent).toContain("Changed live");
  expect(panel.el.querySelector("#mb-det-instr")).toBe(box);
  expect(document.activeElement).toBe(box);
  expect(box.value).toBe("Unsaved instructions");
  expect(box.selectionStart).toBe(2);
  expect(host.update).not.toHaveBeenCalled();
});

test("a remote deletion keeps the routine draft editable and refresh failure keeps rows", async () => {
  const panel = mount();
  await flush();
  panel.el.querySelector<HTMLElement>('[data-det="routine-edit"]')!.click();
  const title = document.querySelector<HTMLInputElement>("#au-title")!;
  title.value = "Unsaved routine";
  title.focus();
  api.routinesList.mockResolvedValue({ routines: [] });
  panel.refreshRoutines();
  await flush();
  expect(document.querySelector<HTMLInputElement>("#au-title")!.value).toBe("Unsaved routine");
  expect(document.activeElement?.id).toBe("au-title");
  document.querySelector<HTMLElement>('[data-dlg="cancel"]')!.click();
  api.routinesList.mockResolvedValue({ routines: [ROUTINE] });
  panel.refreshRoutines();
  await flush();
  api.routinesList.mockRejectedValue(new Error("offline"));
  panel.refreshRoutines();
  await flush();
  expect(panel.el.querySelector('[data-routine="9"]')).not.toBeNull();
});

test("a late routine response cannot replace the newly selected member's routines", async () => {
  const panel = mount();
  await flush();
  let resolve!: (value: { routines: Routine[] }) => void;
  api.routinesList.mockReturnValueOnce(new Promise((done) => { resolve = done; }));
  panel.refreshRoutines();
  current = 4;
  api.routinesList.mockResolvedValue({ routines: [{ ...ROUTINE, id: 10, member_id: 4, title: "Other member" }] });
  panel.showMember();
  await flush();
  resolve({ routines: [ROUTINE] });
  await flush();
  expect(panel.el.querySelector(".mb-rts")!.textContent).toContain("Other member");
  expect(panel.el.querySelector('[data-routine="9"]')).toBeNull();
});


test("a focused name keeps its draft while the remote role updates", async () => {
  const panel = mount();
  await flush();
  const name = panel.el.querySelector<HTMLInputElement>("#mb-det-name")!;
  name.focus();
  name.value = "Draft name";
  name.setSelectionRange(2, 4);
  const original = host.member();
  host.member = () => ({ ...original, role: "New remote role" });
  panel.draw();
  expect(panel.el.querySelector<HTMLInputElement>("#mb-det-role")!.value).toBe("New remote role");
  expect(document.activeElement).toBe(name);
  expect(name.value).toBe("Draft name");
  expect(name.selectionStart).toBe(2);
});
