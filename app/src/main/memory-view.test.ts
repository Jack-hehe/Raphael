import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { setLocale } from "./i18n";

const api = vi.hoisted(() => ({ core: vi.fn() }));
vi.mock("./api", () => api);
vi.mock("./ui", async (original) => ({ ...(await original<typeof import("./ui")>()), toaster: { info: vi.fn() } }));

import { type MemoryList, type MemoryView, createMemory, emptyMemoryView, memoryHTML } from "./memory-view";
import { toaster } from "./ui";
import type { UndoableAction } from "./undo";

afterEach(() => setLocale("zh-TW"));

const LIST: MemoryList = {
  memories: [
    { name: "user-city", description: "主人住在台北", type: "user", origin: "said", updated: "2026-10-22T21:40:00+08:00", expires: null },
    { name: "preference-short", description: "回答保持兩三句", type: "preference", origin: "inferred", updated: "2026-10-22T21:40:00+08:00", expires: null },
  ],
  summary: { text: "Ongoing:\n- 規劃週末", updated_at_ms: 1 },
};
const view = (over: Partial<MemoryView> = {}): MemoryView => ({ ...emptyMemoryView(), status: LIST, ...over });

/** What a sighted reader sees: the text outside the tags. */
const visible = (html: string): string => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
const buttons = (html: string): string[] => [...html.matchAll(/<button\b[^>]*>/g)].map((m) => m[0]);

