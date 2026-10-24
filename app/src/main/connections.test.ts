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

import {
  type ConnectionForm,
  type ConnectionsView,
  bodyHTML,
  connectionsAddHTML,
  connectionsHTML,
  createConnections,
  emptyConnectionsView,
  examplePrompt,
  iconHTML,
  needsLogin,
  toolLabel,
} from "./connections";
import { ico, toaster } from "./ui";

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
  test("are quiet tabs that say which is open and how many each holds, with the search beside them", () => {
    const html = connectionsHTML(view());
    const bar = html.slice(html.indexOf('class="cs-bar"'), html.indexOf("data-cs-body"));
    expect(bar).toContain('class="tabs plain"');
    expect(bar).toContain('role="tablist"');
    expect(bar).toMatch(/<button[^>]*class="tab"[^>]*role="tab"[^>]*aria-selected="true"[^>]*data-id="installed"[^>]*>已安裝<span class="n">2<\/span>/);
    expect(bar).toMatch(/<button[^>]*class="tab"[^>]*role="tab"[^>]*aria-selected="false"[^>]*data-id="store"[^>]*>商店<span class="n">4<\/span>/);
    expect(bar).toContain('id="conn-search"');
    expect(connectionsHTML(view({ tab: "store" }))).toMatch(/aria-selected="true"[^>]*data-id="store"/);
    // nothing connected yet still says so: 0
    expect(connectionsHTML(view({ groups: [] }))).toMatch(/data-id="installed"[^>]*>已安裝<span class="n">0<\/span>/);
  });

  test("the menu that adds a server of the user's own is a dropdown, beside the tab's name (settings-view.ts puts it there)", () => {
    expect(connectionsAddHTML()).toMatch(/^<button type="button" class="sel cs-add" aria-haspopup="menu" aria-expanded="false" data-k="conn-add"><span>新增<\/span>/);
    expect(connectionsHTML(view())).not.toContain('data-k="conn-add"');
  });

  test("the loading and failed states say so", () => {
    expect(connectionsHTML(view({ groups: "loading" }))).toContain("讀取中");
    expect(connectionsHTML(view({ groups: "failed" }))).toContain("無法讀取連接");
    expect(connectionsHTML(view({ tab: "store", catalog: "failed" }))).toContain("無法讀取連接");
  });
});

describe("已安裝", () => {
  const html = connectionsHTML(view());
  const items = (out: string): string[] => out.split('<div class="cs-item"').slice(1);

  test("is one list, a row for each service: its mark, its name and a line, a switch, a chevron; the row opens the dialog", () => {
    expect(html.match(/class="cs-list"/g)).toHaveLength(1);
    expect(html).toContain('class="cs-list" data-stagger="installed"');
    const rows = items(html);
    expect(rows).toHaveLength(2);
    const [notion, files] = rows;
    expect(notion).toMatch(/^ data-conn="open" data-id="ab12">/);
    expect(notion).toMatch(/<button type="button" class="btn cs-open" data-conn="open" data-id="ab12" data-k="cs-open:ab12" aria-haspopup="dialog" title="查看 Notion">/);
    expect(notion).toContain('<span class="nm">Notion</span>');
    expect(notion).toContain('<span class="ds">Notion的一句話</span>');
    expect(notion).toContain('class="logo"');
    expect(notion).toMatch(/class="tg" role="switch" aria-checked="true" aria-label="開啟 Notion" data-conn="enable" data-id="ab12"/);
    // the chevron is a picture of where the row goes, not a second button
    expect(notion).toMatch(/<span class="chev" aria-hidden="true"><svg/);
    expect(notion.match(/<button/g)).toHaveLength(2);
    // A server of the user's own: where it is reached, and a neutral plug.
    expect(files).toContain("Files");
    expect(files).toContain("npx");
    expect(files).toContain(ico("plug"));
    expect(files).not.toContain(">F<");
  });

  test("the switch is the whole service's and shows it off", () => {
    expect(connectionsHTML(view({ groups: [{ ...notes, enabled: false, status: "disabled" }] }))).toContain('aria-checked="false"');
  });

  test("says what is wrong with a service in its line; the way to sign in again is in its dialog", () => {
    const login: ConnectionGroup = { ...notes, status: "error", error: "auth" };
    expect(needsLogin(login)).toBe(true);
    expect(needsLogin({ ...login, error: "timeout" })).toBe(false);
    expect(needsLogin({ ...login, auth: "none" })).toBe(false);
    const out = connectionsHTML(view({ groups: [login] }));
    expect(out).toContain("需要重新登入");
    expect(out).not.toContain('data-conn="relogin"');
    const failed = connectionsHTML(view({ groups: [{ ...notes, status: "error", error: "timeout" }] }));
    expect(failed).toContain("連線失敗：太久沒有回應");
    expect(connectionsHTML(view({ groups: [{ ...notes, status: "unchecked" }] }))).toContain("還沒有讀取工具");
  });

  test("an empty list points at the store, and a search that finds nothing says so", () => {
    expect(connectionsHTML(view({ groups: [] }))).toContain("到「商店」加入一個");
    expect(connectionsHTML(view({ search: "zzz" }))).toContain("沒有符合的項目");
    const found = bodyHTML(view({ search: "files" }));
    expect(found).toContain("Files");
    expect(found).not.toContain("Notion");
  });

  test("keeps the list clean: the tools and the latest actions are in each service's dialog, not here", () => {
    expect(html).not.toContain("最近的動作");
    expect(html).not.toContain("create_page");
    expect(html).not.toContain('role="dialog"');
  });
});

