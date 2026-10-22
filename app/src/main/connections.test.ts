import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { setLocale } from "./i18n";
import type { AuditEntry, ConnectionGroup, StoreService } from "./types";

const api = vi.hoisted(() => ({
  toolsetsList: vi.fn(),
  integrationsCatalog: vi.fn(),
  integrationsAudit: vi.fn(),
  integrationsConnect: vi.fn(),
  integrationsConnectCancel: vi.fn(),
  integrationsAdd: vi.fn(),
  integrationsRefresh: vi.fn(),
  integrationsSetEnabled: vi.fn(),
  integrationsDisconnect: vi.fn(),
  integrationsSetToolEnabled: vi.fn(),
  integrationsSetToolsEnabled: vi.fn(),
}));
vi.mock("./api", () => api);
vi.mock("./ui", async (original) => ({ ...(await original<typeof import("./ui")>()), toaster: { info: vi.fn() } }));

import { type ConnectionForm, type ConnectionsView, bodyHTML, connectionsHTML, createConnections, emptyConnectionsView, needsLogin, toolLabel } from "./connections";
import { toaster } from "./ui";

afterEach(() => setLocale("zh-TW"));

const NOW = Date.UTC(2026, 9, 21, 6, 0);
const CJK = /[㐀-鿿぀-ヿ]/;

const words = (zh: string, en: string, ja: string) => ({ "zh-TW": zh, en, ja });
const service = (id: string, name: string, over: Partial<StoreService> = {}): StoreService => ({
  id, name, publisher: name, url: `https://mcp.${id}.example/mcp`, category: "docs", icon: null, popular: false,
  summary: words(`${name}的一句話`, `${name} in a line`, `${name}の一言`),
  about: words(`${name}的說明`, `About ${name}`, `${name}について`),
  examples: { "zh-TW": [`跟${name}說一`, `跟${name}說二`], en: [`Tell ${name} one`, `Tell ${name} two`], ja: [`${name}に一`, `${name}に二`] },
  server_id: null, ...over,
});
const CATALOG: StoreService[] = [
  service("notion", "Notion", { icon: "notion", popular: true, server_id: "ab12" }),
  service("linear", "Linear", { icon: "linear", popular: true }),
  service("sentry", "Sentry", { icon: "sentry" }),
  service("canva", "Canva", { popular: true }),
];

const notes: ConnectionGroup = {
  id: "ab12", name: "Notion", enabled: true, status: "ready", error: null, auth: "oauth", catalog_id: "notion",
  transport: "http", command: "", url: "https://mcp.notion.example/mcp", env: [], headers: [], args: [],
  tool_count: 3, enabled_count: 2, write_count: 2, read_count: 1, checked_at_ms: NOW,
  tools: [
    { name: "search", description: "Search the workspace.", enabled: true, kind: "read" },
    { name: "create_page", description: "Create a page.", enabled: true, kind: "write" },
    { name: "delete_page", description: "Delete a page.", enabled: false, kind: "write" },
  ],
};
const custom: ConnectionGroup = {
  ...notes, id: "cu1", name: "Files", catalog_id: null, auth: "none", transport: "stdio", command: "npx", url: "", env: ["API_KEY"],
  tool_count: 1, enabled_count: 1, write_count: 0, read_count: 1, tools: [{ name: "list", description: "List.", enabled: true, kind: "read" }],
};
const GROUPS = [notes, custom];
const AUDIT: AuditEntry[] = [{ id: 1, at_ms: NOW - 60_000, server: "Notion", tool: "create_page", approver: "auto", outcome: "ok" }];

const view = (over: Partial<ConnectionsView> = {}): ConnectionsView => ({
  ...emptyConnectionsView(), groups: GROUPS, catalog: CATALOG, audit: AUDIT, now: NOW, ...over,
});
const form = (over: Partial<ConnectionForm> = {}): ConnectionForm => ({
  type: "stdio", name: "", command: "", args: "", url: "", pairs: [], pairName: "", pairValue: "", busy: false, error: null, ...over,
});
const lastToast = (): string => {
  const calls = vi.mocked(toaster.info).mock.calls;
  return calls[calls.length - 1]![0] as string;
};
const installedPage = (id: string, over: Partial<ConnectionsView> = {}): string =>
  connectionsHTML(view({ open: { tab: "installed", id }, ...over }));
const storePage = (id: string, over: Partial<ConnectionsView> = {}): string => connectionsHTML(view({ tab: "store", open: { tab: "store", id }, ...over }));

