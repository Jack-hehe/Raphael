import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { setLocale } from "./i18n";
import type { AuditEntry, ConnectionGroup, ToolTier } from "./types";

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

import { type ConnectionForm, type ConnectionsView, connectionsHTML, createConnections, emptyConnectionsView, tierChoices } from "./connections";

afterEach(() => setLocale("zh-TW"));

const NOW = Date.UTC(2026, 9, 21, 6, 0);
const CJK = /[㐀-鿿぀-ヿ]/;

const builtin = (name: "web" | "computer", tools: [string, ToolTier][]): ConnectionGroup => ({
  id: `builtin:${name}`, kind: "builtin", name, enabled: true, switchable: false, tier_fixed: true, status: "builtin", error: null,
  tools: tools.map(([tool, tier]) => ({ name: tool, description: "", tier, decided: true, changed: false, suggested: null })),
});
const notes: ConnectionGroup = {
  id: "ab12", kind: "mcp", name: "Notes", enabled: true, switchable: true, tier_fixed: false, status: "ready", error: null,
  transport: "stdio", command: "npx", env: ["API_KEY"], headers: [], tool_count: 3, allowed_count: 1, pending_count: 2, checked_at_ms: NOW,
  tools: [
    { name: "look", description: "Look something up.", tier: null, decided: false, changed: false, suggested: "T0" },
    { name: "note", description: "Write a note.", tier: "T2", decided: true, changed: false, suggested: null },
    { name: "wipe", description: "Delete everything.", tier: null, decided: false, changed: true, suggested: null },
  ],
};
const GROUPS: ConnectionGroup[] = [
  builtin("web", [["weather", "T0"], ["web_search", "T0"], ["local_time", "T0"]]),
  builtin("computer", [["open_youtube", "T1"], ["copy_text", "T2"]]),
  notes,
];
const AUDIT: AuditEntry[] = [{ id: 1, at_ms: NOW - 60_000, server: "Notes", tool: "note", tier: "T2", approver: "user", outcome: "ok" }];

const view = (over: Partial<ConnectionsView> = {}): ConnectionsView => ({
  ...emptyConnectionsView(), groups: GROUPS, audit: AUDIT, now: NOW, ...over,
});
const form = (over: Partial<ConnectionForm> = {}): ConnectionForm => ({
  type: "stdio", name: "", command: "", args: "", url: "", pairs: [], pairName: "", pairValue: "", busy: false, error: null, ...over,
});