describe("the dialog of a connected service", () => {
  const dialog = (html: string): string => html.slice(html.indexOf('<div class="cs-scrim"'));

  test("opens over the list, which stays under it: a modal dialog named by the service, that grows in once", () => {
    const html = installedPage("ab12");
    expect(html).toContain("data-cs-body");
    expect(html).toContain('<div class="cs-scrim" data-conn="scrim" data-dialog="ab12">');
    expect(html).toContain('<div class="cs-dialog" id="cs-dialog" role="dialog" aria-modal="true" aria-labelledby="cs-dlg-title" tabindex="-1" data-trap>');
    expect(dialog(html)).toContain('<h2 class="cs-dlg-name" id="cs-dlg-title">Notion</h2>');
  });

  test("has the mark, the switch, a close button, the name, what it is and the count of its tools", () => {
    const d = dialog(installedPage("ab12"));
    expect(d).toMatch(/class="mono xl logo-mono"/);
    expect(d).toMatch(/role="switch" aria-checked="true" aria-label="開啟 Notion" data-conn="enable" data-id="ab12" data-k="cs-dlg-enable"/);
    expect(d).toMatch(/<button type="button" class="btn icon" aria-label="關閉" title="關閉" data-conn="close"/);
    expect(d).toContain("Notion的說明");
    expect(d).toContain("共 3 個工具（2 個會改動、1 個只讀取）");
  });

  test("lists 會改動 and 只讀取 as two rows with their counts, each opening its tools; no tool is listed before", () => {
    const d = dialog(installedPage("ab12"));
    expect(d).toMatch(/<div class="cs-grp"><button type="button" class="btn gr" data-conn="tools" data-kind="write" data-k="cs-kind:write"><span class="gt">會改動<em class="num">2<\/em><\/span>/);
    expect(d).toMatch(/<button type="button" class="btn gr" data-conn="tools" data-kind="read" data-k="cs-kind:read"><span class="gt">只讀取<em class="num">1<\/em><\/span>/);
    expect(d).not.toContain(">Create page<");
    // a service with only one kind of tool has only that row
    expect(dialog(installedPage("cu1")).match(/data-conn="tools"/g)).toHaveLength(1);
  });

  test("a row of tools opens them in the dialog: back, the kind and its count, a switch for them all, then a switch each", () => {
    const write = dialog(installedPage("ab12", { toolsKind: "write" }));
    expect(write).toMatch(/aria-label="返回"[^>]*data-conn="tools-back"/);
    expect(write).toContain('<h2 class="cs-dlg-name" id="cs-dlg-title">會改動<em class="num">2</em></h2>');
    expect(write).toContain(">Create page<");
    expect(write).toContain(">Delete page<");
    expect(write).not.toContain(">Search<");
    expect(write).not.toContain("共 3 個工具");
    expect(write).toContain('data-stagger="tools:write"');
    expect(write).toMatch(/aria-checked="false" aria-label="開啟工具 Delete page" data-conn="tool" data-id="ab12" data-tool="delete_page"/);
    const read = dialog(installedPage("ab12", { toolsKind: "read" }));
    expect(read).toContain(">Search<");
    expect(read).toContain('title="search"'); // the raw name stays in the tooltip
    expect(read.match(/data-conn="tool"/g)).toHaveLength(1);
    // a kind the service has no tools of shows the summary
    expect(dialog(installedPage("cu1", { toolsKind: "write" }))).toContain("共 1 個工具");
  });

  test("the switch for a whole list is on, mixed or off, a real control with a name of its own", () => {
    // Notion has two write tools, one on and one off: mixed; its only read tool is on
    expect(installedPage("ab12", { toolsKind: "write" })).toMatch(
      /<button type="button" class="tg" role="checkbox" aria-checked="mixed" aria-label="全部會改動的工具" data-conn="group" data-id="ab12" data-kind="write" data-k="cs-group"><\/button>/,
    );
    expect(installedPage("ab12", { toolsKind: "read" })).toMatch(/role="checkbox" aria-checked="true" aria-label="全部只讀取的工具" data-conn="group" data-id="ab12" data-kind="read"/);
    const allOff = { ...notes, tools: notes.tools.map((x) => ({ ...x, enabled: false })) };
    expect(installedPage("ab12", { groups: [allOff, custom], toolsKind: "write" })).toMatch(/aria-checked="false" aria-label="全部會改動的工具"/);
    expect(installedPage("ab12", { toolsKind: "write", busy: new Set(["ab12"]) })).toMatch(/data-conn="group"[^>]* disabled/);
    // no button holds another control
    expect(installedPage("ab12", { toolsKind: "write" })).not.toMatch(/<button(?:(?!<\/button>).)*<button/s);
  });

  test("names the group switches in the language of the window", () => {
    setLocale("en");
    expect(installedPage("ab12", { toolsKind: "write" })).toContain('aria-label="All tools that change things"');
    expect(installedPage("ab12", { toolsKind: "read" })).toContain('aria-label="All read-only tools"');
    setLocale("ja");
    expect(installedPage("ab12", { toolsKind: "write" })).toContain('aria-label="変更するツールすべて"');
    expect(installedPage("ab12", { toolsKind: "read" })).toContain('aria-label="読み取り専用のツールすべて"');
  });

  test("「Try it」 is the primary button, in English in every language, for a service of the store that has things to say", () => {
    expect(dialog(installedPage("ab12"))).toMatch(/<button type="button" class="btn pri" data-conn="try" data-id="ab12" data-k="cs-try">Try it<\/button>/);
    expect(examplePrompt(CATALOG[0])).toBe("跟Notion說一");
    setLocale("en");
    expect(examplePrompt(CATALOG[0])).toBe("Tell Notion one");
    expect(examplePrompt(undefined)).toBeNull();
    expect(examplePrompt({ ...CATALOG[0], examples: { "zh-TW": [], en: [], ja: [] } })).toBeNull();
    // a server of the user's own has nothing to try
    expect(dialog(installedPage("cu1"))).not.toContain('data-conn="try"');
  });

  test("ends with a sticky action row: disconnect first, secondary controls, then the primary Try it", () => {
    const d = dialog(installedPage("ab12"));
    const acts = d.slice(d.indexOf('class="action-row cs-acts is-sticky"'));
    expect(acts).toContain('class="action-destructive"');
    expect(acts.indexOf('data-conn="disconnect"')).toBeLessThan(acts.indexOf('data-conn="relogin"'));
    expect(acts.indexOf('data-conn="refresh"')).toBeLessThan(acts.indexOf('data-conn="try"'));
    expect(acts).toMatch(/class="btn sm" data-conn="relogin" data-id="ab12"[^>]*>重新登入</);
    expect(acts).toMatch(/class="btn sm" data-conn="refresh" data-id="ab12"[^>]*>重新讀取工具</);
    expect(acts).toMatch(/class="btn danger sm" data-conn="disconnect" data-id="ab12"[^>]*>中斷連線</);
    expect(installedPage("ab12", { confirmingDisconnect: "ab12" })).toMatch(/class="btn danger sm" data-conn="disconnect"[^>]*>確定中斷？</);
    // a service with no login has none to do again
    expect(dialog(installedPage("cu1"))).not.toContain('data-conn="relogin"');
  });

  test("a login that ran out is said at the top with its button; while it is open, every sign-in waits", () => {
    const out = dialog(installedPage("ab12", { groups: [{ ...notes, status: "error", error: "auth" }] }));
    expect(out).toContain("需要重新登入");
    expect(out).toMatch(/<div class="ca-alert">.*data-conn="relogin" data-id="ab12" data-k="cs-relogin-top"/);
    expect(out.match(/data-conn="relogin"/g)).toHaveLength(1);
    const waiting = installedPage("ab12", { groups: [{ ...notes, status: "error", error: "auth" }], login: { target: "ab12", name: "Notion" } });
    expect(waiting).toMatch(/data-conn="relogin"[^>]*disabled[^>]*>請在瀏覽器登入…/);
  });

  test("has that service's own latest actions, folded under a row that counts them, and the note that no content is kept", () => {
    const audit = [...AUDIT, { ...AUDIT[0], id: 2, server: "Files", tool: "list" }];
    const folded = dialog(installedPage("ab12", { audit }));
    expect(folded).toMatch(/<button type="button" class="btn gr" data-conn="audit" aria-expanded="false" data-k="cs-audit"><span class="gt">最近的動作<em class="num">1<\/em>/);
    expect(folded).not.toContain("自動執行");
    const open = dialog(installedPage("ab12", { audit, auditOpen: true }));
    expect(open).toContain('aria-expanded="true"');
    expect(open).toContain("Create page");
    expect(open).not.toContain(">List<");
    expect(open).toContain("自動執行");
    expect(open).toContain("只記錄時間、工具和結果，不記錄內容。");
    expect(dialog(installedPage("cu1"))).not.toContain("最近的動作"); // none of its own
    expect(dialog(installedPage("ab12", { audit: [] }))).not.toContain("最近的動作");
    expect(open).not.toMatch(/T[0-3]\b/);
  });

  test("has no levels, no review step and no word about pending tools", () => {
    for (const html of [installedPage("ab12", { toolsKind: "write" }), installedPage("ab12"), connectionsHTML(view()), storePage("linear")]) {
      expect(html).not.toMatch(/T[0-3]\b|決定權限|先問我|按住確認|待決定|conn-tier|conn-draft|conn-all|review/);
    }
  });

  test("a server that changed its tools shows them all the same, new ones on", () => {
    const grown: ConnectionGroup = { ...notes, tools: [...notes.tools, { name: "fresh", description: "New.", enabled: true, kind: "read" }], tool_count: 4, read_count: 2, enabled_count: 3 };
    expect(installedPage("ab12", { groups: [grown] })).toContain("共 4 個工具（2 個會改動、2 個只讀取）");
    const html = installedPage("ab12", { groups: [grown], toolsKind: "read" });
    expect(html).toMatch(/aria-checked="true" aria-label="開啟工具 Fresh"/);
    expect(html).not.toMatch(/>新<|待決定/);
  });

  test("a server of the user's own has no store words: the command and the names of its secrets", () => {
    const d = dialog(installedPage("cu1"));
    expect(d).toContain('<p class="cs-desc">自訂伺服器</p>');
    expect(d).not.toContain("由 ");
    expect(d).toContain("npx");
    expect(d).toContain("API_KEY");
    expect(d).toContain("共 1 個工具（0 個會改動、1 個只讀取）");
  });

  test("tools not read yet say how to read them", () => {
    expect(installedPage("cu1", { groups: [{ ...custom, tools: [], tool_count: 0, status: "unchecked" }] })).toContain("按「重新讀取工具」");
  });

  test("a dialog whose service is gone is the list alone", () => {
    const html = installedPage("nope");
    expect(html).toContain("data-cs-body");
    expect(html).not.toContain("cs-scrim");
  });
});

