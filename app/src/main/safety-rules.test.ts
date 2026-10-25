import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { setLocale } from "./i18n";

const api = vi.hoisted(() => ({ core: vi.fn() }));
vi.mock("./api", () => api);

import { type RulesView, type SafetyRule, createRules, emptyRulesView, ruleProblem, rulesHTML, verdictChoices } from "./safety-rules";
import { searchSettings } from "./pages/settings-view";
import type { UndoableAction } from "./undo";

afterEach(() => setLocale("zh-TW"));

const RULES: SafetyRule[] = [
  { id: 1, action: "付款結帳", verdict: "ask", position: 1 },
  { id: 2, action: "刪除檔案", verdict: "ask", position: 2 },
  { id: 3, action: "寄信或傳訊息給別人", verdict: "ask", position: 3 },
  { id: 4, action: "在 Notion 新增頁面", verdict: "allow", position: 4 },
  { id: 5, action: "在社群網站發文", verdict: "deny", position: 5 },
];
const view = (over: Partial<RulesView> = {}): RulesView => ({ ...emptyRulesView(), status: RULES, ...over });
const visible = (html: string): string => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
const buttons = (html: string): string[] => [...html.matchAll(/<button\b[^>]*>/g)].map((m) => m[0]);

describe("the safety rules section", () => {
  test("it is the line that adds a rule, then the rules under 當拉斐爾想要 and 他會", () => {
    const html = rulesHTML(view());
    expect(html).toMatch(/^<section class="sgroup rules" data-rules-section aria-label="安全規則"><h2>安全規則<\/h2>/);
    expect(html).toContain('placeholder="當拉斐爾想要…（例如：在網路上留言）"');
    expect(visible(html)).toBe(
      "安全規則 先問我 新增 當拉斐爾想要 他會 付款結帳 先問我 刪除檔案 先問我 寄信或傳訊息給別人 先問我 在 Notion 新增頁面 直接做 在社群網站發文 不准做",
    );
    expect(html.match(/class="rules-row"/g)).toHaveLength(5);
  });

  test("the verdict is a dropdown of 先問我, 直接做 and 不准做, never a switch", () => {
    const html = rulesHTML(view());
    expect(html).toContain('class="sel" aria-haspopup="menu"');
    expect(html).toContain('data-k="rule-new-verdict"');
    expect(verdictChoices().map((c) => c.label)).toEqual(["先問我", "直接做", "不准做"]);
    expect(html).not.toContain('role="switch"');
  });

  test("the add action is framed and each rule has one named overflow button", () => {
    const html = rulesHTML(view());
    const tags = buttons(html);
    expect(tags.find((b) => b.includes('data-rule="add"'))).toMatch(/^<button type="button" class="btn pri" data-rule="add"/);
    const icons = tags.filter((b) => /class="btn icon/.test(b));
    expect(icons).toHaveLength(5);
    for (const tag of icons) expect(tag).toMatch(/aria-label="[^"]+" title="[^"]+"/);
    expect(html).toContain('aria-label="「刪除檔案」的選項" title="「刪除檔案」的選項"');
    expect(html).not.toMatch(/data-rule="(?:edit|delete)"/);
  });

  test("a rule being changed is its words in a field and its dropdown, with 取消 and 儲存, in its own row", () => {
    const html = rulesHTML(view({ editing: { id: 2, action: "刪除<檔案>", verdict: "deny", error: null, busy: false } }));
    expect(html).toContain('<div class="rules-row is-editing" data-rule-row="2">');
    expect(html).toContain('id="rule-edit" type="text" maxlength="80" value="刪除&lt;檔案&gt;"');
    expect(html).toContain('data-k="rule-edit-verdict"');
    expect(html).toContain('data-rule="cancel"');
    expect(html).toContain('data-rule="save"');
    expect(html.match(/class="rules-row"/g)).toHaveLength(4);
  });

  test("what is wrong with a rule is said under it", () => {
    const html = rulesHTML(view({ draft: { action: "", verdict: "ask", error: "empty", busy: false } }));
    expect(html).toContain('role="alert"');
    expect(visible(html)).toContain("請寫下拉斐爾想要做的事");
    expect(ruleProblem("  ", RULES)).toBe("empty");
    expect(ruleProblem("x".repeat(81), RULES)).toBe("too_long");
    expect(ruleProblem("付款結帳 ", RULES)).toBe("duplicate");
    expect(ruleProblem("付款結帳", RULES, 1)).toBeNull();
    expect(ruleProblem("在網路上留言", RULES)).toBeNull();
  });

  test("a rule waiting out its 復原 is not drawn, and with none it says so", () => {
    expect(rulesHTML(view({ hidden: [1] }))).not.toContain("付款結帳");
    expect(visible(rulesHTML(view({ status: [] })))).toBe("安全規則 先問我 新增 沒有規則");
  });

  test("the owner's words are escaped", () => {
    const html = rulesHTML(view({ status: [{ id: 9, action: '<img src=x onerror="1">', verdict: "ask", position: 1 }] }));
    expect(html).not.toContain("<img src=x");
    expect(html).toContain("&lt;img src=x onerror=&quot;1&quot;&gt;");
  });

  test("one language at a time", () => {
    setLocale("en");
    const html = rulesHTML(view({ status: RULES.slice(4) }));
    expect(visible(html)).toBe("Safety rules Ask me first Add When Raphael wants to He will 在社群網站發文 Never do it");
    setLocale("ja");
    expect(visible(rulesHTML(view({ status: [] })))).toBe("安全ルール 先に聞く 追加 ルールはありません");
  });

  test("while it loads, and when it cannot be read", () => {
    expect(rulesHTML(view({ status: "loading" }))).toContain('role="status"');
    expect(rulesHTML(view({ status: "failed" }))).toContain('role="alert"');
  });

  test("it is in Settings › 拉斐爾, after the memory, and the search finds it", () => {
    const hits = searchSettings("安全規則");
    expect(hits.some((h) => h.pane === "agent")).toBe(true);
  });
});

describe("the controller", () => {
  let redraws = 0;
  let begin: ReturnType<typeof vi.fn<(action: UndoableAction) => void>>;
  const make = () => createRules({ visible: () => true, redraw: () => void redraws++, undo: { begin } });
  const click = (c: ReturnType<typeof make>, rule: string, id = "") => {
    const b = { dataset: { rule, id } } as unknown as HTMLElement;
    return c.click({ closest: () => b } as unknown as Element);
  };
  const field = (id: string, value: string) => ({ id, value, closest: () => null }) as unknown as HTMLInputElement;
  const keyOn = (id: string, key: string) =>
    ({ key, target: { id }, isComposing: false, preventDefault: vi.fn(), stopPropagation: vi.fn() }) as unknown as KeyboardEvent;

  beforeEach(() => {
    redraws = 0;
    begin = vi.fn<(action: UndoableAction) => void>();
    vi.clearAllMocks();
    api.core.mockImplementation(async (method: string) => {
      if (method === "safety.rules.list") return { rules: structuredClone(RULES) };
      return {};
    });
  });

  test("it reads the rules in the window's language, and a list it cannot read is shown as failed", async () => {
    setLocale("ja");
    const c = make();
    await c.load();
    expect(api.core).toHaveBeenCalledWith("safety.rules.list", { ui_language: "ja" });
    expect(c.view().status).toHaveLength(5);
    api.core.mockRejectedValue("down");
    const d = make();
    await d.load();
    expect(d.view().status).toBe("failed");
  });

  test("新增 sends the words and the verdict chosen, then reads the list again", async () => {
    const c = make();
    await c.load();
    expect(c.input(field("rule-new", "  在網路上\n留言 "))).toBe(true);
    c.menuFor("rule-new-verdict")!.pick("deny");
    expect(c.view().draft.verdict).toBe("deny");
    click(c, "add");
    await vi.waitFor(() => expect(api.core).toHaveBeenCalledWith("safety.rules.create", { action: "在網路上 留言", verdict: "deny" }));
    await vi.waitFor(() => expect(c.view().draft.action).toBe(""));
    expect(api.core.mock.calls.filter(([m]) => m === "safety.rules.list")).toHaveLength(2);
  });

  test("Enter in the field adds too; an empty or a known rule is not sent and says why", async () => {
    const c = make();
    await c.load();
    c.input(field("rule-new", "   "));
    expect(c.key(keyOn("rule-new", "Enter"))).toBe(true);
    await vi.waitFor(() => expect(c.view().draft.error).toBe("empty"));
    c.input(field("rule-new", "刪除檔案"));
    click(c, "add");
    await vi.waitFor(() => expect(c.view().draft.error).toBe("duplicate"));
    expect(api.core).not.toHaveBeenCalledWith("safety.rules.create", expect.anything());
  });

  test("a refusal of the core is said under the field", async () => {
    const c = make();
    await c.load();
    api.core.mockImplementation(async (method: string) => {
      if (method === "safety.rules.create") throw "[-32602] Invalid params: there are too many rules";
      return { rules: RULES };
    });
    c.input(field("rule-new", "新的規則"));
    click(c, "add");
    await vi.waitFor(() => expect(c.view().draft.error).toBe("too_many"));
    expect(c.view().draft.action).toBe("新的規則");
  });

  test("the pencil opens the rule in place; 儲存 sends only what changed", async () => {
    const c = make();
    await c.load();
    click(c, "edit", "2");
    expect(c.view().editing).toMatchObject({ id: 2, action: "刪除檔案", verdict: "ask" });
    c.menuFor("rule-edit-verdict")!.pick("deny");
    click(c, "save");
    await vi.waitFor(() => expect(api.core).toHaveBeenCalledWith("safety.rules.update", { id: 2, verdict: "deny" }));
    await vi.waitFor(() => expect(c.view().editing).toBeNull());
    click(c, "edit", "3");
    c.input(field("rule-edit", "寄信給別人"));
    expect(c.key(keyOn("rule-edit", "Enter"))).toBe(true);
    await vi.waitFor(() => expect(api.core).toHaveBeenCalledWith("safety.rules.update", { id: 3, action: "寄信給別人" }));
  });

  test("取消 and Escape close the rule without sending; nothing changed sends nothing", async () => {
    const c = make();
    await c.load();
    click(c, "edit", "1");
    click(c, "cancel");
    expect(c.view().editing).toBeNull();
    click(c, "edit", "1");
    expect(c.key(keyOn("rule-edit", "Escape"))).toBe(true);
    expect(c.view().editing).toBeNull();
    click(c, "edit", "1");
    click(c, "save");
    expect(c.view().editing).toBeNull();
    expect(api.core).not.toHaveBeenCalledWith("safety.rules.update", expect.anything());
  });

  test("the trash can hides the rule for its 復原 and deletes it when the time is up", async () => {
    const c = make();
    await c.load();
    click(c, "delete", "4");
    expect(c.view().hidden).toEqual([4]);
    const action = begin.mock.calls[0][0];
    expect(action.message).toBe("已刪除 1 條規則");
    await action.commit();
    expect(api.core).toHaveBeenCalledWith("safety.rules.delete", { id: 4 });
    expect((c.view().status as SafetyRule[]).map((r) => r.id)).toEqual([1, 2, 3, 5]);
    click(c, "delete", "5");
    begin.mock.calls[1][0].restore();
    expect(c.view().hidden).toEqual([]);
  });
});