describe("the list on the left", () => {
  test("has the overview, Raphael's two groups, each program with a short status, and the button that adds one", () => {
    const failed: ConnectionGroup = { ...notes, id: "f1", name: "files", status: "error", error: "timeout", pending_count: 0 };
    const quiet: ConnectionGroup = { ...notes, id: "q1", name: "Quiet", pending_count: 0, tool_count: 2, allowed_count: 1 };
    const html = connectionsHTML(view({ groups: [...GROUPS, failed, quiet] }));
    const list = html.slice(html.indexOf('class="ca-list"'), html.indexOf('class="ca-detail"'));
    expect(list).toContain("總覽");
    expect(list).toContain("拉斐爾內建");
    expect(list).toContain("已連接的程式");
    expect(list).toContain("網路查詢");
    expect(list).toContain("內建 · 3 個工具");
    expect(list).toContain("這台電腦");
    expect(list).toContain("內建 · 2 個工具");
    expect(list).toContain("2 個新工具待決定"); // Notes
    expect(list).toContain("連線失敗"); // files
    expect(list).toContain("已連線 · 1/2 開放"); // Quiet
    expect(list).toContain("新增連接");
    expect(list.match(/data-conn="select"/g)).toHaveLength(1 + 2 + 1 + 2);
  });

  test("the items are real buttons that say which one is open", () => {
    const html = connectionsHTML(view({ selected: "ab12" }));
    const items = [...html.matchAll(/<(\w+)[^>]*class="ca-item[^"]*"[^>]*>/g)];
    expect(items.length).toBe(4);
    expect(new Set(items.map((m) => m[1]))).toEqual(new Set(["button"]));
    const open = items.filter((m) => m[0].includes('aria-current="true"'));
    expect(open).toHaveLength(1);
    expect(open[0][0]).toContain('data-id="ab12"');
    expect(html).not.toMatch(/<a[ >]/);
  });

  test("a core that does not answer, and the list while it is read", () => {
    expect(connectionsHTML(view({ groups: "failed" }))).toContain("無法讀取連接");
    expect(connectionsHTML(view({ groups: "loading" }))).toContain("讀取中");
    expect(connectionsHTML(view({ groups: GROUPS.slice(0, 2) }))).toContain("還沒有連接任何程式");
  });
});

describe("the overview", () => {
  test("says which program has tools to decide, with a button, then the permissions, then the latest actions", () => {
    const html = connectionsHTML(view());
    expect(html).toContain("<b>Notes</b>");
    expect(html).toContain("有 2 個新工具還沒給權限");
    expect(html).toContain('data-conn="review" data-id="ab12"');
    expect(html).toContain("權限怎麼分");
    for (const word of ["封鎖", "自動 · 只讀", "自動 · 小動作", "先問我", "按住確認"]) expect(html).toContain(word);
    expect(html).toContain("最近的動作");
    expect(html).toContain("Notes / note");
    expect(html).toContain("先問我 · T2 · 你確認");
    expect(connectionsHTML(view({ audit: [] }))).toContain("還沒有動作");
    expect(connectionsHTML(view({ groups: [builtin("web", [["weather", "T0"]])] }))).not.toContain("去決定");
  });
});

describe("a group of Raphael's own", () => {
  test("is only shown: no switch, no dropdown, a fixed permission with its T level", () => {
    const html = connectionsHTML(view({ selected: "builtin:computer" }));
    const detail = html.slice(html.indexOf('class="ca-detail"'));
    expect(detail).toContain("這台電腦");
    expect(detail).toContain("開啟 YouTube");
    expect(detail).toContain("自動 · 小動作");
    expect(detail).toContain("先問我");
    expect(detail).not.toContain('role="switch"');
    expect(detail).not.toContain("conn-tier:");
    expect(detail).not.toContain("conn-all:");
    expect(detail).not.toContain("open_youtube");
  });
});

describe("a connected program", () => {
  const detail = (over: Partial<ConnectionsView> = {}): string => {
    const html = connectionsHTML(view({ selected: "ab12", ...over }));
    return html.slice(html.indexOf('class="ca-detail"'));
  };

  test("has its head, where it is reached and the names of its secrets, never their values", () => {
    const html = detail();
    expect(html).toContain("<h2 class=\"big\">Notes</h2>");
    expect(html).toContain("已連線，3 個工具，1 個已開放");
    expect(html).toContain('role="switch"');
    expect(html).toContain("重新讀取工具");
    expect(html).toContain("移除");
    expect(html).toContain("本機程式");
    expect(html).toContain("npx");
    expect(html).toContain("API_KEY");
    expect(html).toContain("存在認證管理員");
    expect(html).toContain("有 2 個新工具還沒決定權限");
    expect(html).toContain("決定權限");
  });

  test("splits the tools into those that change things and those the server says only read, each with its own 「全部設為」", () => {
    const html = detail();
    const write = html.slice(html.indexOf("會改動東西"), html.indexOf("伺服器說只讀取"));
    const read = html.slice(html.indexOf("伺服器說只讀取"));
    expect(write).toContain(">note<");
    expect(write).toContain(">wipe<");
    expect(write).not.toContain(">look<");
    expect(read).toContain(">look<");
    expect(html).toContain('data-k="conn-all:ab12:write"');
    expect(html).toContain('data-k="conn-all:ab12:read"');
    expect(html.match(/全部設為…/g)!.length).toBeGreaterThanOrEqual(2);
    expect(html.match(/data-k="conn-tier:ab12:[a-z]+"/g)).toEqual([
      'data-k="conn-tier:ab12:note"', 'data-k="conn-tier:ab12:wipe"', 'data-k="conn-tier:ab12:look"',
    ]);
  });

  test("a new tool says so and counts as to decide; one the owner blocked does not", () => {
    const blocked: ConnectionGroup = { ...notes, tools: notes.tools.map((x) => (x.name === "wipe" ? { ...x, decided: true, changed: false } : x)) };
    const html = detail({ groups: [blocked] });
    const rowOf = (name: string): string => html.split('<div class="row conn-tool">').find((r) => r.includes(`>${name}<`)) ?? "";
    const look = rowOf("look");
    expect(look).toContain(">新<");
    expect(look).toContain("未指定"); // never decided: blocked, and it says so
    const wipe = rowOf("wipe");
    expect(wipe).not.toContain(">新<");
    expect(wipe).not.toContain("未指定"); // blocked on purpose
    expect(wipe).toContain("封鎖");
    // a tool that changed says so, and is not 「新」
    const changed = detail();
    expect(changed).toContain("內容變了，請重新確認");
    expect(changed.match(/>新</g)).toHaveLength(1);
  });

  test("the server's read-only claim is only a suggestion beside the tool", () => {
    expect(detail()).toContain("建議「自動 · 只讀」");
    expect(detail()).not.toContain("建議 T0");
  });

  test("the dropdown says the friendly name and keeps the T level beside it", () => {
    const html = detail();
    const note = html.slice(html.indexOf('data-k="conn-tier:ab12:note"'), html.indexOf('data-k="conn-tier:ab12:note"') + 300);
    expect(note).toContain('class="tname">先問我<');
    expect(note).toContain('class="ttag">T2<');
  });

  test("its menu lists the five words with the T levels, a line of what each does, and tags the suggested one", () => {
    const choices = tierChoices("T0");
    expect(choices.map((c) => c.value)).toEqual(["", "T0", "T1", "T2", "T3"]);
    expect(choices.map((c) => c.label)).toEqual(["封鎖", "自動 · 只讀", "自動 · 小動作", "先問我", "按住確認"]);
    expect(choices.map((c) => c.detail)).toEqual([undefined, "T0", "T1", "T2", "T3"]);
    expect(choices.every((c) => c.desc && !CJK.test("") && c.desc.length > 4)).toBe(true);
    expect(choices.filter((c) => c.tag).map((c) => c.value)).toEqual(["T0"]);
    expect(tierChoices().some((c) => c.tag)).toBe(false);
  });

  test("a program that failed, one that is off and one whose tools are not read say so", () => {
    expect(detail({ groups: [{ ...notes, status: "error", error: "timeout" }] })).toContain("連線失敗：太久沒有回應");
    expect(detail({ groups: [{ ...notes, status: "disabled", enabled: false }] })).toContain("已關閉");
    expect(detail({ groups: [{ ...notes, status: "unchecked", tools: [], tool_count: 0, pending_count: 0 }] })).toContain("還沒有讀取工具");
  });

  test("asks twice before it removes", () => {
    expect(detail()).not.toContain("確定移除？");
    expect(detail({ confirmingRemoval: "ab12" })).toContain("確定移除？");
  });

  test("an address is shown for a program reached over the network", () => {
    const remote: ConnectionGroup = { ...notes, transport: "http", url: "https://example.com/mcp", command: "", env: [], headers: ["Authorization"] };
    const html = detail({ groups: [remote] });
    expect(html).toContain("https://example.com/mcp");
    expect(html).toContain("Authorization");
  });
});

describe("the step that decides what the new tools may do", () => {
  const review = { id: "ab12", tiers: { look: "T0", wipe: null } as Record<string, ToolTier | null>, busy: false };
  const html = (over: Partial<ConnectionsView> = {}): string => {
    const all = connectionsHTML(view({ selected: "ab12", review, ...over }));
    return all.slice(all.indexOf('class="ca-detail"'));
  };

  test("lists only the tools to decide, with what is proposed, and waits for 「完成」", () => {
    const out = html();
    expect(out).toContain("決定權限");
    expect(out).toContain("已連上「Notes」，讀到 2 個工具");
    expect(out).toContain('data-k="conn-draft:ab12:look"');
    expect(out).toContain('data-k="conn-draft:ab12:wipe"');
    expect(out).not.toContain("conn-draft:ab12:note"); // decided already
    expect(out).not.toContain("conn-tier:");
    expect(out).toContain('data-conn="review-done"');
    expect(out).toContain('data-conn="review-later"');
    expect(out).toContain("完成");
    expect(out).toContain("稍後再說");
    expect(out).toContain("你按「完成」之前，拉斐爾不會用任何一個");
    const look = out.slice(out.indexOf('data-k="conn-draft:ab12:look"'), out.indexOf('data-k="conn-draft:ab12:look"') + 250);
    expect(look).toContain('class="ttag">T0<');
  });

  test("is not shown for another group", () => {
    const all = connectionsHTML(view({ selected: "builtin:web", review }));
    expect(all).not.toContain("conn-draft:");
  });
});

describe("the form that connects a program", () => {
  test("is on the right, not in a dialog: a type dropdown, fields for that type, a warning, two buttons", () => {
    const html = connectionsHTML(view({ selected: "add", form: form() }));
    const detail = html.slice(html.indexOf('class="ca-detail"'));
    expect(html).toContain('data-conn="add" aria-current="true"');
    expect(detail).toContain("新增連接");
    expect(detail).toContain('data-k="conn-type"');
    expect(detail).toContain('id="conn-command"');
    expect(detail).toContain("只接你信任的程式。");
    expect(detail).toContain('data-conn="connect"');
    expect(detail).toContain('data-conn="form-cancel"');
    expect(html).not.toContain('role="dialog"');
  });

  test("an address and a header for the network type; the values of secrets are never printed", () => {
    const f = form({ type: "http", name: "Remote", url: "https://example.com/mcp", pairs: [{ name: "Authorization", value: "Bearer TOPSECRET" }], pairName: "X-Key", pairValue: "ALSOSECRET" });
    const html = connectionsHTML(view({ selected: "add", form: f }));
    expect(html).toContain('id="conn-url"');
    expect(html).not.toContain('id="conn-command"');
    expect(html).toContain("Authorization");
    expect(html).toContain("標頭名稱");
    expect(html).not.toContain("TOPSECRET");
    const valueField = /<input[^>]*id="conn-pair-value"[^>]*>/.exec(html)![0];
    expect(valueField).toContain('type="password"');
    expect(valueField).not.toContain("ALSOSECRET=");
  });
});

describe("the owner's rules for this tab", () => {
  test("everything that does something is a button or a switch, and every choice is a dropdown", () => {
    const all = [
      connectionsHTML(view({ selected: "ab12" })),
      connectionsHTML(view({ selected: "add", form: form({ pairs: [{ name: "TOKEN", value: "x" }] }) })),
      connectionsHTML(view({ selected: "ab12", review: { id: "ab12", tiers: { look: "T0" }, busy: false } })),
      connectionsHTML(view()),
    ].join("");
    const acts = [...all.matchAll(/<(\w+)[^>]*data-conn="/g)].map((m) => m[1]);
    expect(acts.length).toBeGreaterThan(12);
    expect(new Set(acts)).toEqual(new Set(["button"]));
    expect(all).not.toMatch(/<a[ >]/);
    expect(all).not.toContain("<select");
    expect(all).not.toContain('type="radio"');
    expect(all).not.toContain("onclick");
  });

  test.each(["en", "ja"] as const)("is in one language at a time (%s)", (locale) => {
    setLocale(locale);
    const pages = [
      connectionsHTML(view()),
      connectionsHTML(view({ selected: "ab12", confirmingRemoval: "ab12" })),
      connectionsHTML(view({ selected: "builtin:web" })),
      connectionsHTML(view({ selected: "ab12", review: { id: "ab12", tiers: { look: "T0", wipe: null }, busy: false } })),
      connectionsHTML(view({ selected: "add", form: form() })),
    ].join("");
    for (const zh of ["全部設為", "重新讀取工具", "網路查詢", "這台電腦", "最近的動作", "封鎖", "新增連接", "決定權限", "先問我", "按住確認", "待決定", "會改動東西"]) expect(pages).not.toContain(zh);
    if (locale === "en") {
      expect(pages).not.toMatch(CJK);
      expect(pages).toContain("Ask me first");
      expect(pages).toContain("Decide permissions");
    } else {
      expect(pages).toContain("接続を追加");
      expect(pages).toContain("権限を決める");
      expect(pages).not.toMatch(/Set all|Read tools again|Recent actions|Add a connection|Ask me/);
    }
  });
});

describe("the marks of programs", () => {
  const server = (name: string, over: Partial<ConnectionGroup> = {}): ConnectionGroup => ({ ...notes, id: "x1", name, ...over });
  const mark = (g: ConnectionGroup): string => {
    const html = connectionsHTML(view({ groups: [g] }));
    const item = html.slice(html.indexOf('data-id="x1"'), html.indexOf("</button>", html.indexOf('data-id="x1"')));
    return item;
  };

  test("a well-known program has its mark in the colour of the text, found by name, package or address", () => {
    for (const g of [
      server("Notion"),
      server("Work notes", { command: "npx", args: ["-y", "@notionhq/notion-mcp-server"] }),
      server("Wiki", { transport: "http", url: "https://mcp.notion.com/mcp", command: "" }),
    ]) {
      const item = mark(g);
      expect(item).toContain('class="logo"');
      expect(item).toContain('fill="currentColor"');
      expect(item).toContain("logo-mono");
    }
  });

  test("an unknown program has a neutral letter, and a built-in group keeps its line icon", () => {
    const item = mark(server("filesystem", { command: "npx", args: ["-y", "@modelcontextprotocol/server-filesystem"] }));
    expect(item).not.toContain("<svg");
    expect(item).toContain(">F<");
    const html = connectionsHTML(view());
    expect(html).toContain('class="mono sm builtin"');
  });

  test("the name of a program is what picks the letter, even in other scripts", () => {
    expect(mark(server("筆記"))).toContain(">筆<");
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

  test("reads the groups and the latest actions, and starts at the overview", async () => {
    const c = createConnections(host);
    expect(c.view().groups).toBe("loading");
    await c.load();
    expect(c.view().groups).toEqual(GROUPS);
    expect(c.view().audit).toEqual(AUDIT);
    expect(c.view().selected).toBe("overview");
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

  test("shows the item that is pressed on the left, and the overview when the one shown is gone", async () => {
    const c = createConnections(host);
    await c.load();
    expect(c.click(button("select", { id: "ab12" }))).toBe(true);
    expect(c.view().selected).toBe("ab12");
    api.toolsetsList.mockResolvedValue({ groups: GROUPS.slice(0, 2) });
    await c.load();
    expect(c.view().selected).toBe("overview");
  });

  test("gives a tool a tier from its dropdown, and a block is a choice of its own", async () => {
    const c = createConnections(host);
    await c.load();
    c.menuFor("conn-tier:ab12:look")!.pick("T0");
    await settle();
    expect(api.integrationsSetTier).toHaveBeenCalledWith("ab12", "look", "T0");
    c.menuFor("conn-tier:ab12:note")!.pick("");
    await settle();
    expect(api.integrationsSetTier).toHaveBeenCalledWith("ab12", "note", null);
    // a tool nobody decided on, given the block: that decides it
    api.integrationsSetTier.mockClear();
    c.menuFor("conn-tier:ab12:look")!.pick("");
    await settle();
    expect(api.integrationsSetTier).toHaveBeenCalledWith("ab12", "look", null);
    // the same tier again, for a tool already decided, does nothing
    api.integrationsSetTier.mockClear();
    c.menuFor("conn-tier:ab12:note")!.pick("T2");
    await settle();
    expect(api.integrationsSetTier).not.toHaveBeenCalled();
    expect(c.menuFor("conn-tier:ab12:look")!.choices.map((x) => x.value)).toEqual(["", "T0", "T1", "T2", "T3"]);
    expect(c.menuFor("conn-tier:ab12:look")!.choices.find((x) => x.tag)?.value).toBe("T0");
    expect(c.menuFor("nothing")).toBeNull();
  });

  test("「全部設為」 gives one tier to the tools of its own list only", async () => {
    const c = createConnections(host);
    await c.load();
    c.menuFor("conn-all:ab12:write")!.pick("T2");
    await settle();
    expect(api.integrationsSetGroupTier).toHaveBeenLastCalledWith("ab12", "T2", ["note", "wipe"]);
    c.menuFor("conn-all:ab12:read")!.pick("T0");
    await settle();
    expect(api.integrationsSetGroupTier).toHaveBeenLastCalledWith("ab12", "T0", ["look"]);
    c.menuFor("conn-all:ab12:read")!.pick("");
    await settle();
    expect(api.integrationsSetGroupTier).toHaveBeenLastCalledWith("ab12", null, ["look"]);
    expect(c.menuFor("conn-all:nope:read")).toBeNull();
  });

  test("switches a program on or off, and reads its tools again", async () => {
    const c = createConnections(host);
    await c.load();
    c.click(button("enable", { id: "ab12" }));
    await settle();
    expect(api.integrationsSetEnabled).toHaveBeenCalledWith("ab12", false);
    c.click(button("refresh", { id: "ab12" }));
    await settle();
    expect(api.integrationsRefresh).toHaveBeenCalledWith("ab12");
  });

  test("removes only on the second press, and goes back to the overview", async () => {
    vi.useFakeTimers();
    try {
      const c = createConnections(host);
      await c.load();
      c.click(button("select", { id: "ab12" }));
      c.click(button("remove", { id: "ab12" }));
      expect(c.view().confirmingRemoval).toBe("ab12");
      expect(api.integrationsRemove).not.toHaveBeenCalled();
      vi.advanceTimersByTime(5100);
      expect(c.view().confirmingRemoval).toBeNull();
      api.toolsetsList.mockResolvedValue({ groups: GROUPS.slice(0, 2) });
      c.click(button("remove", { id: "ab12" }));
      c.click(button("remove", { id: "ab12" }));
      await vi.advanceTimersByTimeAsync(0);
      expect(api.integrationsRemove).toHaveBeenCalledTimes(1);
      expect(api.integrationsRemove).toHaveBeenCalledWith("ab12");
      expect(c.view().selected).toBe("overview");
    } finally {
      vi.useRealTimers();
    }
  });

  describe("connecting a program", () => {
    const fresh: ConnectionGroup = {
      ...notes, id: "new1", name: "Notes", pending_count: 3, allowed_count: 0,
      tools: [
        { name: "look", description: "Look.", tier: null, decided: false, changed: false, suggested: "T0" },
        { name: "list", description: "List.", tier: null, decided: false, changed: false, suggested: "T0" },
        { name: "wipe", description: "Delete.", tier: null, decided: false, changed: false, suggested: null },
      ],
    };

    async function connected() {
      api.integrationsAdd.mockResolvedValue({ server: { id: "new1" } });
      api.toolsetsList.mockResolvedValue({ groups: [...GROUPS.slice(0, 2), fresh] });
      const c = createConnections(host);
      await c.load();
      c.click(button("add"));
      c.input(input("conn-name", " Notes "));
      c.input(input("conn-command", "npx"));
      c.input(input("conn-args", "-y   notes-server"));
      c.input(input("conn-pair-name", "API_KEY"));
      c.input(input("conn-pair-value", "TOPSECRET"));
      c.click(button("pair-add"));
      c.click(button("connect"));
      await settle();
      return c;
    }

    test("the values go to the core once, the names stay, the form is dropped, and the new program is shown", async () => {
      api.integrationsAdd.mockResolvedValue({ server: { id: "new1" } });
      const c = createConnections(host);
      await c.load();
      c.click(button("add"));
      expect(c.view().form).not.toBeNull();
      expect(c.view().selected).toBe("add");
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
      expect(JSON.stringify(c.view())).not.toContain("TOPSECRET");
    });

    test("then asks what the new tools may do, with the server's suggestions as a proposal, and grants nothing yet", async () => {
      const c = await connected();
      expect(c.view().selected).toBe("new1");
      expect(c.view().review).toEqual({ id: "new1", tiers: { look: "T0", list: "T0", wipe: null }, busy: false });
      expect(api.integrationsSetTier).not.toHaveBeenCalled();
      expect(api.integrationsSetGroupTier).not.toHaveBeenCalled();
      expect(connectionsHTML(c.view())).toContain("決定權限");
    });

    test("「完成」 gives each tool what was chosen, in one call for each tier, the block included", async () => {
      const c = await connected();
      c.menuFor("conn-draft:new1:look")!.pick("T1");
      expect(c.view().review!.tiers.look).toBe("T1");
      expect(api.integrationsSetGroupTier).not.toHaveBeenCalled();
      api.toolsetsList.mockResolvedValue({ groups: [...GROUPS.slice(0, 2), { ...fresh, pending_count: 0, tools: fresh.tools.map((x) => ({ ...x, decided: true })) }] });
      c.click(button("review-done"));
      await settle();
      expect(api.integrationsSetGroupTier.mock.calls).toEqual([
        ["new1", "T1", ["look"]],
        ["new1", "T0", ["list"]],
        ["new1", null, ["wipe"]],
      ]);
      expect(c.view().review).toBeNull();
      expect(c.view().selected).toBe("new1");
    });

    test("「稍後再說」 leaves every tool as it was, still to decide", async () => {
      const c = await connected();
      c.click(button("review-later"));
      expect(c.view().review).toBeNull();
      expect(api.integrationsSetGroupTier).not.toHaveBeenCalled();
      expect(api.integrationsSetTier).not.toHaveBeenCalled();
    });

    test("a failure while giving the tiers keeps the step open", async () => {
      const c = await connected();
      api.integrationsSetGroupTier.mockRejectedValueOnce("no");
      c.click(button("review-done"));
      await settle();
      expect(c.view().review).not.toBeNull();
      expect(c.view().review!.busy).toBe(false);
    });

    test("pressing another item drops the step; 「去決定」 brings it back for a program with tools to decide", async () => {
      const c = await connected();
      c.click(button("select", { id: "overview" }));
      expect(c.view().review).toBeNull();
      c.click(button("review", { id: "new1" }));
      expect(c.view().selected).toBe("new1");
      expect(c.view().review!.tiers).toEqual({ look: "T0", list: "T0", wipe: null });
    });

    test("a program whose tools could not be read has nothing to decide yet", async () => {
      api.integrationsAdd.mockResolvedValue({ server: { id: "new1" } });
      api.integrationsRefresh.mockRejectedValue("down");
      api.toolsetsList.mockResolvedValue({ groups: [...GROUPS.slice(0, 2), { ...fresh, status: "error", error: "timeout", tools: [], pending_count: 0 }] });
      const c = createConnections(host);
      await c.load();
      c.click(button("add"));
      c.input(input("conn-name", "Notes"));
      c.input(input("conn-command", "npx"));
      c.click(button("connect"));
      await settle();
      expect(c.view().selected).toBe("new1");
      expect(c.view().review).toBeNull();
    });
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
    expect(c.view().selected).toBe("overview");
  });

  test("pressing another item drops an unsent form with its values", async () => {
    const c = createConnections(host);
    await c.load();
    c.click(button("add"));
    c.input(input("conn-name", "Half"));
    c.input(input("conn-pair-name", "TOKEN"));
    c.input(input("conn-pair-value", "TOPSECRET"));
    c.click(button("select", { id: "builtin:web" }));
    expect(c.view().form).toBeNull();
    expect(JSON.stringify(c.view())).not.toContain("TOPSECRET");
  });

  test("leaving the tab drops the form, the step and the pending removal", async () => {
    const c = createConnections(host);
    await c.load();
    c.click(button("select", { id: "ab12" }));
    c.click(button("review", { id: "ab12" }));
    c.click(button("remove", { id: "ab12" }));
    c.reset();
    expect(c.view()).toMatchObject({ selected: "overview", review: null, form: null, confirmingRemoval: null });
  });

  test("ignores a click that is none of its own", () => {
    const c = createConnections(host);
    expect(c.click({ closest: () => null } as unknown as Element)).toBe(false);
  });
});
