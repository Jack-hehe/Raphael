import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { setLocale } from "./i18n";
import type { AuditEntry, ConnectionGroup } from "./types";

const api = vi.hoisted(() => ({
  toolsetsList: vi.fn(),
  integrationsAudit: vi.fn(),
  integrationsAdd: vi.fn(),
  integrationsRefresh: vi.fn(),
  integrationsSetEnabled: vi.fn(),
  integrationsRemove: vi.fn(),
  integrationsSetTier: vi.fn(),
  integrationsSetGroupTier: vi.fn(),
}));
vi.mock("./api", () => api);
vi.mock("./ui", async (original) => ({ ...(await original<typeof import("./ui")>()), toaster: { info: vi.fn() } }));

import { type ConnectionsView, connectionsHTML, createConnections, emptyConnectionsView } from "./connections";

afterEach(() => setLocale("zh-TW"));

const NOW = Date.UTC(2026, 9, 21, 6, 0);
const CJK = /[㐀-鿿぀-ヿ]/;

const builtin = (name: "web" | "computer", tools: [string, "T0" | "T1" | "T2"][]): ConnectionGroup => ({
  id: `builtin:${name}`, kind: "builtin", name, enabled: true, switchable: false, tier_fixed: true, status: "builtin", error: null,
  tools: tools.map(([tool, tier]) => ({ name: tool, description: "", tier, changed: false, suggested: null })),
});
const notes: ConnectionGroup = {
  id: "ab12", kind: "mcp", name: "Notes", enabled: true, switchable: true, tier_fixed: false, status: "ready", error: null,
  transport: "stdio", command: "npx", env: ["API_KEY"], headers: [], tool_count: 3, allowed_count: 1, checked_at_ms: NOW,
  tools: [
    { name: "look", description: "Look something up.", tier: null, changed: false, suggested: "T0" },
    { name: "note", description: "Write a note.", tier: "T2", changed: false, suggested: null },
    { name: "wipe", description: "Delete everything.", tier: null, changed: true, suggested: null },
  ],
};
const GROUPS: ConnectionGroup[] = [
  builtin("web", [["weather", "T0"], ["web_search", "T0"], ["local_time", "T0"]]),
  builtin("computer", [["open_youtube", "T1"], ["copy_text", "T2"]]),
  notes,
];
const AUDIT: AuditEntry[] = [{ id: 1, at_ms: NOW - 60_000, server: "Notes", tool: "note", tier: "T2", approver: "user", outcome: "ok" }];

const view = (over: Partial<ConnectionsView> = {}): ConnectionsView => ({
  ...emptyConnectionsView(), groups: GROUPS, audit: AUDIT, now: NOW, expanded: new Set(["ab12", "builtin:web"]), ...over,
});