describe("the two views", () => {
  test("are segmented buttons that say which is open, with the search and the menu that adds beside them", () => {
    const html = connectionsHTML(view());
    const bar = html.slice(html.indexOf('class="cs-bar"'), html.indexOf("data-cs-body"));
    expect(bar).toContain('role="tablist"');
    expect(bar).toMatch(/<button[^>]*class="tab"[^>]*role="tab"[^>]*aria-selected="true"[^>]*data-id="installed"[^>]*>已安裝<span class="n">2<\/span>/);
    expect(bar).toMatch(/<button[^>]*class="tab"[^>]*role="tab"[^>]*aria-selected="false"[^>]*data-id="store"[^>]*>商店</);
    expect(bar).toContain('id="conn-search"');
    expect(bar).toMatch(/<button[^>]*class="sel"[^>]*data-k="conn-add"[^>]*><span>新增<\/span>/);
    expect(connectionsHTML(view({ tab: "store" }))).toMatch(/aria-selected="true"[^>]*data-id="store"/);
  });

  test("the loading and failed states say so", () => {
    expect(connectionsHTML(view({ groups: "loading" }))).toContain("讀取中");
    expect(connectionsHTML(view({ groups: "failed" }))).toContain("無法讀取連接");
    expect(connectionsHTML(view({ tab: "store", catalog: "failed" }))).toContain("無法讀取連接");
  });
});

describe("已安裝", () => {
  const html = connectionsHTML(view());

  test("has one row for each service, with its mark, its name, a line about it, a switch and a button that opens it", () => {
    const rows = html.split('class="row cs-row"').slice(1);
    expect(rows).toHaveLength(2);
    const [notion, files] = rows;
    expect(notion).toContain("Notion");
    expect(notion).toContain("Notion的一句話");
    expect(notion).toContain('class="logo"');
    expect(notion).toMatch(/class="tg" role="switch" aria-checked="true" aria-label="開啟 Notion" data-conn="enable" data-id="ab12"/);
    expect(notion).toMatch(/class="btn icon" data-conn="open" data-id="ab12" aria-label="查看 Notion"/);
    // a server of the user's own: where it is reached, and a letter for a mark
    expect(files).toContain("Files");
    expect(files).toContain("npx");
    expect(files).toContain(">F<");
  });

  test("the switch is the whole service's and shows it off", () => {
    expect(connectionsHTML(view({ groups: [{ ...notes, enabled: false, status: "disabled" }] }))).toContain('aria-checked="false"');
  });

  test("says what is wrong with a service where it is, and a login that ran out has a button that signs in again", () => {
    const login: ConnectionGroup = { ...notes, status: "error", error: "auth" };
    expect(needsLogin(login)).toBe(true);
    expect(needsLogin({ ...login, error: "timeout" })).toBe(false);
    expect(needsLogin({ ...login, auth: "none" })).toBe(false);
    const out = connectionsHTML(view({ groups: [login] }));
    expect(out).toContain("需要重新登入");
    expect(out).toMatch(/<button[^>]*class="btn sm"[^>]*data-conn="relogin" data-id="ab12"[^>]*>重新登入</);
    const failed = connectionsHTML(view({ groups: [{ ...notes, status: "error", error: "timeout" }] }));
    expect(failed).toContain("連線失敗：太久沒有回應");
    expect(failed).not.toContain('data-conn="relogin"');
    expect(connectionsHTML(view({ groups: [{ ...notes, status: "unchecked" }] }))).toContain("還沒有讀取工具");
  });

  test("while the sign-in is open the button says so and every other sign-in waits", () => {
    const login: ConnectionGroup = { ...notes, status: "error", error: "auth" };
    const out = connectionsHTML(view({ groups: [login], login: { target: "ab12", name: "Notion" } }));
    expect(out).toMatch(/data-conn="relogin"[^>]*disabled[^>]*>請在瀏覽器登入…/);
  });

  test("an empty list points at the store, and a search that finds nothing says so", () => {
    expect(connectionsHTML(view({ groups: [] }))).toContain("到「商店」加入一個");
    expect(connectionsHTML(view({ search: "zzz" }))).toContain("沒有符合的項目");
    const found = bodyHTML(view({ search: "files" }));
    expect(found).toContain("Files");
    expect(found).not.toContain("Notion");
  });

  test("keeps the list clean: the latest actions are on the page of each service, not here", () => {
    expect(html).not.toContain("最近的動作");
    expect(html).not.toContain("create_page");
  });
});

