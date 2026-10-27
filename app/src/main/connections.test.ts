import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { setLocale } from "./i18n";
import type { SkillItem } from "./connection-skills";
import type { AppsInventory, AuditEntry, ConnectionGroup, InventoryApp, StoreService } from "./types";

const api = vi.hoisted(() => ({
  connectorsStatus: vi.fn(), connectorsCatalog: vi.fn(), connectorsCategories: vi.fn(), connectorsTools: vi.fn(),
  connectorsSetTools: vi.fn(), connectorsConnect: vi.fn(), connectorsGoogleConnect: vi.fn(), connectorsDisconnect: vi.fn(), connectorsSetKey: vi.fn(),
  toolsetsList: vi.fn(),
  integrationsCatalog: vi.fn(),
  integrationsAudit: vi.fn(),
  integrationsConnect: vi.fn(),
  integrationsConnectCancel: vi.fn(),
  integrationsAdd: vi.fn(),
  integrationsUpdate: vi.fn(),
  integrationsTest: vi.fn(),
  integrationsRefresh: vi.fn(),
  integrationsSetEnabled: vi.fn(),
  integrationsDisconnect: vi.fn(),
  integrationsSetToolEnabled: vi.fn(),
  integrationsSetToolsEnabled: vi.fn(),
  integrationsInventory: vi.fn(),
  membersList: vi.fn(), membersSkillsList: vi.fn(), membersSkillsRead: vi.fn(), membersSkillsEdit: vi.fn(), membersSkillsUpdate: vi.fn(), membersSkillsDelete: vi.fn(),
}));
vi.mock("./api", () => api);
vi.mock("./ui", async (original) => ({ ...(await original<typeof import("./ui")>()), toaster: { info: vi.fn() } }));
vi.mock("./member-dialogs", async (original) => ({ ...(await original<typeof import("./member-dialogs")>()), confirmDelete: vi.fn(async () => true) }));

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
/** What the core counts of the two services above: both can be called now. */
const app = (id: string, name: string, over: Partial<InventoryApp> = {}): InventoryApp => ({ id, name, enabled: true, state: "ready", tools: 2, connected: true, ...over });
const inventoryOf = (apps: InventoryApp[]): AppsInventory => ({ state: "complete", count: apps.filter((a) => a.connected).length, apps });
const INVENTORY = inventoryOf([app("ab12", "Notion"), app("cu1", "Files", { tools: 1 })]);
const SKILLS: SkillItem[] = [
  { member: { id: 1, name: "Ada" }, skill: { id: 7, name: "summary-style", description: "Use when the owner asks for a summary", enabled: true, source: "owner/repo" } },
  { member: { id: 2, name: "Ben" }, skill: { id: 8, name: "meeting-notes", description: "Use after a meeting to list owners", enabled: false, source: "owner/notes" } },
];

