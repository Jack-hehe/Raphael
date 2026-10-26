// @vitest-environment happy-dom
// The dialogs of the members (member-dialogs.ts): 新增成員 as the mockup draws it, the question before
// a delete, the routine the core offers, and a group's places.
import { beforeEach, describe, expect, test, vi } from "vitest";
import { type NewMemberResult, confirmDelete, editSources, offerRoutine, openNewMember, pictureType } from "./member-dialogs";
import type { MemberGroup } from "./types";

const GROUPS: MemberGroup[] = [
  { id: 1, name: "專案", position: 0, sources: [{ kind: "github", ref: "example/app" }] },
  { id: 2, name: "學習", position: 1, sources: [] },
];
const flush = async (): Promise<void> => {
  for (let i = 0; i < 10; i++) await Promise.resolve();
};

beforeEach(() => {
  document.body.innerHTML = "";
});

function open(over: Partial<Parameters<typeof openNewMember>[0]> = {}) {
  const create = vi.fn(async (_result: NewMemberResult) => true);
  const dialog = openNewMember({ groups: GROUPS, groupId: null, presets: [], taken: ["拉斐爾", "文書"], create, ...over });
  const $ = <T extends HTMLElement>(q: string) => dialog.el.querySelector<T>(q)!;
  return { dialog, create, $ };
}