describe("the page of a connected service", () => {
  test("has the mark, the name, who provides it, the switch, what it is and the count of its tools", () => {
    const html = installedPage("ab12");
    expect(html).toContain('data-conn="back"');
    expect(html).toContain('<h2 class="big">Notion</h2>');
    expect(html).toContain("由 Notion 提供");
    expect(html).toContain("Notion的說明");
    expect(html).toContain("共 3 個工具（2 個會改動、1 個只讀取）");
    expect(html).toMatch(/class="mono xl logo-mono"/);
    expect(html).toMatch(/role="switch" aria-checked="true" aria-label="開啟 Notion" data-conn="enable" data-id="ab12"/);
    expect(html).toContain("https://mcp.notion.example/mcp");
    expect(html).not.toContain('data-cs-body');
  });

  test("has the tools in two lists that unfold, 會改動 and 只讀取, each with a switch of its own", () => {
    const folded = installedPage("ab12");
    expect(folded).toMatch(/aria-expanded="false" data-conn="fold" data-kind="write"><span class="gt">會改動<small>2 個<\/small>/);
    expect(folded).toMatch(/aria-expanded="false" data-conn="fold" data-kind="read"><span class="gt">只讀取<small>1 個<\/small>/);
    expect(folded).not.toContain(">Create page<");
    const both = installedPage("ab12", { unfolded: { write: true, read: true } });
    const write = both.slice(both.indexOf('data-kind="write"'), both.indexOf('data-kind="read"'));
    const read = both.slice(both.indexOf('data-kind="read"'));
    expect(write).toContain(">Create page<");
    expect(write).toContain(">Delete page<");
    expect(write).not.toContain(">Search<");
    expect(read).toContain(">Search<");
    expect(read).toContain('title="search"'); // the raw name stays in the tooltip
    expect(both.match(/data-conn="tool"/g)).toHaveLength(3);
    expect(both).toMatch(/aria-checked="false" aria-label="開啟工具 Delete page" data-conn="tool" data-id="ab12" data-tool="delete_page"/);
    expect(both).toMatch(/aria-checked="true" aria-label="開啟工具 Search" data-conn="tool" data-id="ab12" data-tool="search"/);
  });

  test("gives each list a switch for all its tools: on, mixed or off, a real control with a name of its own", () => {
    const html = installedPage("ab12");
    // Notion has two write tools, one on and one off: mixed; its only read tool is on
    expect(html).toMatch(/<button type="button" class="tg" role="checkbox" aria-checked="mixed" aria-label="全部會改動的工具" data-conn="group" data-id="ab12" data-kind="write"><\/button>/);
    expect(html).toMatch(/role="checkbox" aria-checked="true" aria-label="全部只讀取的工具" data-conn="group" data-id="ab12" data-kind="read"/);
    const allOff = { ...notes, tools: notes.tools.map((x) => ({ ...x, enabled: false })) };
    expect(installedPage("ab12", { groups: [allOff, custom] })).toMatch(/aria-checked="false" aria-label="全部會改動的工具"/);
    // the switch is beside the fold button, never inside it: a button holds no other control
    expect(html).toMatch(/<div class="ghead">\s*<button type="button" class="btn ghfold"[^>]*data-conn="fold" data-kind="write">/);
    expect(html.match(/<button[^>]*data-conn="fold"[^>]*>(?:(?!<\/button>).)*<button/s)).toBeNull();
    // a service that has only one kind of tool shows only that list's switch
    expect(installedPage("cu1").match(/data-conn="group"/g)).toHaveLength(1);
    expect(installedPage("ab12", { busy: new Set(["ab12"]) })).toMatch(/data-conn="group"[^>]* disabled/);
  });

  test("names the group switches in the language of the window", () => {
    setLocale("en");
    expect(installedPage("ab12")).toContain('aria-label="All tools that change things"');
    expect(installedPage("ab12")).toContain('aria-label="All read-only tools"');
    setLocale("ja");
    expect(installedPage("ab12")).toContain('aria-label="変更するツールすべて"');
    expect(installedPage("ab12")).toContain('aria-label="読み取り専用のツールすべて"');
  });

  test("ends with that service's own latest actions, by tool, and the note that no content is kept", () => {
    const html = installedPage("ab12", { audit: [...AUDIT, { ...AUDIT[0], id: 2, server: "Files", tool: "list" }] });
    const audit = html.slice(html.indexOf("最近的動作"));
    expect(audit).toContain("Notion / create_page");
    expect(audit).not.toContain("Files / list");
    expect(audit).toContain("自動執行");
    expect(audit).toContain("只記錄時間、工具和結果，不記錄內容。");
    expect(installedPage("cu1")).not.toContain("最近的動作"); // none of its own
    expect(installedPage("ab12", { audit: [] })).not.toContain("最近的動作");
    expect(audit).not.toMatch(/T[0-3]\b/);
  });

  test("has no levels, no review step and no word about pending tools", () => {
    for (const html of [installedPage("ab12", { unfolded: { write: true, read: true } }), connectionsHTML(view()), storePage("linear")]) {
      expect(html).not.toMatch(/T[0-3]\b|決定權限|先問我|按住確認|待決定|conn-tier|conn-draft|conn-all|review/);
    }
  });

  test("a server that changed its tools shows them all the same, new ones on", () => {
    const grown: ConnectionGroup = { ...notes, tools: [...notes.tools, { name: "fresh", description: "New.", enabled: true, kind: "read" }], tool_count: 4, read_count: 2, enabled_count: 3 };
    const html = installedPage("ab12", { groups: [grown], unfolded: { write: false, read: true } });
    expect(html).toContain("共 4 個工具（2 個會改動、2 個只讀取）");
    expect(html).toMatch(/aria-checked="true" aria-label="開啟工具 Fresh"/);
    expect(html).not.toMatch(/>新<|待決定/);
  });

  test("a disconnect asks twice, and a login that ran out is said at the top with a button", () => {
    expect(installedPage("ab12")).toContain(">中斷連線<");
    expect(installedPage("ab12")).not.toContain("確定中斷？");
    expect(installedPage("ab12", { confirmingDisconnect: "ab12" })).toContain("確定中斷？");
    const out = installedPage("ab12", { groups: [{ ...notes, status: "error", error: "auth" }] });
    expect(out).toContain("需要重新登入");
    expect(out).toContain('data-conn="relogin"');
  });

  test("a server of the user's own has no store words: no publisher, the command, the names of its secrets", () => {
    const html = installedPage("cu1");
    expect(html).toContain("自訂伺服器");
    expect(html).not.toContain("由 ");
    expect(html).toContain("npx");
    expect(html).toContain("API_KEY");
    expect(html).toContain("共 1 個工具（0 個會改動、1 個只讀取）");
  });

  test("tools not read yet say how to read them", () => {
    expect(installedPage("cu1", { groups: [{ ...custom, tools: [], tool_count: 0, status: "unchecked" }] })).toContain("重新讀取工具");
  });

  test("a page whose service is gone is the list again", () => {
    expect(installedPage("nope")).toContain("data-cs-body");
  });
});

