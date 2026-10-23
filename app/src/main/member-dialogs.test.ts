// @vitest-environment happy-dom
// The dialogs of the members (member-dialogs.ts): 新增成員 as the mockup draws it, the question before
// a delete, the routine the core offers, and a group's places.
import { beforeEach, describe, expect, test, vi } from "vitest";
import { type NewMemberResult, confirmDelete, editSources, offerRoutine, openNewMember, pictureType } from "./member-dialogs";
import { toolChoices } from "./members";
import type { MemberGroup } from "./types";

const GROUPS: MemberGroup[] = [
  { id: 1, name: "專案", position: 0, sources: [{ kind: "github", ref: "example/app" }] },
  { id: 2, name: "學習", position: 1, sources: [] },
];
const TOOLS = toolChoices([{ id: "notion", name: "Notion", enabled: true } as never]);
const flush = async (): Promise<void> => {
  for (let i = 0; i < 10; i++) await Promise.resolve();
};

beforeEach(() => {
  document.body.innerHTML = "";
});

function open(over: Partial<Parameters<typeof openNewMember>[0]> = {}) {
  const create = vi.fn(async (_result: NewMemberResult) => true);
  const dialog = openNewMember({ groups: GROUPS, groupId: null, presets: [], tools: TOOLS, taken: ["拉斐爾", "文書"], create, ...over });
  const $ = <T extends HTMLElement>(q: string) => dialog.el.querySelector<T>(q)!;
  return { dialog, create, $ };
}

describe("新增成員", () => {
  test("picture, name, role, instructions, group and tools, with 取消 and 建立", () => {
    const { dialog } = open();
    expect(dialog.el.querySelector("h2")!.textContent).toBe("新增成員");
    expect([...dialog.el.querySelectorAll(".mb-frow > span:first-child")].map((s) => s.textContent)).toEqual(["名字", "角色", "指示", "分組", "工具"]);
    expect(dialog.el.querySelector(".mb-new-av .mb-av-letter")).not.toBeNull();
    expect([...dialog.el.querySelectorAll(".mb-dlg-act .btn")].map((b) => b.textContent)).toEqual(["取消", "建立"]);
    // the name takes the focus
    expect(document.activeElement?.id).toBe("mb-new-name");
  });

  test("the placeholder picture is the first character of the name typed", () => {
    const { $ } = open();
    const name = $<HTMLInputElement>("#mb-new-name");
    name.value = "日文老師";
    name.dispatchEvent(new Event("input"));
    expect($(".mb-av-letter").textContent).toBe("日");
  });

  test("建立 hands over what was typed; every tool ticked is the default and is not sent", async () => {
    const { $, create } = open({ groupId: 2 });
    $<HTMLInputElement>("#mb-new-name").value = " 日文老師 ";
    $<HTMLInputElement>("#mb-new-role").value = "N2 嚴格版";
    $<HTMLTextAreaElement>("#mb-new-instr").value = "每天出 10 題";
    $('[data-dlg="create"]').click();
    await flush();
    expect(create).toHaveBeenCalledWith({
      member: { name: "日文老師", role: "N2 嚴格版", instructions: "每天出 10 題", group_id: 2 },
      picture: null,
    });
    expect(document.querySelector(".mb-scrim")).toBeNull();
  });

  test("the tools are a checklist; the ones left ticked are sent", async () => {
    const { $, create } = open();
    $<HTMLInputElement>("#mb-new-name").value = "行程管家";
    $('[data-dlg="tools"]').click();
    const items = [...document.querySelectorAll<HTMLElement>("#app-popover [data-tool]")];
    expect(items.map((b) => b.getAttribute("aria-checked"))).toEqual(["true", "true", "true", "true", "true"]);
    items[0].click();
    items[3].click();
    expect($('[data-dlg="tools"]').textContent).toBe("讀網頁、查天氣、Notion");
    $('[data-dlg="create"]').click();
    await flush();
    expect(create.mock.calls[0]![0].member.tools).toEqual(["web_read", "weather", "notion"]);
  });

  test("the group is a dropdown, and nothing else is asked in the dialog", async () => {
    const { $, create } = open();
    $('[data-dlg="group"]').click();
    document.querySelector<HTMLElement>('#app-popover [data-value="1"]')!.click();
    expect($('[data-dlg="group"]').textContent).toContain("專案");
    expect($(".tg")).toBeNull();
    $<HTMLInputElement>("#mb-new-name").value = "進度官";
    $('[data-dlg="create"]').click();
    await flush();
    expect(create.mock.calls[0]![0]).toEqual({ member: { name: "進度官", role: "", instructions: "", group_id: 1 }, picture: null });
  });

  test("no name, or a name already taken, is not sent", async () => {
    const { $, create } = open();
    $('[data-dlg="create"]').click();
    $<HTMLInputElement>("#mb-new-name").value = "文書";
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