describe("新增成員", () => {
  test("a distinct personality field is sent with the member", async () => {
    const { dialog, $, create } = open();
    const box = dialog.el.querySelector<HTMLTextAreaElement>("#mb-new-personality");
    expect(box).not.toBeNull();
    expect(box!.maxLength).toBe(2000);
    box!.value = "  嚴厲、耐心。\n稱呼你。  ";
    $<HTMLInputElement>("#mb-new-name").value = "Teacher";
    $<HTMLTextAreaElement>("#mb-new-instr").value = "Teach N1.";
    $('[data-dlg="create"]').click();
    await flush();
    expect(create.mock.calls[0]![0].member).toMatchObject({
      personality: "嚴厲、耐心。\n稱呼你。", instructions: "Teach N1.",
    });
  });

  test("picture, name, role, instructions and group, with 取消 and 建立", () => {
    const { dialog } = open();
    expect(dialog.el.querySelector("h2")!.textContent).toBe("新增成員");
    expect([...dialog.el.querySelectorAll("#mp-page-identity .mb-frow > span:first-child")].map((s) => s.textContent)).toEqual(["名字", "角色", "簡介", "分組"]);
    expect(dialog.el.querySelectorAll('[role="tab"]')).toHaveLength(7);
    expect(dialog.el.querySelector(".mb-new-av .mp-person")).not.toBeNull();
    expect([...dialog.el.querySelectorAll(".mb-dlg-act .btn")].map((b) => b.textContent)).toEqual(["取消", "建立"]);
    // the name takes the focus
    expect(document.activeElement?.id).toBe("mb-new-name");
  });

  test("the default silhouette remains visible after typing a name", () => {
    const { $ } = open();
    const name = $<HTMLInputElement>("#mb-new-name");
    name.value = "日文老師";
    name.dispatchEvent(new Event("input"));
    expect($(".mp-person svg")).not.toBeNull();
  });

  test("建立 hands over what was typed", async () => {
    const { $, create } = open({ groupId: 2 });
    $<HTMLInputElement>("#mb-new-name").value = " 日文老師 ";
    $<HTMLInputElement>("#mb-new-role").value = "N2 嚴格版";
    $<HTMLTextAreaElement>("#mb-new-instr").value = "每天出 10 題";
    $('[data-dlg="create"]').click();
    await flush();
    expect(create).toHaveBeenCalledWith({
      member: { name: "日文老師", role: "N2 嚴格版", personality: "", instructions: "每天出 10 題", group_id: 2, blurb: "", reply_style: "full", voice: "", read_aloud: false, notify: false, ask_before_members: false },
      picture: null,
    });
    expect(document.querySelector(".mb-scrim")).toBeNull();
  });

  test("the group is a dropdown and no model or tool override is offered", async () => {
    const { $, create } = open();
    $('[data-dlg="group"]').click();
    document.querySelector<HTMLElement>('#app-popover [data-value="1"]')!.click();
    expect($('[data-dlg="group"]').textContent).toContain("專案");
    expect(document.querySelector("select")).toBeNull();
    expect(document.querySelector('[data-dlg="model"], [data-dlg="tools"]')).toBeNull();
    $<HTMLInputElement>("#mb-new-name").value = "進度官";
    $('[data-dlg="create"]').click();
    await flush();
    expect(create.mock.calls[0]![0]).toMatchObject({ member: { name: "進度官", role: "", personality: "", instructions: "", group_id: 1 }, picture: null });
  });

  test("no name, or a name already taken, is not sent", async () => {
    const { $, create } = open({ taken: ["日文 老師"] });
    $('[data-dlg="create"]').click();
    $<HTMLInputElement>("#mb-new-name").value = "日文   老師";
    $('[data-dlg="create"]').click();
    $<HTMLInputElement>("#mb-new-name").value = "Raphael";
    $('[data-dlg="create"]').click();
    await flush();
    expect(create).not.toHaveBeenCalled();
    expect(document.querySelector(".mb-scrim")).not.toBeNull();
  });

  test("取消 and Escape close it", () => {
    open().$('[data-dlg="cancel"]').click();
    expect(document.querySelector(".mb-scrim")).toBeNull();
    open();
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    expect(document.querySelector(".mb-scrim")).toBeNull();
  });

  test("a preset is chosen from the picture's popover", async () => {
    const url = "data:image/png;base64,iVBORw0KGgo=";
    const { $, create } = open({ presets: [{ avatar: "preset:a", name: "a", url }] });
    $('[data-dlg="avatar"]').click();
    expect([...document.querySelectorAll("#app-popover button")].map((b) => b.getAttribute("aria-label") ?? b.textContent)).toEqual(["頭像 1", "從電腦選…"]);
    document.querySelector<HTMLElement>('#app-popover [data-preset="preset:a"]')!.click();
    expect($(".mb-new-av img").getAttribute("src")).toBe(url);
    $<HTMLInputElement>("#mb-new-name").value = "日文老師";
    $('[data-dlg="create"]').click();
    await flush();
    expect(create.mock.calls[0]![0].member.avatar).toBe("preset:a");
  });

  test("with no presets, the only choice is a picture from the PC", () => {
    const { $ } = open();
    $('[data-dlg="avatar"]').click();
    expect([...document.querySelectorAll("#app-popover button")].map((b) => b.textContent)).toEqual(["從電腦選…"]);
  });

  test("the computer picker stays inside its dialog so its synthetic click cannot dismiss the profile", () => {
    const { $, dialog } = open();
    $('[data-dlg="avatar"]').click();
    document.querySelector<HTMLElement>('#app-popover [data-pick="0"]')!.click();
    expect(dialog.el.isConnected).toBe(true);
    expect(dialog.el.querySelector('input[type="file"]')).not.toBeNull();
  });

  test("a preset without a picture URL keeps the default silhouette after typing a name", () => {
    const { $ } = open({ presets: [{ avatar: "preset:a", name: "a", url: "" }] });
    $('[data-dlg="avatar"]').click();
    document.querySelector<HTMLElement>('#app-popover [data-preset="preset:a"]')!.click();
    const name = $<HTMLInputElement>("#mb-new-name");
    name.value = "進度官";
    name.dispatchEvent(new Event("input"));
    expect($(".mp-person svg")).not.toBeNull();
  });

  test("Enter in name or role submits (unless composing), and Tab wraps inside the dialog", async () => {
    const { $, dialog, create } = open();
    const name = $<HTMLInputElement>("#mb-new-name");
    name.value = "日文老師";
    name.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true, isComposing: true }));
    expect(create).not.toHaveBeenCalled();

    const closeBtn = $<HTMLButtonElement>('[data-dlg="close"]');
    const createBtn = $<HTMLButtonElement>('[data-dlg="create"]');
    closeBtn.focus();
    closeBtn.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", shiftKey: true, bubbles: true }));
    expect(document.activeElement).toBe(createBtn);
    createBtn.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", bubbles: true }));
    expect(document.activeElement).toBe(closeBtn);

    name.focus();
    name.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    await flush();
    expect(create).toHaveBeenCalledTimes(1);
    expect(dialog.el.isConnected).toBe(false);
  });

  test("while create is in flight, Escape, backdrop click and menus are blocked; failed create keeps focus inside", async () => {
    let finish!: (ok: boolean) => void;
    const create = vi.fn(() => new Promise<boolean>((r) => (finish = r)));
    const { $, dialog } = open({ create });
    $<HTMLInputElement>("#mb-new-name").value = "日文老師";
    const createBtn = $<HTMLButtonElement>('[data-dlg="create"]');
    createBtn.focus();
    createBtn.click();
    expect(create).toHaveBeenCalledTimes(1);

    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    dialog.el.parentElement!.click();
    $('[data-dlg="close"]').click();
    $('[data-dlg="group"]').click();
    expect(document.querySelector(".mb-scrim")).not.toBeNull();
    expect(document.getElementById("app-popover")).toBeNull();

    finish(false);
    await flush();
    expect(document.querySelector(".mb-scrim")).not.toBeNull();
    expect(dialog.el.contains(document.activeElement)).toBe(true);
  });

  test("Escape with stacked dialogs closes only the top dialog", async () => {
    open();
    const top = confirmDelete("文書");
    expect(document.querySelectorAll(".mb-scrim")).toHaveLength(2);
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    expect(await top).toBe(false);
    expect(document.querySelectorAll(".mb-scrim")).toHaveLength(1);
  });
});