describe("商店", () => {
  const html = connectionsHTML(view({ tab: "store" }));

  test("has 熱門, then 全部 for the rest, as two columns of cards", () => {
    expect(html).toContain("<h2>熱門</h2>");
    expect(html).toContain("<h2>全部</h2>");
    const popular = html.slice(html.indexOf("<h2>熱門</h2>"), html.indexOf("<h2>全部</h2>"));
    expect([...popular.matchAll(/class="nm">([^<]+)</g)].map((m) => m[1])).toEqual(["Notion", "Linear", "Canva"]);
    const all = html.slice(html.indexOf("<h2>全部</h2>"));
    expect([...all.matchAll(/class="nm">([^<]+)</g)].map((m) => m[1])).toEqual(["Sentry"]); // the rest, by name, none twice
    expect(html.match(/class="cs-grid"/g)).toHaveLength(2);
  });

  test("a card is a container with the button that opens the page and, inside it, the button that adds, or 已安裝; no button is in a button", () => {
    const cards = html.split('<div class="cs-card">').slice(1);
    const card = cards.find((c) => c.includes('data-id="linear"'))!;
    expect(card).toMatch(/class="btn cs-main" data-conn="open-store" data-id="linear"/);
    expect(card).toContain("Linear的一句話");
    // the container holds both, and nothing is nested
    expect(html).toMatch(/<div class="cs-card"><button[^>]*cs-main[^>]*>(?:(?!<\/button>).)*<\/button><button[^>]*data-conn="join"/);
    for (const c of cards) expect(c.match(/<button/g)!.length).toBeLessThanOrEqual(2);
    expect(html).not.toMatch(/<button(?:(?!<\/button>).)*<button/);
    expect(card).toMatch(/<button[^>]*class="btn sm" data-conn="join" data-id="linear"[^>]*>.*加入<\/button>/);
    const notion = cards.find((c) => c.includes('data-id="notion"'))!;
    expect(notion).toContain("已安裝");
    expect(notion).not.toContain('data-conn="join" data-id="notion"');
  });

  test("a service with no mark is a letter, and one with a mark has it bare, in the brand colour where it reads", () => {
    expect(html).toContain(">C<"); // Canva
    expect(html).toMatch(/class="mono md logo-mono" style="--brand:#5E6AD2"/); // Linear
    expect(html).toMatch(/class="mono md logo-mono">/); // Notion: the colour of the text
    expect(html).not.toMatch(/class="mono[^"]*tile|background/);
  });

  test("while a login is open the card says so with a button to cancel, and the other 加入 buttons wait", () => {
    const out = connectionsHTML(view({ tab: "store", login: { target: "linear", name: "Linear" } }));
    const linear = out.slice(out.indexOf('data-id="linear"'));
    expect(linear.slice(0, linear.indexOf("</div>"))).toContain("請在瀏覽器登入…");
    expect(linear.slice(0, linear.indexOf("</div>"))).toContain('data-conn="cancel-login"');
    expect(out).toMatch(/data-conn="join" data-id="sentry"[^>]*disabled/);
  });

  test("searches by name, publisher and summary, and then lists the matches once", () => {
    const found = bodyHTML(view({ tab: "store", search: "lin" }));
    expect(found).not.toContain("<h2>熱門</h2>");
    expect(found).toContain("<h2>全部</h2>");
    expect([...found.matchAll(/class="nm">([^<]+)</g)].map((m) => m[1])).toEqual(["Linear"]);
    expect(bodyHTML(view({ tab: "store", search: "zzz" }))).toContain("沒有符合的項目");
  });
});

describe("the names of tools", () => {
  const tool = (name: string, title?: string | null) => ({ name, description: "", enabled: true, kind: "read" as const, title });
  test("the title a server gives is used", () => {
    expect(toolLabel(tool("notion-search", "Search Notion"), "Notion")).toBe("Search Notion");
    expect(toolLabel(tool("x", "  "), "")).toBe("X");
  });
  test("otherwise the name is made readable: the service's prefix off, dashes and underscores as spaces, a capital", () => {
    expect(toolLabel(tool("notion-create-pages"), "Notion")).toBe("Create pages");
    expect(toolLabel(tool("notion_get_users"), "Notion")).toBe("Get users");
    expect(toolLabel(tool("create_page"), "Notion")).toBe("Create page");
    expect(toolLabel(tool("list-issues"), "Linear")).toBe("List issues");
    expect(toolLabel(tool("linear"), "Linear")).toBe("Linear"); // a name that is only the prefix stays
    expect(toolLabel(tool("-_"), "")).toBe("-_");
    expect(toolLabel(tool("getUser"), "")).toBe("GetUser");
  });
});