describe("the connections tab", () => {
  test("has a card for each group: Raphael's two are only shown, a program's has a switch and its buttons", () => {
    const html = connectionsHTML(view());
    expect(html.match(/class="conn-group"/g)).toHaveLength(3);
    expect(html).toContain("網路查詢");
    expect(html).toContain("這台電腦");
    // the built-in cards: no switch, no tier dropdown, no 「全部設為」
    const cards = html.split('<section class="conn-group"').slice(1);
    for (const card of cards.slice(0, 2)) {
      expect(card).not.toContain('role="switch"');
      expect(card).not.toContain("conn-tier:");
      expect(card).not.toContain("conn-all:");
    }
    // the program's card
    const card = cards[2];
    expect(card).toContain('role="switch"');
    expect(card).toContain('data-k="conn-all:ab12"');
    expect(card).toContain("全部設為…");
    expect(card).toContain("重新讀取工具");
    expect(card).toContain("移除");
    expect(card).toContain("已連線，3 個工具，1 個已開放");
  });

  test("lists a tool per row when open, each with a tier dropdown, blocked until a tier is given", () => {
    const card = connectionsHTML(view()).split('<section class="conn-group"')[3];
    expect(card.match(/data-k="conn-tier:ab12:[a-z]+"/g)).toEqual([
      'data-k="conn-tier:ab12:look"', 'data-k="conn-tier:ab12:note"', 'data-k="conn-tier:ab12:wipe"',
    ]);
    expect(card).toContain("未指定（封鎖）");
    expect(card).toContain("T2 · 先問我");
    // the read-only claim of the server is a hint on the tool that has no T0, nothing more
    expect(card.match(/建議 T0/g)).toHaveLength(1);
    expect(card).toContain("內容變了，請重新確認");
    // closed: no tool rows
    const closed = connectionsHTML(view({ expanded: new Set() }));
    expect(closed).not.toContain("conn-tier:");
    expect(closed).toContain("顯示工具（3）");
  });

  test("shows Raphael's own tools by name in the language of the window, with fixed tiers", () => {
    const html = connectionsHTML(view({ expanded: new Set(["builtin:web", "builtin:computer"]) }));
    expect(html).toContain("天氣");
    expect(html).toContain("開啟 YouTube");
    expect(html).toContain("T1 · 小動作");
    expect(html).not.toContain("web_search");
    setLocale("en");
    const en = connectionsHTML(view({ expanded: new Set(["builtin:web"]) }));
    expect(en).toContain("Web search");
    expect(en).toContain("T0 · Read only");
  });

  test("everything that does something is a button or a switch, and every choice is a dropdown", () => {
    const html = connectionsHTML(view({ form: { type: "stdio", name: "", command: "", args: "", url: "", pairs: [{ name: "TOKEN", value: "x" }], pairName: "", pairValue: "", busy: false, error: null } }));
    const acts = [...html.matchAll(/<(\w+)[^>]*data-conn="/g)].map((m) => m[1]);
    expect(acts.length).toBeGreaterThan(6);
    expect(new Set(acts)).toEqual(new Set(["button"]));
    expect(html).not.toMatch(/<a[ >]/);
    expect(html).not.toContain("<select");
    expect(html).not.toContain('type="radio"');
    expect(html).toContain('data-k="conn-type"');
  });

  test("asks twice before it removes", () => {
    expect(connectionsHTML(view())).not.toContain("確定移除？");
    expect(connectionsHTML(view({ confirmingRemoval: "ab12" }))).toContain("確定移除？");
  });

  test("the form: a type dropdown, fields for that type, and the values of secrets only in password fields", () => {
    const form = { type: "http", name: "Remote", command: "", args: "", url: "https://example.com/mcp", pairs: [{ name: "Authorization", value: "Bearer TOPSECRET" }], pairName: "X-Key", pairValue: "ALSOSECRET", busy: false, error: null } as const;
    const html = connectionsHTML(view({ form: { ...form, pairs: [...form.pairs] } }));
    expect(html).toContain("網址");
    expect(html).toContain('id="conn-url"');
    expect(html).not.toContain('id="conn-command"');
    expect(html).toContain("Authorization");
    expect(html).toContain("標頭名稱");
    expect(html).not.toContain("TOPSECRET");
    // a value in the field being typed is kept in a password field, which the page does not read aloud
    const valueField = /<input[^>]*id="conn-pair-value"[^>]*>/.exec(html)![0];
    expect(valueField).toContain('type="password"');
    const stdio = connectionsHTML(view({ form: { ...form, type: "stdio", pairs: [] } }));
    expect(stdio).toContain('id="conn-command"');
    expect(stdio).toContain("環境變數");
    expect(stdio).not.toContain('id="conn-url"');
  });

  test("the record of actions has the time, the program, the tool, the tier and the result, and the tiers are explained", () => {
    const html = connectionsHTML(view());
    expect(html).toContain("最近的動作");
    expect(html).toContain("Notes / note");
    expect(html).toContain("T2 · 你確認");
    expect(html).toContain("完成");
    expect(html).toContain("等級是什麼");
    for (const tier of ["T0 · 只讀取", "T1 · 小動作", "T2 · 先問我", "T3 · 按住確認"]) expect(html).toContain(tier);
    expect(connectionsHTML(view({ audit: [] }))).toContain("還沒有動作");
  });

  test("a program that is not connected yet, a failed one, and a core that does not answer", () => {
    expect(connectionsHTML(view({ groups: GROUPS.slice(0, 2) }))).toContain("還沒有連接任何程式");
    const failed = { ...notes, status: "error", error: "timeout" } as const;
    expect(connectionsHTML(view({ groups: [failed] }))).toContain("連線失敗：太久沒有回應");
    expect(connectionsHTML(view({ groups: "failed" }))).toContain("無法讀取連接");
  });

  test.each(["en", "ja"] as const)("is in one language at a time (%s)", (locale) => {
    setLocale(locale);
    const html = connectionsHTML(view({ expanded: new Set(["ab12", "builtin:web", "builtin:computer"]), confirmingRemoval: "ab12" }));
    for (const zh of ["全部設為", "重新讀取工具", "網路查詢", "這台電腦", "最近的動作", "未指定（封鎖）", "新增連接"]) expect(html).not.toContain(zh);
    if (locale === "en") expect(html).not.toMatch(CJK);
    else {
      expect(html).not.toMatch(/Set all|Read tools again|Recent actions|Not set/);
      expect(html).toContain("接続を追加");
    }
  });
});

describe("what the tab does", () => {
  const host = { visible: () => true, redraw: vi.fn() };
  const button = (conn: string, extra: Record<string, string> = {}): Element =>
    ({ closest: () => ({ dataset: { conn, ...extra } }) }) as unknown as Element;
  const input = (id: string, value: string): HTMLInputElement => ({ id, value }) as HTMLInputElement;
  const settle = (): Promise<void> => new Promise((resolve) => setTimeout(resolve, 0));

  beforeEach(() => {
    for (const fn of Object.values(api)) fn.mockReset();
    api.toolsetsList.mockResolvedValue({ groups: GROUPS });
    api.integrationsAudit.mockResolvedValue({ entries: AUDIT });
    for (const name of ["integrationsRefresh", "integrationsSetEnabled", "integrationsSetTier", "integrationsSetGroupTier"] as const) api[name].mockResolvedValue({});
    api.integrationsRemove.mockResolvedValue({ removed: true });
    host.redraw.mockReset();
  });

  test("reads the groups and the latest actions", async () => {
    const c = createConnections(host);
    expect(c.view().groups).toBe("loading");
    await c.load();
    expect(c.view().groups).toEqual(GROUPS);
    expect(c.view().audit).toEqual(AUDIT);
    expect(api.integrationsAudit).toHaveBeenCalled();
  });

  test("says the groups cannot be had when the first read fails, and keeps the old ones when a later one does", async () => {
    api.toolsetsList.mockRejectedValueOnce("down");
    const c = createConnections(host);
    await c.load();
    expect(c.view().groups).toBe("failed");
    await c.load();
    api.toolsetsList.mockRejectedValueOnce("down");
    await c.load();
    expect(c.view().groups).toEqual(GROUPS);
  });

  test("gives a tool a tier, and all the tools of a group one, from the dropdowns", async () => {
    const c = createConnections(host);
    await c.load();
    c.menuFor("conn-tier:ab12:look")!.pick("T0");
    await settle();
    expect(api.integrationsSetTier).toHaveBeenCalledWith("ab12", "look", "T0");
    c.menuFor("conn-tier:ab12:note")!.pick("");
    await settle();
    expect(api.integrationsSetTier).toHaveBeenCalledWith("ab12", "note", null);
    c.menuFor("conn-all:ab12")!.pick("T1");
    await settle();
    expect(api.integrationsSetGroupTier).toHaveBeenCalledWith("ab12", "T1");
    expect(c.menuFor("conn-tier:ab12:look")!.choices.map((x) => x.value)).toEqual(["", "T0", "T1", "T2", "T3"]);
    expect(c.menuFor("nothing")).toBeNull();
  });

  test("switches a program on or off", async () => {
    const c = createConnections(host);
    await c.load();
    expect(c.click(button("enable", { id: "ab12" }))).toBe(true);
    await settle();
    expect(api.integrationsSetEnabled).toHaveBeenCalledWith("ab12", false);
  });

  test("reads the tools again", async () => {
    const c = createConnections(host);
    await c.load();
    c.click(button("refresh", { id: "ab12" }));
    await settle();
    expect(api.integrationsRefresh).toHaveBeenCalledWith("ab12");
  });

  test("removes only on the second press", async () => {
    vi.useFakeTimers();
    try {
      const c = createConnections(host);
      await c.load();
      c.click(button("remove", { id: "ab12" }));
      expect(c.view().confirmingRemoval).toBe("ab12");
      expect(api.integrationsRemove).not.toHaveBeenCalled();
      vi.advanceTimersByTime(5100);
      expect(c.view().confirmingRemoval).toBeNull();
      c.click(button("remove", { id: "ab12" }));
      c.click(button("remove", { id: "ab12" }));
      await vi.advanceTimersByTimeAsync(0);
      expect(api.integrationsRemove).toHaveBeenCalledTimes(1);
      expect(api.integrationsRemove).toHaveBeenCalledWith("ab12");
    } finally {
      vi.useRealTimers();
    }
  });

  test("connects a program: the values go to the core once, the names stay, the form is dropped", async () => {
    api.integrationsAdd.mockResolvedValue({ server: { id: "new1" } });
    const c = createConnections(host);
    await c.load();
    c.click(button("add"));
    expect(c.view().form).not.toBeNull();
    c.input(input("conn-name", " Notes "));
    c.input(input("conn-command", "npx"));
    c.input(input("conn-args", "-y   notes-server"));
    c.input(input("conn-pair-name", "API_KEY"));
    c.input(input("conn-pair-value", "TOPSECRET"));
    c.click(button("pair-add"));
    expect(c.view().form!.pairs).toEqual([{ name: "API_KEY", value: "TOPSECRET" }]);
    expect(c.view().form!.pairValue).toBe("");
    c.click(button("connect"));
    await settle();
    expect(api.integrationsAdd).toHaveBeenCalledWith({
      name: "Notes", transport: "stdio", command: "npx", args: ["-y", "notes-server"], env: [{ name: "API_KEY", value: "TOPSECRET" }],
    });
    expect(api.integrationsRefresh).toHaveBeenCalledWith("new1");
    expect(c.view().form).toBeNull();
    expect(c.view().expanded.has("new1")).toBe(true);
    expect(JSON.stringify(c.view())).not.toContain("TOPSECRET");
  });

  test("an address with a header; a half-typed pair and missing fields are told before anything is sent", async () => {
    api.integrationsAdd.mockResolvedValue({ server: { id: "new2" } });
    const c = createConnections(host);
    await c.load();
    c.click(button("add"));
    c.click(button("connect"));
    expect(c.view().form!.error!()).toBe("請填名稱");
    c.input(input("conn-name", "Remote"));
    c.click(button("connect"));
    expect(c.view().form!.error!()).toBe("請填指令");
    c.menuFor("conn-type")!.pick("http");
    c.click(button("connect"));
    expect(c.view().form!.error!()).toBe("請填網址");
    c.input(input("conn-url", "https://example.com/mcp"));
    c.input(input("conn-pair-name", "Authorization"));
    c.click(button("connect"));
    expect(c.view().form!.error!()).toBe("請先按「加入」，或清掉這一列");
    c.input(input("conn-pair-value", "Bearer TOKEN"));
    c.click(button("connect")); // both parts are there: the pair counts
    await settle();
    expect(api.integrationsAdd).toHaveBeenCalledTimes(1);
    expect(api.integrationsAdd).toHaveBeenCalledWith({
      name: "Remote", transport: "http", url: "https://example.com/mcp", headers: [{ name: "Authorization", value: "Bearer TOKEN" }],
    });
  });

  test("a refused connection keeps the form and says why; cancelling drops what was typed", async () => {
    api.integrationsAdd.mockRejectedValue("[-32602] Invalid params: A plugin with this name exists already");
    const c = createConnections(host);
    await c.load();
    c.click(button("add"));
    c.input(input("conn-name", "Notes"));
    c.input(input("conn-command", "npx"));
    c.click(button("connect"));
    await settle();
    expect(c.view().form!.busy).toBe(false);
    expect(c.view().form!.error!()).toContain("exists already");
    c.click(button("form-cancel"));
    expect(c.view().form).toBeNull();
  });

  test("expands and folds a card", async () => {
    const c = createConnections(host);
    c.click(button("expand", { id: "ab12" }));
    expect(c.view().expanded.has("ab12")).toBe(true);
    c.click(button("expand", { id: "ab12" }));
    expect(c.view().expanded.has("ab12")).toBe(false);
  });

  test("ignores a click that is none of its own", () => {
    const c = createConnections(host);
    expect(c.click({ closest: () => null } as unknown as Element)).toBe(false);
  });
});