test("a picture of the PC is a PNG, JPG, WebP or GIF", () => {
  expect(pictureType({ name: "a.png", type: "image/png" })).toBe("image/png");
  expect(pictureType({ name: "a.JPG", type: "" })).toBe("image/jpeg");
  expect(pictureType({ name: "a.svg", type: "image/svg+xml" })).toBeNull();
  expect(pictureType({ name: "a.exe", type: "" })).toBeNull();
});

test("a delete is asked first: 刪除 says yes, 取消 no", async () => {
  const yes = confirmDelete("文書");
  expect(document.querySelector(".mb-dialog h2")!.textContent).toBe("刪除「文書」？");
  expect([...document.querySelectorAll(".mb-dlg-act .btn")].map((b) => b.textContent)).toEqual(["取消", "刪除"]);
  const dialog = document.querySelector<HTMLElement>(".mb-dialog")!;
  expect(document.getElementById(dialog.getAttribute("aria-describedby")!)?.textContent).toBe("此操作無法復原。");
  expect(dialog.querySelector('[data-dlg="delete"]')?.classList.contains("pri")).toBe(true);
  expect(document.activeElement).toBe(document.querySelector('[data-dlg="cancel"]'));
  document.querySelector<HTMLElement>('[data-dlg="delete"]')!.click();
  expect(await yes).toBe(true);
  const no = confirmDelete("文書");
  document.querySelector<HTMLElement>('[data-dlg="cancel"]')!.click();
  expect(await no).toBe(false);
});

test("the routine the core offers is the owner's to take: 加入 says yes, 不用 no", async () => {
  const offer = { title: "每天早上的進度", prompt: "看進度", schedule: { kind: "daily" as const, time: "09:00" } };
  const yes = offerRoutine(offer);
  expect(document.querySelector(".mb-dialog h2")!.textContent).toBe("加入例行任務？");
  expect(document.querySelector(".mb-offer-t")!.textContent).toBe("每天早上的進度每天 09:00");
  expect([...document.querySelectorAll(".mb-dlg-act .btn")].map((b) => b.textContent)).toEqual(["不用", "加入"]);
  document.querySelector<HTMLElement>('[data-dlg="yes"]')!.click();
  expect(await yes).toBe(true);
  const no = offerRoutine(offer);
  document.querySelector<HTMLElement>('[data-dlg="no"]')!.click();
  expect(await no).toBe(false);
  const left = offerRoutine(offer);
  document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  expect(await left).toBe(false);
});

test("a group's places: added by what is typed, taken off by their ×, kept with 儲存", async () => {
  const done = editSources(GROUPS[0]);
  const input = document.querySelector<HTMLInputElement>("#mb-source")!;
  input.value = "https://github.com/example/other";
  document.querySelector<HTMLElement>('[data-dlg="add"]')!.click();
  input.value = "not a place";
  document.querySelector<HTMLElement>('[data-dlg="add"]')!.click();
  expect([...document.querySelectorAll(".mb-chip")].map((c) => c.textContent)).toEqual(["example/app", "example/other"]);
  document.querySelector<HTMLElement>('[data-dlg="drop"][data-i="0"]')!.click();
  document.querySelector<HTMLElement>('[data-dlg="save"]')!.click();
  expect(await done).toEqual([{ kind: "github", ref: "example/other" }]);
});