describe("the page of a store service", () => {
  test("has a big mark, the name, who provides it, the button that adds, things to say, what it is and the server", () => {
    const html = storePage("linear");
    expect(html).toContain('data-conn="back"');
    expect(html).toMatch(/class="mono xl logo-mono"/);
    expect(html).toContain('<h2 class="big">Linear</h2>');
    expect(html).toContain("由 Linear 提供");
    expect(html).toMatch(/<button[^>]*class="btn pri" data-conn="join" data-id="linear">.*加入<\/button>/);
    expect(html).toContain("對拉斐爾說說看");
    expect(html).toContain("跟Linear說一");
    expect(html).toContain("跟Linear說二");
    expect(html).toContain("Linear的說明");
    expect(html).toContain("https://mcp.linear.example/mcp");
  });

  test("an added service says so and opens its own page", () => {
    const html = storePage("notion");
    expect(html).toContain("已安裝");
    expect(html).toContain('data-conn="manage" data-id="ab12"');
    expect(html).not.toContain('data-conn="join"');
  });

  test("a public service has no login to wait for: it says it is connecting, in card and page", () => {
    const open = service("context7", "Context7", { icon: "context7", auth: "none" });
    const login = { target: "context7", name: "Context7" };
    const base = { catalog: [...CATALOG, open], login };
    const card = connectionsHTML(view({ ...base, tab: "store" }));
    expect(card.slice(card.indexOf('data-id="context7"'))).toContain("連線中…");
    const page = storePage("context7", base);
    expect(page).toContain("連線中…");
    expect(page).not.toContain("請在瀏覽器登入");
  });

  test("waiting for the browser it says so and has 取消; a failed login says why and the button is back", () => {
    const waiting = storePage("linear", { login: { target: "linear", name: "Linear" } });
    expect(waiting).toContain("請在瀏覽器登入…");
    expect(waiting).toContain('data-conn="cancel-login"');
    expect(waiting).not.toContain('data-conn="join"');
    const denied = storePage("linear", { loginError: { target: "linear", kind: "denied" } });
    expect(denied).toContain("你在瀏覽器上拒絕了，所以沒有加入。");
    expect(denied).toContain('data-conn="join"');
    expect(storePage("linear", { loginError: { target: "linear", kind: "timeout" } })).toContain("太久沒有登入");
    expect(storePage("linear", { loginError: { target: "linear", kind: "network" } })).toContain("沒有登入成功：網路連不上");
    expect(storePage("sentry", { loginError: { target: "linear", kind: "denied" } })).not.toContain("拒絕");
  });
});

describe("the form that connects a server of the user's own", () => {
  test("takes the place of the tab: a type dropdown, fields for that type, a warning, two buttons", () => {
    const html = connectionsHTML(view({ form: form() }));
    expect(html).toContain("自訂伺服器");
    expect(html).toContain('data-k="conn-type"');
    expect(html).toContain('id="conn-command"');
    expect(html).toContain("只接你信任的程式。");
    expect(html).toContain("全部開啟");
    expect(html).toContain('data-conn="connect"');
    expect(html).toContain('data-conn="form-cancel"');
    expect(html).not.toContain('role="tablist"');
    expect(html).not.toContain('role="dialog"');
  });

  test("an address and a header for the network type; the values of secrets are never printed", () => {
    const f = form({ type: "http", name: "Remote", url: "https://example.com/mcp", pairs: [{ name: "Authorization", value: "Bearer TOPSECRET" }], pairName: "X-Key", pairValue: "ALSOSECRET" });
    const html = connectionsHTML(view({ form: f }));
    expect(html).toContain('id="conn-url"');
    expect(html).not.toContain('id="conn-command"');
    expect(html).toContain("Authorization");
    expect(html).toContain("標頭名稱");
    expect(html).not.toContain("TOPSECRET");
    const valueField = /<input[^>]*id="conn-pair-value"[^>]*>/.exec(html)![0];
    expect(valueField).toContain('type="password"'); // typed, not yet added: shown as dots, and only here
  });
});