describe("商店", () => {
  const html = connectionsHTML(view({ tab: "store" }));

  test("has 熱門, then 全部 for the rest, as two columns of cards whose rows come in once", () => {
    expect(html).toContain("<h2>熱門</h2>");
    expect(html).toContain("<h2>全部</h2>");
    const popular = html.slice(html.indexOf("<h2>熱門</h2>"), html.indexOf("<h2>全部</h2>"));
    expect([...popular.matchAll(/class="nm">([^<]+)</g)].map((m) => m[1])).toEqual(["Notion", "Linear", "Canva"]);
    const all = html.slice(html.indexOf("<h2>全部</h2>"));
    expect([...all.matchAll(/class="nm">([^<]+)</g)].map((m) => m[1])).toEqual(["Sentry"]); // the rest, by name, none twice
    expect(html.match(/class="cs-grid"/g)).toHaveLength(2);
    expect(html).toContain('class="cs-grid" data-stagger="store:hot"');
    expect(html).toContain('class="cs-grid" data-stagger="store:all"');
  });

  test("a card is a container with the button that opens the page and, inside it, the button that adds, or 已安裝; no button is in a button", () => {
    const cards = html.split('<div class="cs-card">').slice(1);
    const card = cards.find((c) => c.includes('data-id="linear"'))!;
    expect(card).toMatch(/class="btn cs-main" data-conn="open-store" data-id="linear" data-k="cs-store:linear"/);
    expect(card).toContain("Linear的一句話");
    // the container holds both, and nothing is nested
    expect(html).toMatch(/<div class="cs-card"><button[^>]*cs-main[^>]*>(?:(?!<\/button>).)*<\/button><button[^>]*data-conn="join"/);
    // the mark sits bare in its own slot, as in the list
    expect(card).toMatch(/<span class="ic"><span class="mono md logo-mono"/);
    for (const c of cards) expect(c.match(/<button/g)!.length).toBeLessThanOrEqual(2);
    expect(html).not.toMatch(/<button(?:(?!<\/button>).)*<button/);
    expect(card).toMatch(/<button[^>]*class="btn sm" data-conn="join" data-id="linear"[^>]*>.*加入<\/button>/);
    const notion = cards.find((c) => c.includes('data-id="notion"'))!;
    expect(notion).toContain("已安裝");
    expect(notion).not.toContain('data-conn="join" data-id="notion"');
  });

  test("a service with no bundled mark uses a plug, and known marks keep their brand colour where it reads", () => {
    expect(html).toContain(ico("plug"));
    expect(html).not.toContain(">C<");
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
    expect(html.indexOf('data-conn="form-cancel"')).toBeLessThan(html.indexOf('data-conn="connect"'));
    expect(html).toContain('class="action-row add-acts is-sticky"');
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
    installedPage("ab12", { confirmingDisconnect: "ab12", auditOpen: true }),
    installedPage("ab12", { toolsKind: "write" }),
    installedPage("ab12", { toolsKind: "read" }),
    installedPage("ab12", { groups: [{ ...notes, status: "error", error: "auth" }] }),
    storePage("linear", { loginError: { target: "linear", kind: "denied" } }),
    connectionsHTML(view({ form: form({ pairs: [{ name: "TOKEN", value: "x" }] }) })),
  ];

  test("everything that does something is a button or a switch with a shape, and every choice is a dropdown", () => {
    const all = pages().join("");
    // A row of the list and the dimmed part around a dialog take a click too, each beside a real
    // button that does the same (the row's name, the dialog's ×).
    const acts = [...all.matchAll(/<(\w+)[^>]*data-conn="(\w+)"/g)].filter((m) => !(m[1] === "div" && (m[2] === "open" || m[2] === "scrim"))).map((m) => m[1]);
    expect(acts.length).toBeGreaterThan(30);
    expect(new Set(acts)).toEqual(new Set(["button"]));
    for (const tag of all.match(/<button[^>]*>/g)!) expect(tag).toMatch(/class="(btn[ "]|sel[ "]|tg|tab")/);
    expect(all).not.toMatch(/<a[ >]/);
    expect(all).not.toContain("<select");
    expect(all).not.toContain('type="radio"');
    expect(all).not.toContain("onclick");
  });

  test("the tabs are real buttons, not clickable text", () => {
    const tabs = connectionsHTML(view()).match(/<button[^>]*role="tab"[^>]*>/g)!;
    expect(tabs).toHaveLength(2);
    for (const tag of tabs) expect(tag).toContain('class="tab"');
  });

  test("the icon-only buttons (close, back) are named in their label and tooltip", () => {
    for (const html of [installedPage("ab12"), installedPage("ab12", { toolsKind: "write" })]) {
      for (const tag of html.match(/<button[^>]*class="btn icon"[^>]*>/g)!) expect(tag).toMatch(/aria-label="[^"]+" title="[^"]+"/);
    }
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
      expect(all).toContain(">Try it<");
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
  const host = { visible: () => true, redraw: vi.fn(), tryPrompt: vi.fn() };
  const button = (conn: string, extra: Record<string, string> = {}): Element =>
    ({ closest: () => ({ dataset: { conn, ...extra } }) }) as unknown as Element;
  const key = (k: string): KeyboardEvent => {
    const e = { key: k, target: null, defaultPrevented: false, preventDefault: vi.fn(() => void (e.defaultPrevented = true)) };
    return e as unknown as KeyboardEvent;
  };
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
    host.tryPrompt.mockReset();
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

  test("changes view, opens a page or a dialog and goes back; one whose service is gone closes", async () => {
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

  test("a store page opens the dialog of the service it was added as, over 已安裝", async () => {
    const c = createConnections(host);
    await c.load();
    c.click(button("tab", { id: "store" }));
    c.click(button("open-store", { id: "notion" }));
    c.click(button("manage", { id: "ab12" }));
    expect(c.view().open).toEqual({ tab: "installed", id: "ab12" });
    expect(c.view().tab).toBe("installed");
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

  test("the dialog opens a list of tools and goes back to its summary, and unfolds the latest actions", async () => {
    const c = createConnections(host);
    await c.load();
    c.click(button("open", { id: "ab12" }));
    expect(c.view()).toMatchObject({ toolsKind: null, auditOpen: false });
    c.click(button("tools", { kind: "read" }));
    expect(c.view().toolsKind).toBe("read");
    c.click(button("tools-back"));
    expect(c.view().toolsKind).toBeNull();
    c.click(button("audit"));
    expect(c.view().auditOpen).toBe(true);
    // opening it again starts at the summary, folded
    c.click(button("close"));
    c.click(button("open", { id: "ab12" }));
    expect(c.view()).toMatchObject({ open: { tab: "installed", id: "ab12" }, toolsKind: null, auditOpen: false });
  });

  test("the dialog controller closes with its X or Escape and leaves raw backdrop targets to shared gesture handling", async () => {
    const c = createConnections(host);
    await c.load();
    c.click(button("open", { id: "ab12" }));
    c.click(button("close"));
    expect(c.view().open).toBeNull();
    c.click(button("open", { id: "ab12" }));
    const esc = key("Escape");
    expect(c.key(esc)).toBe(true);
    expect(esc.preventDefault).toHaveBeenCalled();
    expect(c.view().open).toBeNull();
    // Esc with no dialog is the panel's (it closes the settings)
    expect(c.key(key("Escape"))).toBe(false);
    c.click(button("open", { id: "ab12" }));
    const scrim: { dataset: { conn: string }; closest?: () => unknown } = { dataset: { conn: "scrim" } };
    scrim.closest = () => scrim;
    // a click inside the dialog on no control finds the dimmed part around it, and does nothing
    expect(c.click({ closest: () => scrim } as unknown as Element)).toBe(true);
    expect(c.view().open).not.toBeNull();
    // A raw target cannot establish where the pointer press began.
    expect(c.click(scrim as unknown as Element)).toBe(true);
    expect(c.view().open).not.toBeNull();
    c.click(button("close"));
    expect(c.view().open).toBeNull();
  });

  test("「試試看」 hands the service's first example, in the window's language, to the 拉斐爾 page", async () => {
    const c = createConnections(host);
    await c.load();
    c.click(button("open", { id: "ab12" }));
    c.click(button("try", { id: "ab12" }));
    expect(host.tryPrompt).toHaveBeenCalledWith("跟Notion說一");
    setLocale("ja");
    c.click(button("try", { id: "ab12" }));
    expect(host.tryPrompt).toHaveBeenLastCalledWith("Notionに一");
    // a server of the user's own has none
    c.click(button("try", { id: "cu1" }));
    expect(host.tryPrompt).toHaveBeenCalledTimes(2);
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

  test("a service whose login ran out signs in again from its dialog, which stays open", async () => {
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
    expect(c.view()).toMatchObject({ tab: "installed", open: null, toolsKind: null, auditOpen: false, form: null, confirmingDisconnect: null, search: "" });
    expect(c.view().login).not.toBeNull();
    expect(JSON.stringify(c.view())).not.toContain("TOPSECRET");
  });

  test("ignores a click that is none of its own", () => {
    const c = createConnections(host);
    expect(c.click({ closest: () => null } as unknown as Element)).toBe(false);
  });
});

test("unknown connections use a plug at both sizes without exposing the name as markup or initials", () => {
  for (const size of ["md", "xl"] as const) {
    for (const name of ["Files", "\u7b46\u8a18", "<img src=x onerror=alert(1)>", ""]) {
      expect(iconHTML(name, null, size)).toBe(`<span class="mono ${size}" aria-hidden="true">${ico("plug")}</span>`);
    }
  }
});