describe("the memory's rows", () => {
  test("closed, it is one row: 記憶 and how many notes there are, with a chevron", () => {
    const html = memoryHTML(view());
    expect(html).toMatch(/^<section class="sgroup mem" data-mem-section aria-label="記憶"><div class="slist">/);
    expect(html.match(/<div class="row /g)).toHaveLength(1);
    expect(visible(html)).toBe("記憶 2");
    expect(html).toContain('data-mem="expand" aria-expanded="false"');
  });

  test("open, a row for each note with its type, a pencil and a trash can, then the summary and 全部清除", () => {
    const html = memoryHTML(view({ expanded: true }));
    expect(html.match(/<div class="row mem-sub/g)).toHaveLength(3);
    expect(visible(html)).toBe("記憶 2 本人 主人住在台北 偏好 回答保持兩三句 推測 對話摘要 清除 全部清除");
    expect(html).toContain('data-mem="edit" data-name="user-city"');
    expect(html).toContain('data-mem="delete" data-name="preference-short"');
    // The summary's text itself is not shown: only that there is one, and the way to clear it.
    expect(html).not.toContain("規劃週末");
  });

  test("with no notes it says so and opens on nothing", () => {
    const html = memoryHTML(view({ status: { memories: [], summary: null }, expanded: true }));
    expect(visible(html)).toBe("記憶 尚無記憶");
    expect(html).not.toContain('data-mem="expand"');
    expect(html).not.toContain('data-mem="clear"');
  });

  test("every action is a real button; the icon-only ones are named in their label and tooltip", () => {
    const html = memoryHTML(view({ expanded: true, editing: { name: "user-city", body: "x", draft: "x", description: "主人住在台北", descDraft: "主人住在台北", saving: false, error: null } }));
    for (const tag of buttons(html)) {
      expect(tag).toMatch(/^<button type="button" class="btn/);
      if (/class="btn icon/.test(tag)) {
        expect(tag).toMatch(/aria-label="[^"]+"/);
        expect(tag).toMatch(/title="[^"]+"/);
      }
    }
    expect(html).toContain('aria-label="刪除「主人住在台北」" title="刪除「主人住在台北」"');
  });

  test("an open note is its title on one line and its text in a box, with 取消 and 儲存", () => {
    const html = memoryHTML(view({ expanded: true, editing: { name: "user-city", body: "主人住在台北市。", draft: "主人住在<台北>。", description: "主人住在台北", descDraft: "主人住在&台北", saving: false, error: null } }));
    const title = '<input id="mem-title" class="mem-title" type="text" maxlength="80" aria-label="「主人住在台北」的標題" value="主人住在&amp;台北">';
    const text = '<textarea id="mem-body" class="mem-body" rows="5" maxlength="1200" aria-label="「主人住在台北」的內容">主人住在&lt;台北&gt;。</textarea>';
    expect(html).toContain(title + text);
    expect(html).toContain('data-mem="edit" data-name="user-city" aria-expanded="true"');
    expect(html).toContain('data-mem="cancel"');
    expect(html).toContain('data-mem="save"');
  });

  test("a note waiting out its 復原 is not drawn", () => {
    const html = memoryHTML(view({ expanded: true, hidden: ["user-city"] }));
    expect(html).not.toContain("主人住在台北");
    expect(visible(html).startsWith("記憶 1 ")).toBe(true);
  });

  test("全部清除 and the summary's 清除 ask for a second press", () => {
    const html = memoryHTML(view({ expanded: true, confirmingClear: true, confirmingSummary: true }));
    expect(html).toContain('class="btn danger sm" data-mem="clear"');
    expect(html).toContain('class="btn danger sm" data-mem="summary"');
    expect(visible(html)).toContain("確定全部清除？");
    expect(visible(html)).toContain("確定清除？");
  });

  test("names, types and text are escaped", () => {
    const html = memoryHTML(view({ expanded: true, status: { memories: [{ ...LIST.memories[0], description: '<b>"x"</b>' }], summary: null } }));
    expect(html).not.toContain("<b>");
    expect(html).toContain("&lt;b&gt;&quot;x&quot;&lt;/b&gt;");
  });

  test("one language at a time", () => {
    setLocale("en");
    expect(visible(memoryHTML(view({ expanded: true })))).toBe("Memory 2 About you 主人住在台北 Preference 回答保持兩三句 Inferred Conversation summary Clear Clear all");
    setLocale("ja");
    expect(visible(memoryHTML(view({ expanded: true })))).toBe("記憶 2 本人 主人住在台北 好み 回答保持兩三句 推測 会話の要約 消去 すべて消去");
  });

  test("while it loads, and when it cannot be read", () => {
    expect(memoryHTML(view({ status: "loading" }))).toContain('role="status"');
    expect(memoryHTML(view({ status: "failed" }))).toContain('role="alert"');
  });
});


describe("the controller", () => {
  let redraws = 0;
  let begin: ReturnType<typeof vi.fn<(action: UndoableAction) => void>>;
  const make = () => createMemory({ visible: () => true, redraw: () => void redraws++, undo: { begin } });
  const click = (c: ReturnType<typeof make>, mem: string, name = "") => {
    const b = { dataset: { mem, name } } as unknown as HTMLElement;
    return c.click({ closest: () => b } as unknown as Element);
  };
  const box = (value: string) => ({ id: "mem-body", value, closest: () => null }) as unknown as HTMLTextAreaElement;
  const line = (value: string) => ({ id: "mem-title", value, closest: () => null }) as unknown as HTMLInputElement;
  const keyOn = (key: string, extra: Partial<KeyboardEvent> = {}) =>
    ({ key, target: { id: "mem-body" }, preventDefault: vi.fn(), stopPropagation: vi.fn(), ctrlKey: false, metaKey: false, ...extra }) as unknown as KeyboardEvent;

  beforeEach(() => {
    redraws = 0;
    begin = vi.fn<(action: UndoableAction) => void>();
    vi.clearAllMocks();
    api.core.mockImplementation(async (method: string, params?: { name?: string }) => {
      if (method === "memory.list") return structuredClone(LIST);
      if (method === "memory.get") return { ...LIST.memories.find((m) => m.name === params?.name), body: "主人住在台北市。", turns: [] };
      return {};
    });
  });

  test("it reads the list, and a list it cannot read is shown as failed", async () => {
    const c = make();
    await c.load();
    expect(api.core).toHaveBeenCalledWith("memory.list");
    expect((c.view().status as MemoryList).memories).toHaveLength(2);
    expect(redraws).toBeGreaterThan(0);
    api.core.mockRejectedValue("down");
    const d = make();
    await d.load();
    expect(d.view().status).toBe("failed");
  });

  test("the chevron opens and closes the list", async () => {
    const c = make();
    await c.load();
    expect(click(c, "expand")).toBe(true);
    expect(c.view().expanded).toBe(true);
    click(c, "expand");
    expect(c.view().expanded).toBe(false);
  });

  test("the pencil reads the note; 儲存 sends what was typed and reads the list again", async () => {
    const c = make();
    await c.load();
    click(c, "edit", "user-city");
    await vi.waitFor(() => expect(c.view().editing?.body).toBe("主人住在台北市。"));
    expect(api.core).toHaveBeenCalledWith("memory.get", { name: "user-city" });
    expect(c.input(box("主人住在台北市大安區。"))).toBe(true);
    click(c, "save");
    await vi.waitFor(() => expect(c.view().editing).toBeNull());
    expect(api.core).toHaveBeenCalledWith("memory.update", { name: "user-city", body: "主人住在台北市大安區。" });
    expect(toaster.info).toHaveBeenCalledWith("已儲存記憶");
    await vi.waitFor(() => expect(api.core.mock.calls.filter(([m]) => m === "memory.list")).toHaveLength(2));
  });

  test("the title is read with the text, and 儲存 sends it when it was changed", async () => {
    const c = make();
    await c.load();
    click(c, "edit", "user-city");
    await vi.waitFor(() => expect(c.view().editing?.descDraft).toBe("主人住在台北"));
    expect(c.input(line("  主人住在高雄 "))).toBe(true);
    c.input(box("主人住在高雄市。"));
    click(c, "save");
    await vi.waitFor(() => expect(c.view().editing).toBeNull());
    expect(api.core).toHaveBeenCalledWith("memory.update", { name: "user-city", body: "主人住在高雄市。", description: "主人住在高雄" });
  });

  test("a title changed alone is sent with the text as it was", async () => {
    const c = make();
    await c.load();
    click(c, "edit", "user-city");
    await vi.waitFor(() => expect(c.view().editing?.body).not.toBeNull());
    c.input(line("主人住在高雄"));
    click(c, "save");
    await vi.waitFor(() => expect(c.view().editing).toBeNull());
    expect(api.core).toHaveBeenCalledWith("memory.update", { name: "user-city", body: "主人住在台北市。", description: "主人住在高雄" });
  });

  test("an empty title is not sent, and says so", async () => {
    const c = make();
    await c.load();
    click(c, "edit", "user-city");
    await vi.waitFor(() => expect(c.view().editing?.body).not.toBeNull());
    c.input(line("   "));
    click(c, "save");
    expect(c.view().editing?.error).toBe("標題不能空白");
    expect(api.core).not.toHaveBeenCalledWith("memory.update", expect.anything());
  });

  test("Enter in the title saves, and Escape there closes the note", async () => {
    const c = make();
    await c.load();
    click(c, "edit", "user-city");
    await vi.waitFor(() => expect(c.view().editing?.body).not.toBeNull());
    expect(c.key(keyOn("Escape", { target: { id: "mem-title" } as unknown as EventTarget }))).toBe(true);
    expect(c.view().editing).toBeNull();
    click(c, "edit", "user-city");
    await vi.waitFor(() => expect(c.view().editing?.body).not.toBeNull());
    c.input(line("主人住在高雄"));
    expect(c.key(keyOn("Enter", { target: { id: "mem-title" } as unknown as EventTarget }))).toBe(true);
    await vi.waitFor(() => expect(api.core).toHaveBeenCalledWith("memory.update", expect.objectContaining({ description: "主人住在高雄" })));
  });

  test("儲存 with nothing changed closes the note and sends nothing", async () => {
    const c = make();
    await c.load();
    click(c, "edit", "user-city");
    await vi.waitFor(() => expect(c.view().editing?.body).not.toBeNull());
    click(c, "save");
    expect(c.view().editing).toBeNull();
    expect(api.core).not.toHaveBeenCalledWith("memory.update", expect.anything());
  });

  test("the pencil of the note that is open closes it", async () => {
    const c = make();
    await c.load();
    click(c, "edit", "user-city");
    await vi.waitFor(() => expect(c.view().editing?.body).not.toBeNull());
    click(c, "edit", "user-city");
    expect(c.view().editing).toBeNull();
  });

  test("a refused edit stays open with the reason, which typing takes away", async () => {
    api.core.mockImplementation(async (method: string) => {
      if (method === "memory.list") return structuredClone(LIST);
      if (method === "memory.get") return { ...LIST.memories[0], body: "x", turns: [] };
      if (method === "memory.update") throw "secret: A memory must never hold a password";
      return {};
    });
    const c = make();
    await c.load();
    click(c, "edit", "user-city");
    await vi.waitFor(() => expect(c.view().editing?.body).toBe("x"));
    c.input(box("密碼是 hunter22"));
    click(c, "save");
    await vi.waitFor(() => expect(c.view().editing?.error).toBeTruthy());
    expect(c.view().editing?.draft).toBe("密碼是 hunter22");
    c.input(box("密碼"));
    expect(c.view().editing?.error).toBeNull();
  });

  test("Escape in the box closes it without saving; Ctrl+Enter saves", async () => {
    const c = make();
    await c.load();
    click(c, "edit", "user-city");
    await vi.waitFor(() => expect(c.view().editing?.body).not.toBeNull());
    const escape = keyOn("Escape");
    expect(c.key(escape)).toBe(true);
    expect(escape.stopPropagation).toHaveBeenCalled(); // the panel stays open
    expect(c.view().editing).toBeNull();
    click(c, "edit", "user-city");
    await vi.waitFor(() => expect(c.view().editing?.body).not.toBeNull());
    c.input(box("新的"));
    expect(c.key(keyOn("Enter", { ctrlKey: true }))).toBe(true);
    await vi.waitFor(() => expect(api.core).toHaveBeenCalledWith("memory.update", { name: "user-city", body: "新的" }));
    expect(c.key({ key: "Escape", target: { id: "other" } } as unknown as KeyboardEvent)).toBe(false);
  });

  test("the trash can hides the note at once and deletes it only when the 10 s of 復原 are up", async () => {
    const c = make();
    await c.load();
    click(c, "delete", "user-city");
    expect(c.view().hidden).toEqual(["user-city"]);
    expect(api.core).not.toHaveBeenCalledWith("memory.delete", expect.anything());
    const first = begin.mock.calls[0][0];
    expect(first.message).toBe("已刪除 1 條記憶");
    first.restore();
    expect(c.view().hidden).toEqual([]);
    click(c, "delete", "user-city");
    await begin.mock.calls[1][0].commit();
    expect(api.core).toHaveBeenCalledWith("memory.delete", { name: "user-city" });
    expect(c.view().hidden).toEqual([]);
    expect((c.view().status as MemoryList).memories.map((m) => m.name)).toEqual(["preference-short"]);
  });

  test("全部清除 clears only on the second press, and the first is forgotten after a while", async () => {
    const c = make();
    await c.load();
    vi.useFakeTimers();
    try {
      click(c, "clear");
      expect(c.view().confirmingClear).toBe(true);
      vi.advanceTimersByTime(5000);
      expect(c.view().confirmingClear).toBe(false);
      click(c, "clear");
      expect(api.core).not.toHaveBeenCalledWith("memory.clear");
      click(c, "clear");
      expect(api.core).toHaveBeenCalledWith("memory.clear");
    } finally {
      vi.useRealTimers();
    }
    await vi.waitFor(() => expect(toaster.info).toHaveBeenCalledWith("已清除全部記憶"));
  });

  test("the summary's 清除 clears it on the second press", async () => {
    const c = make();
    await c.load();
    click(c, "summary");
    expect(api.core).not.toHaveBeenCalledWith("memory.summary_clear");
    click(c, "summary");
    expect(api.core).toHaveBeenCalledWith("memory.summary_clear");
    await vi.waitFor(() => expect(toaster.info).toHaveBeenCalledWith("已清除對話摘要"));
  });

  test("a click elsewhere is not the memory's, and leaving the tab closes everything", async () => {
    const c = make();
    await c.load();
    expect(c.click({ closest: () => null } as unknown as Element)).toBe(false);
    click(c, "expand");
    click(c, "clear");
    c.reset();
    expect(c.view()).toMatchObject({ expanded: false, editing: null, confirmingClear: false });
  });
});


test("reset invalidates a memory list still loading for the previous member", async () => {
  let resolve!: (value: MemoryList) => void;
  api.core.mockReturnValueOnce(new Promise((done) => { resolve = done; }));
  const memory = createMemory({ visible: () => true, redraw: vi.fn(), undo: { begin: vi.fn() } });
  const old = memory.load();
  memory.reset();
  resolve(LIST);
  await old;
  expect(memory.view().status).toBe("loading");
});