describe("the rules for this tab", () => {
  const pages = (): string[] => [
    connectionsHTML(view()),
    connectionsHTML(view({ tab: "store" })),
    connectionsHTML(view({ tab: "store", login: { target: "linear", name: "Linear" } })),
    installedPage("ab12", { unfolded: { write: true, read: true }, confirmingDisconnect: "ab12" }),
    installedPage("ab12", { groups: [{ ...notes, status: "error", error: "auth" }] }),
    storePage("linear", { loginError: { target: "linear", kind: "denied" } }),
    connectionsHTML(view({ form: form({ pairs: [{ name: "TOKEN", value: "x" }] }) })),
  ];

  test("everything that does something is a button or a switch with a shape, and every choice is a dropdown", () => {
    const all = pages().join("");
    const acts = [...all.matchAll(/<(\w+)[^>]*data-conn="/g)].map((m) => m[1]);
    expect(acts.length).toBeGreaterThan(30);
    expect(new Set(acts)).toEqual(new Set(["button"]));
    for (const tag of all.match(/<button[^>]*>/g)!) expect(tag).toMatch(/class="(btn[ "]|sel[ "]|tg|tab")/);
    expect(all).not.toMatch(/<a[ >]/);
    expect(all).not.toContain("<select");
    expect(all).not.toContain('type="radio"');
    expect(all).not.toContain("onclick");
  });

  test("the segmented buttons are real bordered buttons, not clickable text", () => {
    const tabs = connectionsHTML(view()).match(/<button[^>]*role="tab"[^>]*>/g)!;
    expect(tabs).toHaveLength(2);
    for (const tag of tabs) expect(tag).toContain('class="tab"');
  });

  test.each(["en", "ja"] as const)("is in one language at a time (%s)", (locale) => {
    setLocale(locale);
    const all = pages().join("");
    for (const zh of ["已安裝", "商店", "加入", "中斷連線", "熱門", "只讀取", "會改動", "請在瀏覽器登入", "重新登入", "重新讀取工具", "最近的動作", "沒有符合"]) expect(all).not.toContain(zh);
    if (locale === "en") {
      // what the service itself says (here the fake store's own words) is the catalog's, in English
      expect(all).toContain("Installed");
      expect(all).toContain("Sign in in your browser…");
      expect(all).toContain("Contains 3 tools (2 write, 1 read)");
      expect(all).toContain("Tell Linear one");
      expect(all).not.toMatch(CJK);
    } else {
      expect(all).toContain("インストール済み");
      expect(all).toContain("ブラウザでログインしてください…");
      expect(all).toContain("Linearに一");
      expect(all).not.toMatch(/Installed|Popular|Sign in|Contains|Recent actions|Disconnect/);
      expect(all).not.toContain("跟");
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
    vi.mocked(toaster.info).mockReset();
    api.toolsetsList.mockResolvedValue({ groups: GROUPS });
    api.integrationsCatalog.mockResolvedValue({ services: CATALOG });
    api.integrationsAudit.mockResolvedValue({ entries: AUDIT });
    for (const name of ["integrationsRefresh", "integrationsSetEnabled", "integrationsSetToolEnabled"] as const) api[name].mockResolvedValue({});
    api.integrationsDisconnect.mockResolvedValue({ removed: true, revoked: true });
    api.integrationsConnectCancel.mockResolvedValue({ cancelled: true });
    host.redraw.mockReset();
  });

  test("reads the services, the store and the latest actions, and starts on 已安裝", async () => {
    const c = createConnections(host);
    expect(c.view().groups).toBe("loading");
    await c.load();
    expect(c.view().groups).toEqual(GROUPS);
    expect(c.view().catalog).toEqual(CATALOG);
    expect(c.view().audit).toEqual(AUDIT);
    expect(c.view().tab).toBe("installed");
  });

  test("with nothing connected yet it starts on the store, once", async () => {
    api.toolsetsList.mockResolvedValue({ groups: [] });
    const c = createConnections(host);
    await c.load();
    expect(c.view().tab).toBe("store");
    c.click(button("tab", { id: "installed" }));
    await c.load();
    expect(c.view().tab).toBe("installed");
  });

  test("says the services cannot be had when the first read fails, and keeps the old ones when a later one does", async () => {
    api.toolsetsList.mockRejectedValueOnce("down");
    api.integrationsCatalog.mockRejectedValueOnce("down");
    const c = createConnections(host);
    await c.load();
    expect(c.view().groups).toBe("failed");
    expect(c.view().catalog).toBe("failed");
    await c.load();
    api.toolsetsList.mockRejectedValueOnce("down");
    api.integrationsCatalog.mockRejectedValueOnce("down");
    await c.load();
    expect(c.view().groups).toEqual(GROUPS);
    expect(c.view().catalog).toEqual(CATALOG);
  });

  test("changes view, opens a page and goes back; a page whose service is gone closes", async () => {
    const c = createConnections(host);
    await c.load();
    c.click(button("tab", { id: "store" }));
    expect(c.view().tab).toBe("store");
    c.click(button("open-store", { id: "linear" }));
    expect(c.view().open).toEqual({ tab: "store", id: "linear" });
    c.click(button("back"));
    expect(c.view().open).toBeNull();
    c.click(button("tab", { id: "installed" }));
    c.click(button("open", { id: "ab12" }));
    expect(c.view().open).toEqual({ tab: "installed", id: "ab12" });
    api.toolsetsList.mockResolvedValue({ groups: [custom] });
    await c.load();
    expect(c.view().open).toBeNull();
  });

  test("a store page opens the page of the service it was added as", async () => {
    const c = createConnections(host);
    await c.load();
    c.click(button("open-store", { id: "notion" }));
    c.click(button("manage", { id: "ab12" }));
    expect(c.view().open).toEqual({ tab: "installed", id: "ab12" });
  });

  test("a group switch sets every tool of its list in one call: all off when all are on, all on otherwise", async () => {
    api.integrationsSetToolsEnabled.mockResolvedValue({});
    const c = createConnections(host);
    await c.load();
    // the write list is mixed (one on, one off): turns all on
    c.click(button("group", { id: "ab12", kind: "write" }));
    await settle();
    expect(api.integrationsSetToolsEnabled).toHaveBeenLastCalledWith("ab12", ["create_page", "delete_page"], true);
    // the read list is all on: turns all off
    c.click(button("group", { id: "ab12", kind: "read" }));
    await settle();
    expect(api.integrationsSetToolsEnabled).toHaveBeenLastCalledWith("ab12", ["search"], false);
    expect(api.integrationsSetToolEnabled).not.toHaveBeenCalled();
  });

  test("unfolds and folds the two lists of tools", async () => {
    const c = createConnections(host);
    await c.load();
    c.click(button("open", { id: "ab12" }));
    c.click(button("fold", { kind: "write" }));
    expect(c.view().unfolded).toEqual({ write: true, read: false });
    c.click(button("fold", { kind: "write" }));
    expect(c.view().unfolded.write).toBe(false);
  });

  test("the search filters what is under the bar without drawing the page again", async () => {
    const body = { innerHTML: "" };
    vi.stubGlobal("document", { querySelector: () => body });
    try {
      const c = createConnections(host);
      await c.load();
      host.redraw.mockReset();
      expect(c.input(input("conn-search", "files"))).toBe(true);
      expect(c.view().search).toBe("files");
      expect(body.innerHTML).toContain("Files");
      expect(body.innerHTML).not.toContain("Notion");
      expect(host.redraw).not.toHaveBeenCalled();
      c.click(button("tab", { id: "store" }));
      expect(c.view().search).toBe(""); // another view starts with no search
    } finally {
      vi.unstubAllGlobals();
    }
  });

  test("switches a service on or off, a tool on or off, and reads its tools again", async () => {
    const c = createConnections(host);
    await c.load();
    c.click(button("enable", { id: "ab12" }));
    await settle();
    expect(api.integrationsSetEnabled).toHaveBeenCalledWith("ab12", false);
    c.click(button("tool", { id: "ab12", tool: "delete_page" }));
    await settle();
    expect(api.integrationsSetToolEnabled).toHaveBeenLastCalledWith("ab12", "delete_page", true);
    c.click(button("tool", { id: "ab12", tool: "search" }));
    await settle();
    expect(api.integrationsSetToolEnabled).toHaveBeenLastCalledWith("ab12", "search", false);
    c.click(button("refresh", { id: "ab12" }));
    await settle();
    expect(api.integrationsRefresh).toHaveBeenCalledWith("ab12");
  });

  test("disconnects only on the second press, and tells where else the access can be taken away", async () => {
    vi.useFakeTimers();
    try {
      const c = createConnections(host);
      await c.load();
      c.click(button("open", { id: "ab12" }));
      c.click(button("disconnect", { id: "ab12" }));
      expect(c.view().confirmingDisconnect).toBe("ab12");
      expect(api.integrationsDisconnect).not.toHaveBeenCalled();
      vi.advanceTimersByTime(5100);
      expect(c.view().confirmingDisconnect).toBeNull();
      api.toolsetsList.mockResolvedValue({ groups: [custom] });
      c.click(button("disconnect", { id: "ab12" }));
      c.click(button("disconnect", { id: "ab12" }));
      await vi.advanceTimersByTimeAsync(0);
      expect(api.integrationsDisconnect).toHaveBeenCalledTimes(1);
      expect(api.integrationsDisconnect).toHaveBeenCalledWith("ab12");
      expect(c.view().open).toBeNull();
      expect(lastToast()).toContain("Notion 自己的設定");
    } finally {
      vi.useRealTimers();
    }
  });

  describe("adding a service from the store", () => {
    test("加入 starts the login, shows the wait, and on success the service is under 已安裝", async () => {
      let finish!: (value: unknown) => void;
      api.integrationsConnect.mockReturnValue(new Promise((resolve) => (finish = resolve)));
      const c = createConnections(host);
      await c.load();
      c.click(button("tab", { id: "store" }));
      c.click(button("join", { id: "linear" }));
      expect(api.integrationsConnect).toHaveBeenCalledWith({ catalog_id: "linear" });
      expect(c.view().login).toEqual({ target: "linear", name: "Linear" });
      expect(connectionsHTML(c.view())).toContain("請在瀏覽器登入…");
      api.toolsetsList.mockResolvedValue({ groups: [...GROUPS, { ...notes, id: "ln1", name: "Linear", catalog_id: "linear" }] });
      finish({ connected: true, server: { id: "ln1" } });
      await settle();
      expect(c.view().login).toBeNull();
      expect(c.view().tab).toBe("installed");
      expect(c.view().open).toBeNull();
      expect(lastToast()).toBe("已連接 Linear");
    });

    test("only one login at a time", async () => {
      api.integrationsConnect.mockReturnValue(new Promise(() => undefined));
      const c = createConnections(host);
      await c.load();
      c.click(button("join", { id: "linear" }));
      c.click(button("join", { id: "sentry" }));
      expect(api.integrationsConnect).toHaveBeenCalledTimes(1);
    });

    test("取消 ends the wait and says nothing about it", async () => {
      api.integrationsConnect.mockImplementation(async () => {
        await settle();
        return { connected: false, error: "cancelled" };
      });
      const c = createConnections(host);
      await c.load();
      c.click(button("join", { id: "linear" }));
      c.click(button("cancel-login"));
      expect(api.integrationsConnectCancel).toHaveBeenCalled();
      await settle();
      await settle();
      expect(c.view().login).toBeNull();
      expect(c.view().loginError).toBeNull();
      expect(toaster.info).not.toHaveBeenCalled();
    });

    test("a refusal, a timeout or a failure is told, and kept for the page of the service", async () => {
      const c = createConnections(host);
      await c.load();
      api.integrationsConnect.mockResolvedValueOnce({ connected: false, error: "denied" });
      c.click(button("join", { id: "linear" }));
      await settle();
      expect(c.view().loginError).toEqual({ target: "linear", kind: "denied" });
      expect(lastToast()).toBe("你在瀏覽器上拒絕了，所以沒有加入。");
      api.integrationsConnect.mockResolvedValueOnce({ connected: false, error: "timeout" });
      c.click(button("join", { id: "linear" }));
      await settle();
      expect(lastToast()).toContain("太久沒有登入");
      // trying again clears what went wrong
      api.integrationsConnect.mockReturnValueOnce(new Promise(() => undefined));
      c.click(button("join", { id: "linear" }));
      expect(c.view().loginError).toBeNull();
    });

    test("a call that throws is told and the buttons come back", async () => {
      api.integrationsConnect.mockRejectedValue("the core is not running");
      const c = createConnections(host);
      await c.load();
      c.click(button("join", { id: "linear" }));
      await settle();
      expect(c.view().login).toBeNull();
      expect(toaster.info).toHaveBeenCalled();
    });

    test("an entry that is not in the store does nothing", async () => {
      const c = createConnections(host);
      await c.load();
      c.click(button("join", { id: "nope" }));
      expect(api.integrationsConnect).not.toHaveBeenCalled();
    });
  });

  test("a service whose login ran out signs in again from its row, and stays where it was", async () => {
    api.toolsetsList.mockResolvedValue({ groups: [{ ...notes, status: "error", error: "auth" }, custom] });
    api.integrationsConnect.mockResolvedValue({ connected: true, server: { id: "ab12" } });
    const c = createConnections(host);
    await c.load();
    c.click(button("open", { id: "ab12" }));
    c.click(button("relogin", { id: "ab12" }));
    expect(api.integrationsConnect).toHaveBeenCalledWith({ id: "ab12" });
    await settle();
    expect(c.view().open).toEqual({ tab: "installed", id: "ab12" });
    expect(lastToast()).toBe("已重新登入 Notion");
  });

  describe("a server of the user's own", () => {
    const menu = (c: ReturnType<typeof createConnections>) => c.menuFor("conn-add")!;

    test("the 新增 menu has one item, 自訂伺服器, which opens the form", async () => {
      const c = createConnections(host);
      await c.load();
      expect(menu(c).choices.map((x) => x.label)).toEqual(["自訂伺服器"]);
      menu(c).pick("custom");
      expect(c.view().form).not.toBeNull();
      expect(c.menuFor("nothing")).toBeNull();
    });

    test("the values go to the core once, the names stay, the form is dropped, and the service is under 已安裝", async () => {
      api.integrationsAdd.mockResolvedValue({ server: { id: "new1" } });
      const c = createConnections(host);
      await c.load();
      c.click(button("tab", { id: "store" }));
      menu(c).pick("custom");
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
      expect(c.view().tab).toBe("installed");
      expect(JSON.stringify(c.view())).not.toContain("TOPSECRET");
    });

    test("an address with a header; a half-typed pair and missing fields are told before anything is sent", async () => {
      api.integrationsAdd.mockResolvedValue({ server: { id: "new2" } });
      const c = createConnections(host);
      await c.load();
      menu(c).pick("custom");
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
      menu(c).pick("custom");
      c.input(input("conn-name", "Notes"));
      c.input(input("conn-command", "npx"));
      c.click(button("connect"));
      await settle();
      expect(c.view().form!.busy).toBe(false);
      expect(c.view().form!.error!()).toContain("exists already");
      c.click(button("form-cancel"));
      expect(c.view().form).toBeNull();
    });
  });

  test("leaving the tab drops the form, the page, the search and a pending disconnect, but not a login that is under way", async () => {
    api.integrationsConnect.mockReturnValue(new Promise(() => undefined));
    const c = createConnections(host);
    await c.load();
    c.click(button("open", { id: "ab12" }));
    c.click(button("disconnect", { id: "ab12" }));
    c.click(button("join", { id: "linear" }));
    c.menuFor("conn-add")!.pick("custom");
    c.input(input("conn-name", "Half"));
    c.input(input("conn-pair-name", "TOKEN"));
    c.input(input("conn-pair-value", "TOPSECRET"));
    c.reset();
    expect(c.view()).toMatchObject({ tab: "installed", open: null, form: null, confirmingDisconnect: null, search: "" });
    expect(c.view().login).not.toBeNull();
    expect(JSON.stringify(c.view())).not.toContain("TOPSECRET");
  });

  test("ignores a click that is none of its own", () => {
    const c = createConnections(host);
    expect(c.click({ closest: () => null } as unknown as Element)).toBe(false);
  });
});