const view = (over: Partial<ConnectionsView> = {}): ConnectionsView => ({
  ...emptyConnectionsView(), groups: GROUPS, catalog: CATALOG, inventory: INVENTORY, skills: [], audit: AUDIT, now: NOW, ...over,
});
const form = (over: Partial<ConnectionForm> = {}): ConnectionForm => ({
  type: "stdio", name: "", command: "", args: "", url: "", pairLines: "", busy: false, error: null, ...over,
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
    expect(bar).toMatch(/<button[^>]*class="tab"[^>]*role="tab"[^>]*aria-selected="true"[^>]*data-id="installed"[^>]*>已連結<span class="n">2<\/span>/);
    expect(bar).toMatch(/<button[^>]*class="tab"[^>]*role="tab"[^>]*aria-selected="false"[^>]*data-id="store"[^>]*>商店<span class="n">4<\/span>/);
    expect(bar).toContain('id="conn-search"');
    expect(connectionsHTML(view({ tab: "store" }))).toMatch(/aria-selected="true"[^>]*data-id="store"/);
    // nothing connected yet still says so: 0
    expect(connectionsHTML(view({ groups: [], inventory: inventoryOf([]) }))).toMatch(/data-id="installed"[^>]*>已連結<span class="n">0<\/span>/);
  });

  test("the number beside 已連結 is the core's count, not the rows: an app that waits, failed or duplicates another is a row and no count", () => {
    const waiting: ConnectionGroup = { ...notes, id: "w1", name: "Slack", status: "error", error: "auth" };
    const inventory = inventoryOf([app("ab12", "Notion"), app("cu1", "Files", { tools: 1 }), app("w1", "Slack", { state: "needs_sign_in", connected: false, tools: 0 })]);
    const html = connectionsHTML(view({ groups: [...GROUPS, waiting], inventory }));
    expect(html).toMatch(/data-id="installed"[^>]*>已連結<span class="n">2<\/span>/);
    expect(html.match(/data-conn="open" data-id=/g)!.length / 2).toBe(3); // each row names its id twice: the row and its button
    // while it is read there is no number, and when it cannot be read the tab says so instead of showing one
    expect(connectionsHTML(view({ inventory: "loading" }))).toMatch(/data-id="installed"[^>]*>已連結<\/button>/);
    expect(connectionsHTML(view({ inventory: "failed" }))).toMatch(/data-id="installed"[^>]*>已連結<span class="n">—<\/span>/);
    expect(connectionsHTML(view({ inventory: { state: "partial", count: null, apps: INVENTORY.apps } }))).toMatch(/data-id="installed"[^>]*>已連結<span class="n">—<\/span>/);
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
  const items = (out: string): string[] => out.split(/<div class="cs-item(?: mcp-item)?"/).slice(1);

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
    const remote = connectionsHTML(view({ groups: [{ ...custom, transport: "http", url: "https://example.com/mcp" }] }));
    expect(remote).toContain('class="ds">https://example.com/mcp</span>');
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

  test("empty connections and unmatched searches have concise empty states", () => {
    expect(connectionsHTML(view({ groups: [] }))).toContain("尚無連線");
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

describe("the apps that cannot be called, and the private skills", () => {
  const slack: ConnectionGroup = { ...notes, id: "w1", name: "Slack", status: "error", error: "auth", description: "Slack的一句話" };
  const twin: ConnectionGroup = { ...notes, id: "d1", name: "Notion 2", description: "Notion again" };
  const empty: ConnectionGroup = { ...custom, id: "e1", name: "Empty" };
  const off: ConnectionGroup = { ...custom, id: "o1", name: "Notes", enabled: false, status: "disabled", env: [], args: ["-y", "notes"] };
  const inventory = inventoryOf([
    app("ab12", "Notion"), app("cu1", "Files", { tools: 1 }), app("w1", "Slack", { state: "needs_sign_in", connected: false, tools: 0 }),
    app("d1", "Notion 2", { state: "duplicate", connected: false }), app("e1", "Empty", { state: "no_tools", connected: false, tools: 0 }),
    app("o1", "Notes", { state: "disabled", enabled: false, connected: false, tools: 0 }),
  ]);
  const all = view({ groups: [notes, custom, slack, twin, empty, off], inventory });

  test("the apps that can be called now come first; the others follow under their own heading, each with its switch and its dialog", () => {
    const html = bodyHTML(all);
    const [now, later] = html.split(`<h2>${"尚未連結"}</h2>`);
    expect(now).toContain("<h2>應用程式</h2>");
    expect([...now!.matchAll(/class="nm">([^<]+)</g)].map((m) => m[1])).toEqual(["Notion", "Files"]);
    expect([...later!.matchAll(/class="nm">([^<]+)</g)].map((m) => m[1])).toEqual(["Slack", "Notion 2", "Empty", "Notes"]);
    expect(later).toContain('data-stagger="installed-not"');
    // why each one is not one of the connected: a sign-in is waited for, a second way to an app that is counted, every tool off
    expect(later).toContain("需要重新登入");
    expect(later).toContain("與另一個連線重複");
    expect(later).toContain("工具已全部關閉");
    // one that is switched off says it with its switch, and keeps the words it always had
    expect(later).toContain("npx -y notes");
    expect(later).toMatch(/aria-checked="false" aria-label="開啟 Notes"/);
    expect(later!.match(/data-conn="enable"/g)).toHaveLength(4);
    // with nothing to separate there is no heading: the list of apps is what it was
    expect(bodyHTML(view())).not.toContain("<h2>");
  });

  test("while the count is not read, or cannot be, the apps are one list as before", () => {
    for (const inv of ["loading", "failed"] as const) {
      const html = bodyHTML(view({ groups: [notes, slack], inventory: inv }));
      expect(html.match(/class="cs-list"/g)).toHaveLength(1);
      expect(html).not.toContain("尚未連結");
    }
  });

  test("an inventory that is not whole says which app it could not read, and counts none of them", () => {
    const partial = { state: "partial" as const, count: null, apps: [app("ab12", "Notion", { state: "unknown", connected: false, tools: 0 })] };
    const html = connectionsHTML(view({ groups: [notes], inventory: partial }));
    expect(html).toMatch(/data-id="installed"[^>]*>已連結<span class="n">—<\/span>/);
    expect(html).toContain("無法讀取");
  });

  test("the skills are under their own heading, a row each: the member's face, the name, who and when, a switch and a chevron", () => {
    const html = bodyHTML(view({ skills: SKILLS }));
    expect(html).toContain("<h2>應用程式</h2>"); // the apps need a name once there is another list
    const skills = html.slice(html.indexOf("<h2>私人技能</h2>"));
    expect(skills).toContain('data-stagger="skills"');
    const rows = skills.split('<div class="cs-item cs-skill"').slice(1);
    expect(rows).toHaveLength(2);
    expect(rows[0]).toMatch(/^ data-conn="skill-open" data-member="1" data-id="7">/);
    expect(rows[0]).toMatch(/<button type="button" class="btn cs-open" data-conn="skill-open" data-member="1" data-id="7" data-k="cs-skill:1:7" aria-haspopup="dialog" title="查看技能 summary-style">/);
    expect(rows[0]).toContain('<span class="nm">summary-style</span>');
    expect(rows[0]).toContain('<span class="ds">Ada · Use when the owner asks for a summary</span>');
    expect(rows[0]).toContain('class="av mb-av');
    expect(rows[0]).toMatch(/class="tg" role="switch" aria-checked="true" aria-label="開啟技能 summary-style" data-conn="skill-enable" data-member="1" data-id="7"/);
    expect(rows[1]).toMatch(/aria-checked="false" aria-label="開啟技能 meeting-notes"/);
    expect(rows[0]!.match(/<button/g)).toHaveLength(2); // the row's name and the switch: no button in a button
    expect(rows[0]).toMatch(/<span class="chev" aria-hidden="true"><svg/);
  });

  test("a search finds a skill by its name, its words and its member; a skill that cannot be read is told where it would be", () => {
    expect(bodyHTML(view({ skills: SKILLS, search: "meeting" }))).toContain("meeting-notes");
    expect(bodyHTML(view({ skills: SKILLS, search: "meeting" }))).not.toContain("summary-style");
    expect(bodyHTML(view({ skills: SKILLS, search: "Ada" }))).toContain("summary-style");
    expect(bodyHTML(view({ groups: [], skills: SKILLS }))).toContain("summary-style"); // skills alone are no empty page
    const failed = bodyHTML(view({ skills: "failed" }));
    expect(failed).toContain("無法讀取技能。");
    expect(failed).toContain('data-conn="retry-skills"');
    expect(bodyHTML(view({ groups: [], skills: [] }))).toContain("尚無連線");
  });

  test("the page of a skill is the dialog of an app: its name, who has it and when it is used, its instructions and 刪除技能 on the left", () => {
    const document = "---\nname: summary-style\ndescription: Use when the owner asks for a summary\n---\nStart <b>with</b> the verdict.";
    const page = { memberId: 1, id: 7, saved: document, draft: document, state: "ready" as const, busy: false, error: null };
    const html = connectionsHTML(view({ skills: SKILLS, skill: page }));
    const d = html.slice(html.indexOf('<div class="cs-scrim"'));
    expect(d).toContain('<div class="cs-scrim" data-conn="scrim" data-dialog="skill:1:7">');
    expect(d).toContain('<div class="cs-dialog wide" id="cs-dialog" role="dialog" aria-modal="true" aria-labelledby="cs-dlg-title" tabindex="-1" data-trap>');
    expect(d).toContain('<h2 class="cs-dlg-name" id="cs-dlg-title">summary-style</h2>');
    expect(d).toContain('<p class="cs-desc">Ada · Use when the owner asks for a summary</p>');
    expect(d).toMatch(/role="switch" aria-checked="true" aria-label="開啟技能 summary-style" data-conn="skill-enable" data-member="1" data-id="7" data-k="cs-skill-dlg-enable"/);
    expect(d).toMatch(/<button type="button" class="btn icon" aria-label="關閉" title="關閉" data-conn="close"/);
    // the document is the text of the field, written out as text: nothing in it is markup
    expect(d).toMatch(/<textarea id="skill-text" class="field cs-skill-text" rows="14" spellcheck="false" data-k="cs-skill-text">---\nname: summary-style/);
    expect(d).toContain("Start &lt;b&gt;with&lt;/b&gt; the verdict.");
    // the action row: delete on the left (the destructive one first), save the primary one; nothing to save until it changes
    const acts = d.slice(d.indexOf('class="action-row cs-acts is-sticky"'));
    expect(acts.indexOf('data-conn="skill-delete"')).toBeLessThan(acts.indexOf('data-conn="skill-save"'));
    expect(acts).toContain('<span class="action-destructive"><button type="button" class="btn danger" data-conn="skill-delete"');
    expect(acts).toMatch(/class="btn pri" data-conn="skill-save" data-member="1" data-id="7" data-k="cs-skill-save" disabled>儲存</);
    const edited = connectionsHTML(view({ skills: SKILLS, skill: { ...page, draft: document + "\nMore." } }));
    expect(edited).toMatch(/class="btn pri" data-conn="skill-save" data-member="1" data-id="7" data-k="cs-skill-save">儲存</);
    expect(edited).not.toMatch(/data-k="cs-skill-save" disabled/);
    // it is on top of the list, which stays under it; no other dialog is drawn
    expect(html).toContain("data-cs-body");
    expect(d.match(/role="dialog"/g)).toHaveLength(1);
  });

  test("a refusal is said under the document in the words of the window; the document is waited for, or cannot be had", () => {
    const base = { memberId: 1, id: 7, saved: "a", draft: "a", state: "ready" as const, busy: false, error: null };
    const refused = connectionsHTML(view({ skills: SKILLS, skill: { ...base, error: "這位成員已有同名的技能。" } }));
    expect(refused).toMatch(/<div class="note" role="alert">.*這位成員已有同名的技能。/);
    const busy = connectionsHTML(view({ skills: SKILLS, skill: { ...base, draft: "b", busy: true } }));
    expect(busy).toMatch(/<textarea[^>]* disabled>/);
    expect(busy).toMatch(/data-conn="skill-delete"[^>]* disabled/);
    expect(connectionsHTML(view({ skills: SKILLS, skill: { ...base, state: "loading", saved: null } }))).toContain("讀取中");
    const failed = connectionsHTML(view({ skills: SKILLS, skill: { ...base, state: "failed", saved: null } }));
    expect(failed).toContain("無法讀取這個技能。");
    expect(failed).toContain('data-conn="skill-retry"');
    expect(failed).not.toContain("<textarea");
  });

  test("a page whose skill is gone is the list alone", () => {
    const html = connectionsHTML(view({ skills: [], skill: { memberId: 1, id: 7, saved: "a", draft: "a", state: "ready", busy: false, error: null } }));
    expect(html).toContain("data-cs-body");
    expect(html).not.toContain("cs-scrim");
  });

  test("the dialog of an app says its account: a row for each account of a managed one, the owner's own Google, a signed-in server", () => {
    const managed: ConnectionGroup = { ...notes, source: "composio", accounts: [{ status: "ACTIVE" }, { status: "EXPIRED" }] };
    const d = installedPage("ab12", { groups: [managed, custom] }).split('<div class="cs-scrim"')[1]!;
    expect(d).toMatch(/<div class="cs-acct">.*帳號 1<\/span><span class="st"><span class="dot ok"><\/span>已連結<\/span><\/div><div class="cs-acct">.*帳號 2<\/span><span class="st err"><span class="dot err"><\/span>已過期/);
    const google: ConnectionGroup = { ...notes, id: "google", source: "google", accounts: [{ status: "ACTIVE" }] };
    expect(installedPage("google", { groups: [google] })).toContain("你的 Google 帳號");
    // never an account's name or address: the core does not give one, and the page invents none
    expect(d).not.toMatch(/@|\bca_/);
    // a server that is signed in to has one row; one that needs no login has none
    expect(installedPage("ab12", { groups: [notes] }).split('<div class="cs-scrim"')[1]).toContain("已登入");
    expect(installedPage("cu1")).not.toContain('class="cs-acct"');
    // after it signs in again the old account may still be there: both are told, and the app is still one
    expect(installedPage("ab12", { groups: [{ ...managed, status: "error", error: "auth" }] })).toContain("需要重新登入");
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
    // how many of its tools are on, of all it has: Delete page is off
    expect(d).toContain("已開啟 2/3 個工具（2 個會改動、1 個只讀取）");
  });

  test("groups tools inline under write and read headings with individual switches", () => {
    const d = dialog(installedPage("ab12"));
    expect(d).toContain('class="cs-plugin-tools"');
    expect(d.match(/data-conn="tool"/g)).toHaveLength(3);
    expect(d).toContain(">Create page<");
    expect(d).toContain(">Search<");
    expect(d).not.toContain('data-conn="tools"');
    expect(dialog(installedPage("cu1")).match(/data-conn="tool"/g)).toHaveLength(1);
  });

  test("a row of tools opens them in the dialog: back, the kind and its count, a switch for them all, then a switch each", () => {
    const write = dialog(installedPage("ab12", { toolsKind: "write" }));
    expect(write).toMatch(/aria-label="返回"[^>]*data-conn="tools-back"/);
    expect(write).toContain('<h2 class="cs-dlg-name" id="cs-dlg-title">會改動<em class="num">2</em></h2>');
    expect(write).toContain(">Create page<");
    expect(write).toContain(">Delete page<");
    expect(write).not.toContain(">Search<");
    expect(write).not.toContain("個工具（");
    expect(write).toContain('data-stagger="tools:write"');
    expect(write).toMatch(/aria-checked="false" aria-label="開啟工具 Delete page" data-conn="tool" data-id="ab12" data-tool="delete_page"/);
    const read = dialog(installedPage("ab12", { toolsKind: "read" }));
    expect(read).toContain(">Search<");
    expect(read).toContain('title="search"'); // the raw name stays in the tooltip
    expect(read.match(/data-conn="tool"/g)).toHaveLength(1);
    // a kind the service has no tools of shows the summary
    expect(dialog(installedPage("cu1", { toolsKind: "write" }))).toContain("已開啟 1/1 個工具");
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

  test("the store service's primary try button is localized in a Chinese window", () => {
    expect(dialog(installedPage("ab12"))).toMatch(/<button type="button" class="btn pri" data-conn="try" data-id="ab12" data-k="cs-try">試用<\/button>/);
    expect(examplePrompt(CATALOG[0])).toBe("跟Notion說一");
    setLocale("en");
    expect(dialog(installedPage("ab12"))).toContain('data-k="cs-try">Try it</button>');
    expect(examplePrompt(CATALOG[0])).toBe("Tell Notion one");
    expect(examplePrompt(undefined)).toBeNull();
    expect(examplePrompt({ ...CATALOG[0], examples: { "zh-TW": [], en: [], ja: [] } })).toBeNull();
    // a server of the user's own has nothing to try
    expect(dialog(installedPage("cu1"))).not.toContain('data-conn="try"');
  });

  test("keeps disconnect in the detail overflow and secondary controls before Try it", () => {
    const d = dialog(installedPage("ab12"));
    const acts = d.slice(d.indexOf('class="action-row cs-acts is-sticky"'));
    expect(d).toContain('data-conn="server-menu"');
    expect(d).not.toContain('data-conn="disconnect"');
    expect(acts.indexOf('data-conn="refresh"')).toBeLessThan(acts.indexOf('data-conn="try"'));
    expect(acts).toMatch(/class="btn sm" data-conn="relogin" data-id="ab12"[^>]*>重新登入</);
    expect(acts).toMatch(/class="btn sm" data-conn="refresh" data-id="ab12"[^>]*>重新讀取工具</);
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
    expect(installedPage("ab12", { groups: [grown] })).toContain("已開啟 3/4 個工具（2 個會改動、2 個只讀取）"); // Delete page is still off
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
    expect(d).toContain("已開啟 1/1 個工具（0 個會改動、1 個只讀取）");
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
  test("takes the place of the tab with a transport dropdown, multiline fields and test and save actions", () => {
    const html = connectionsHTML(view({ form: form() }));
    expect(html).toContain("新增 MCP 伺服器");
    expect(html).toContain('data-k="conn-type"');
    expect(html).not.toContain('role="tablist"');
    expect(html).toContain('id="conn-args" rows="4"');
    expect(html).toContain('id="conn-command"');
    expect(html).toContain("只接你信任的程式。");
    expect(html).not.toContain('class="cd-help"');
    expect(html).toContain('data-conn="connect"');
    expect(html).toContain('data-conn="form-cancel"');
    expect(html.indexOf('data-conn="form-cancel"')).toBeLessThan(html.indexOf('data-conn="connect"'));
    expect(html).toContain('class="action-row add-acts is-sticky"');
    expect(html).toContain('aria-haspopup="menu"');
    expect(html).not.toContain('role="dialog"');
  });

  test("an address and a header for the network type; the values of secrets are never printed", () => {
    const f = form({ id: "remote", type: "http", name: "Remote", url: "https://example.com/mcp", pairLines: "Authorization=" });
    const html = connectionsHTML(view({ form: f }));
    expect(html).toContain('id="conn-url"');
    expect(html).not.toContain('id="conn-command"');
    expect(html).toContain("Authorization");
    expect(html).toContain("標頭");
    expect(html).not.toContain("TOPSECRET");
    expect(html).toContain('id="conn-pairs"');
    expect(html).toContain('id="conn-limit"');
  });
});

describe("the rules for this tab", () => {
  // Slack waits for its sign-in (two accounts, one expired); the skills of two members are beside the apps
  const slack: ConnectionGroup = { ...notes, id: "w1", name: "Slack", source: "composio", status: "error", error: "auth", accounts: [{ status: "EXPIRED" }, { status: "ACTIVE" }] };
  const more = {
    groups: [...GROUPS, slack],
    inventory: inventoryOf([app("ab12", "Notion"), app("cu1", "Files", { tools: 1 }), app("w1", "Slack", { state: "needs_sign_in", connected: false, tools: 0 })]),
    skills: SKILLS,
  };
  const document = "---\nname: summary-style\ndescription: Use when the owner asks for a summary\n---\nStart with the verdict.";
  const pages = (): string[] => [
    connectionsHTML(view()),
    connectionsHTML(view(more)),
    installedPage("w1", more),
    connectionsHTML(view({ ...more, skill: { memberId: 1, id: 7, saved: document, draft: document + " Edited.", state: "ready", busy: false, error: null } })),
    connectionsHTML(view({ tab: "store" })),
    connectionsHTML(view({ tab: "store", login: { target: "linear", name: "Linear" } })),
    installedPage("ab12", { confirmingDisconnect: "ab12", auditOpen: true }),
    installedPage("ab12", { toolsKind: "write" }),
    installedPage("ab12", { toolsKind: "read" }),
    installedPage("ab12", { groups: [{ ...notes, status: "error", error: "auth" }] }),
    storePage("linear", { loginError: { target: "linear", kind: "denied" } }),
    connectionsHTML(view({ form: form({ pairLines: "TOKEN=" }) })),
  ];

  test("everything that does something is a button or a switch with a shape, and every choice is a dropdown", () => {
    const all = pages().join("");
    // A row of the list and the dimmed part around a dialog take a click too, each beside a real
    // button that does the same (the row's name, the dialog's ×).
    const acts = [...all.matchAll(/<(\w+)[^>]*data-conn="([\w-]+)"/g)].filter((m) => !(m[1] === "div" && ["open", "skill-open", "scrim"].includes(m[2]!))).map((m) => m[1]);
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
    for (const zh of ["已安裝", "已連結", "商店", "加入", "中斷連線", "熱門", "只讀取", "會改動", "請在瀏覽器登入", "重新登入", "重新讀取工具", "最近的動作", "沒有符合", "技能", "尚未連結", "帳號"]) expect(all).not.toContain(zh);
    if (locale === "en") {
      // what the service itself says (here the fake store's own words) is the catalog's, in English
      expect(all).toContain("Installed");
      expect(all).toContain(">Connected<");
      expect(all).toContain("Sign in in your browser…");
      expect(all).toContain("2/3 tools on (2 write, 1 read)");
      expect(all).toContain("Tell Linear one");
      expect(all).toContain(">Try it<");
      expect(all).toContain("Private skills");
      expect(all).toContain("Not connected");
      expect(all).toContain("Account 1");
      expect(all).not.toMatch(CJK);
    } else {
      expect(all).toContain("接続済み");
      expect(all).toContain("ブラウザでログインしてください…");
      expect(all).toContain("Linearに一");
      expect(all).toContain("プライベートスキル");
      expect(all).toContain("未接続");
      expect(all).toContain("アカウント 1");
      expect(all).not.toMatch(/Installed|Popular|Sign in|Contains|Recent actions|Disconnect|Private skills|Not connected|Account/);
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
    api.connectorsStatus.mockResolvedValue({ connectors: [], composio_configured: true, google_configured: false });
    api.connectorsCatalog.mockResolvedValue({ items: CATALOG.map(s => ({ slug: s.id, name: s.name, description: s.summary["zh-TW"], logo: "", categories: [], can_connect: true, no_auth: false })) });
    api.connectorsCategories.mockResolvedValue({ items: [] });
    api.integrationsCatalog.mockResolvedValue({ services: CATALOG });
    api.integrationsAudit.mockResolvedValue({ entries: AUDIT });
    api.integrationsInventory.mockResolvedValue(INVENTORY);
    api.membersList.mockResolvedValue({ members: [{ id: 0, name: "拉斐爾" }, { id: 1, name: "Ada" }, { id: 2, name: "Ben" }], groups: [] });
    api.membersSkillsList.mockImplementation(async (id: number) => ({ skills: SKILLS.filter((item) => item.member.id === id).map((item) => item.skill) }));
    for (const name of ["integrationsRefresh", "integrationsSetEnabled", "integrationsSetToolEnabled"] as const) api[name].mockResolvedValue({});
    api.integrationsDisconnect.mockResolvedValue({ removed: true, revoked: true });
    api.integrationsConnectCancel.mockResolvedValue({ cancelled: true });
    host.redraw.mockReset();
    host.tryPrompt.mockReset();
  });

  test("reads the services, the store, the latest actions and the core's count, and starts on 已連結", async () => {
    const c = createConnections(host);
    expect(c.view().groups).toBe("loading");
    expect(c.view().inventory).toBe("loading");
    await c.load();
    expect(c.view().groups).toEqual(GROUPS);
    expect((c.view().catalog as StoreService[]).map(s => s.id)).toEqual(CATALOG.map(s => s.id));
    expect(c.view().audit).toEqual(AUDIT);
    expect(c.view().inventory).toEqual(INVENTORY);
    expect(c.view().tab).toBe("installed");
  });

  test("the count is the core's: read with the services, unknown when it cannot be read, and read again by every change", async () => {
    api.integrationsInventory.mockRejectedValueOnce("down");
    const c = createConnections(host);
    await c.load();
    expect(c.view().inventory).toBe("failed");
    expect(c.view().groups).toEqual(GROUPS); // the list is there; only the number is not
    await c.load();
    expect(c.view().inventory).toEqual(INVENTORY);
    // a switch, a sign-in, an uninstall: the services and the count are read again together
    api.integrationsInventory.mockResolvedValue(inventoryOf([app("cu1", "Files", { tools: 1 })]));
    c.followEvent({ type: "ConnectionsChanged" });
    await settle();
    expect((c.view().inventory as AppsInventory).count).toBe(1);
  });

  test("reads the private skills of every member but Raphael, and again when a skill changes anywhere", async () => {
    const c = createConnections(host);
    expect(c.view().skills).toBe("loading");
    await c.load();
    await settle();
    expect(api.membersSkillsList.mock.calls.map(([id]) => id)).toEqual([1, 2]); // Raphael has none to ask for
    expect((c.view().skills as SkillItem[]).map((item) => [item.member.name, item.skill.name])).toEqual([["Ada", "summary-style"], ["Ben", "meeting-notes"]]);
    api.membersSkillsList.mockResolvedValue({ skills: [] });
    c.followEvent({ type: "MembersChanged" });
    await settle();
    expect(c.view().skills).toEqual([]);
    // a skill list that cannot be read is told where the skills would be, and keeps the old ones when a later read fails
    api.membersList.mockRejectedValueOnce("down");
    c.followEvent({ type: "MembersChanged" });
    await settle();
    expect(c.view().skills).toEqual([]);
    const fresh = createConnections(host);
    api.membersList.mockRejectedValueOnce("down");
    await fresh.load();
    await settle();
    expect(fresh.view().skills).toBe("failed");
  });

  test("edits a custom server with blank existing secrets, tests without saving, then persists the draft", async () => {
    api.integrationsTest.mockResolvedValue({ ok: true, tools: ["list"] });
    api.integrationsUpdate.mockResolvedValue({ server: custom });
    const c = createConnections(host);
    await c.load();
    c.click(button("edit-server", { id: "cu1" }));
    expect(c.view().form).toMatchObject({ id: "cu1", pairLines: "API_KEY=", command: "npx", limit: 8000 });
    c.input(input("conn-command", "uvx"));
    c.input(input("conn-limit", "2400"));
    c.click(button("test-draft")); await settle();
    expect(api.integrationsTest).toHaveBeenCalledWith(expect.objectContaining({ id: "cu1", command: "uvx", env: [{ name: "API_KEY", value: "" }], result_size_limit: 2400 }));
    expect(api.integrationsUpdate).not.toHaveBeenCalled();
    expect(c.view().form).not.toBeNull();
    c.click(button("connect")); await settle();
    expect(api.integrationsUpdate).toHaveBeenCalledWith("cu1", expect.objectContaining({ command: "uvx", result_size_limit: 2400 }));
    expect(c.view().form).toBeNull();
    c.click(button("test-server", { id: "cu1" })); await settle();
    expect(api.integrationsTest).toHaveBeenLastCalledWith({ id: "cu1" });
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
    api.connectorsStatus.mockRejectedValueOnce("down");
    const c = createConnections(host);
    await c.load();
    expect(c.view().groups).toBe("failed");
    expect(c.view().catalog).toBe("failed");
    await c.load();
    api.toolsetsList.mockRejectedValueOnce("down");
    api.connectorsStatus.mockRejectedValueOnce("down");
    await c.load();
    expect(c.view().groups).toEqual(GROUPS);
    expect((c.view().catalog as StoreService[]).map(s => s.id)).toEqual(CATALOG.map(s => s.id));
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

  describe("a private skill", () => {
    const DOC = "---\nname: summary-style\ndescription: Use when the owner asks for a summary\n---\nStart with the verdict.";
    const open = async () => {
      api.membersSkillsRead.mockResolvedValue({ skill: SKILLS[0]!.skill, text: DOC });
      const c = createConnections(host);
      await c.load();
      await settle();
      c.click(button("skill-open", { member: "1", id: "7" }));
      return c;
    };

    test("opens with its document read, and the page is the one skill of that member", async () => {
      const c = createConnections(host);
      await c.load();
      await settle();
      let release!: (answer: { skill: unknown; text: string }) => void;
      api.membersSkillsRead.mockReturnValue(new Promise((resolve) => { release = resolve; }));
      c.click(button("skill-open", { member: "1", id: "7" }));
      expect(c.view().skill).toMatchObject({ memberId: 1, id: 7, state: "loading", saved: null });
      release({ skill: SKILLS[0]!.skill, text: DOC });
      await settle();
      expect(api.membersSkillsRead).toHaveBeenCalledWith(1, 7);
      expect(c.view().skill).toMatchObject({ state: "ready", saved: DOC, draft: DOC, busy: false, error: null });
      expect(c.view().open).toBeNull(); // the page of a skill is not the dialog of an app
      api.membersSkillsRead.mockRejectedValueOnce("gone");
      c.click(button("skill-retry"));
      await settle();
      expect(c.view().skill!.state).toBe("failed");
    });

    test("is edited where it stands (no page drawn again) and kept as typed; the name and the words follow the document", async () => {
      const c = await open();
      await settle();
      const save = { disabled: true };
      vi.stubGlobal("document", { querySelector: () => save });
      try {
        expect(c.input(input("skill-text", DOC))).toBe(true);
        expect(save.disabled).toBe(true); // nothing changed: nothing to keep
        host.redraw.mockReset();
        expect(c.input(input("skill-text", DOC.replace("verdict", "answer")))).toBe(true);
        expect(save.disabled).toBe(false);
        expect(host.redraw).not.toHaveBeenCalled();
      } finally {
        vi.unstubAllGlobals();
      }
      const edited = DOC.replace("verdict", "answer");
      api.membersSkillsEdit.mockResolvedValue({ skill: { ...SKILLS[0]!.skill, name: "answer-style" } });
      api.membersSkillsList.mockImplementation(async (id: number) => ({ skills: id === 1 ? [{ ...SKILLS[0]!.skill, name: "answer-style" }] : [SKILLS[1]!.skill] }));
      c.click(button("skill-save", { member: "1", id: "7" }));
      await settle();
      expect(api.membersSkillsEdit).toHaveBeenCalledWith(1, 7, edited);
      expect(c.view().skill).toMatchObject({ saved: edited, draft: edited, busy: false, error: null });
      expect(lastToast()).toBe("已儲存技能");
      expect((c.view().skills as SkillItem[]).map((item) => item.skill.name)).toEqual(["answer-style", "meeting-notes"]);
      // saving what was saved is nothing
      c.click(button("skill-save", { member: "1", id: "7" }));
      await settle();
      expect(api.membersSkillsEdit).toHaveBeenCalledTimes(1);
    });

    test("a document the core refuses stays as it was typed, with the reason in the words of the window", async () => {
      const c = await open();
      await settle();
      c.input(input("skill-text", "no frontmatter"));
      const refusals: [string, string][] = [
        ["[-32602] Invalid params: a skill with that name already exists", "這位成員已有同名的技能。"],
        ["[-32602] Invalid params: name must not be empty", "文件開頭要有 name 與 description。"],
        ["[-32602] Invalid params: skill document is too large", "文件太大。"],
        ["[-32602] Invalid params: skill must be a UTF-8 Markdown document", "技能必須是 UTF-8 的 Markdown 文件。"],
      ];
      for (const [message, words] of refusals) {
        api.membersSkillsEdit.mockRejectedValueOnce(message);
        c.click(button("skill-save", { member: "1", id: "7" }));
        await settle();
        expect(c.view().skill).toMatchObject({ draft: "no frontmatter", saved: DOC, busy: false, error: words });
      }
      c.input(input("skill-text", "no frontmatter!")); // typing again takes the refusal away
      expect(c.view().skill!.error).toBeNull();
    });

    test("is switched on or off from its row, and the list follows", async () => {
      const c = await open();
      await settle();
      api.membersSkillsUpdate.mockResolvedValue({});
      api.membersSkillsList.mockImplementation(async (id: number) => ({ skills: id === 1 ? [{ ...SKILLS[0]!.skill, enabled: false }] : [SKILLS[1]!.skill] }));
      c.click(button("skill-enable", { member: "1", id: "7" }));
      await settle();
      expect(api.membersSkillsUpdate).toHaveBeenCalledWith(1, 7, false);
      expect((c.view().skills as SkillItem[])[0]!.skill.enabled).toBe(false);
      api.membersSkillsUpdate.mockRejectedValueOnce("[-32602] Invalid params: no such member skill");
      c.click(button("skill-enable", { member: "1", id: "7" }));
      await settle();
      expect(lastToast()).toContain("no such member skill");
    });

    test("is deleted only after the owner agrees, and the page goes with it", async () => {
      const { confirmDelete } = await import("./member-dialogs");
      const c = await open();
      await settle();
      vi.mocked(confirmDelete).mockResolvedValueOnce(false);
      c.click(button("skill-delete", { member: "1", id: "7" }));
      await settle();
      expect(api.membersSkillsDelete).not.toHaveBeenCalled();
      expect(c.view().skill).not.toBeNull();
      api.membersSkillsDelete.mockResolvedValue({ deleted: true });
      api.membersSkillsList.mockImplementation(async (id: number) => ({ skills: id === 2 ? [SKILLS[1]!.skill] : [] }));
      vi.mocked(confirmDelete).mockResolvedValueOnce(true);
      c.click(button("skill-delete", { member: "1", id: "7" }));
      await settle();
      expect(confirmDelete).toHaveBeenLastCalledWith("summary-style");
      expect(api.membersSkillsDelete).toHaveBeenCalledWith(1, 7);
      expect(c.view().skill).toBeNull();
      expect((c.view().skills as SkillItem[]).map((item) => item.skill.name)).toEqual(["meeting-notes"]);
      expect(lastToast()).toBe("已刪除技能 summary-style");
    });

    test("closes with its X or Escape and drops what was typed; a page whose skill was deleted elsewhere closes by itself", async () => {
      const c = await open();
      await settle();
      c.input(input("skill-text", "half typed"));
      c.click(button("close"));
      expect(c.view().skill).toBeNull();
      c.click(button("skill-open", { member: "1", id: "7" }));
      await settle();
      expect(c.view().skill).toMatchObject({ saved: DOC, draft: DOC }); // the document is read again: nothing typed stays
      const esc = key("Escape");
      expect(c.key(esc)).toBe(true);
      expect(c.view().skill).toBeNull();
      c.click(button("skill-open", { member: "1", id: "7" }));
      await settle();
      api.membersSkillsList.mockResolvedValue({ skills: [] });
      c.followEvent({ type: "MembersChanged" });
      await settle();
      expect(c.view().skill).toBeNull();
      // leaving the tab drops the page too
      api.membersSkillsList.mockImplementation(async (id: number) => ({ skills: SKILLS.filter((item) => item.member.id === id).map((item) => item.skill) }));
      await c.load();
      await settle();
      c.click(button("skill-open", { member: "2", id: "8" }));
      c.reset();
      expect(c.view().skill).toBeNull();
    });
  });

  describe("a server of the user's own", () => {
    const menu = (c: ReturnType<typeof createConnections>) => c.menuFor("conn-add")!;

    test("the add menu offers a form, JSON paste and app discovery", async () => {
      const c = createConnections(host);
      await c.load();
      expect(menu(c).choices.map((x) => x.value)).toEqual(["custom", "paste", "import"]);
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
      c.input(input("conn-args", "-y\nnotes-server\npath with spaces"));
      c.input(input("conn-pairs", "API_KEY=TOPSECRET"));
      c.click(button("connect"));
      await settle();
      expect(api.integrationsAdd).toHaveBeenCalledWith({
        name: "Notes", transport: "stdio", command: "npx", args: ["-y", "notes-server", "path with spaces"], env: [{ name: "API_KEY", value: "TOPSECRET" }], headers: undefined, result_size_limit: 8000,
      });
      expect(api.integrationsRefresh).not.toHaveBeenCalled();
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
      c.input(input("conn-pairs", "Authorization"));
      c.click(button("connect"));
      await settle();
      expect(c.view().form!.error!()).toBe("請使用每行 KEY=value 的格式");
      c.input(input("conn-pairs", "Authorization=Bearer TOKEN"));
      c.click(button("connect"));
      await settle();
      expect(api.integrationsAdd).toHaveBeenCalledTimes(1);
      expect(api.integrationsAdd).toHaveBeenCalledWith({
        name: "Remote", transport: "http", url: "https://example.com/mcp", headers: [{ name: "Authorization", value: "Bearer TOKEN" }], env: undefined, result_size_limit: 8000,
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
    api.connectorsConnect.mockReturnValue(new Promise(() => undefined));
    const c = createConnections(host);
    await c.load();
    c.click(button("open", { id: "ab12" }));
    c.click(button("disconnect", { id: "ab12" }));
    c.click(button("join", { id: "linear" }));
    c.menuFor("conn-add")!.pick("custom");
    c.input(input("conn-name", "Half"));
    c.input(input("conn-pairs", "TOKEN=TOPSECRET"));
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
