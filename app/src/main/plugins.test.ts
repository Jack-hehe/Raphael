// @vitest-environment happy-dom
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import type { ManagedConnector, Toolkit } from "./plugins-data";
import { managedGroup, pluginLogoURL, storePlugin } from "./plugins-data";
import { setLocale } from "./i18n";

const api = vi.hoisted(() => ({
  integrationsCatalog: vi.fn(), integrationsConnect: vi.fn(), integrationsConnectCancel: vi.fn(),
  toolsetsList: vi.fn(), integrationsAudit: vi.fn(), integrationsDisconnect: vi.fn(),
  connectorsStatus: vi.fn(), connectorsCatalog: vi.fn(), connectorsCategories: vi.fn(),
  connectorsTools: vi.fn(), connectorsSetTools: vi.fn(), connectorsConnect: vi.fn(),
  connectorsGoogleConnect: vi.fn(), connectorsDisconnect: vi.fn(), connectorsSetKey: vi.fn(),
  integrationsInventory: vi.fn(), membersList: vi.fn(), membersSkillsList: vi.fn(),
}));
vi.mock("./api", () => api);
vi.mock("./ui", async original => ({ ...(await original<typeof import("./ui")>()), toaster: { info: vi.fn() } }));
import { connectionsHTML, createConnections, emptyConnectionsView } from "./connections";
import { closeMenu, toaster } from "./ui";

let connectors: ManagedConnector[];
const toolkit: Toolkit = { slug: "notion", name: "Notion", description: "Workspace notes", logo: "https://logos.composio.dev/notion.svg", categories: ["docs", "other"], can_connect: true, no_auth: false };
const tools = [
  { slug: "NOTION_SEARCH", name: "Search", description: "Find notes", kind: "read", enabled: true },
  { slug: "NOTION_CREATE", name: "Create", description: "Add a note", kind: "write", enabled: true },
  { slug: "NOTION_DELETE", name: "Delete", description: "Remove a note", kind: "write", enabled: false },
];
beforeEach(() => {
  for (const fn of Object.values(api)) fn.mockReset();
  vi.mocked(toaster.info).mockReset();
  setLocale("en");
  document.body.innerHTML = '<main id="root"></main>';
  connectors = [{ id: "composio:notion", kind: "composio", name: "Notion", description: "Workspace notes", status: "ACTIVE", enabled: true, enabled_tools: ["NOTION_SEARCH", "NOTION_CREATE"], disabled_tools: ["NOTION_DELETE"] },
    { id: "google", kind: "google", status: "ACTIVE", enabled: true, enabled_tools: [], disabled_tools: [] }];
  api.toolsetsList.mockResolvedValue({ groups: [{ ...managedGroup(connectors[0]!), id: "custom", source: undefined, catalog_id: null, auth: "none", name: "Local MCP" },
    { ...managedGroup(connectors[0]!), id: "connectors_composio" }, { ...managedGroup(connectors[1]!), id: "connectors_google" }] });
  api.integrationsCatalog.mockResolvedValue({ services: [] });
  api.integrationsAudit.mockResolvedValue({ entries: [] });
  api.integrationsInventory.mockResolvedValue({ state: "complete", count: 0, apps: [] });
  api.membersList.mockResolvedValue({ members: [], groups: [] });
  api.membersSkillsList.mockResolvedValue({ skills: [] });
  api.connectorsStatus.mockImplementation(async () => ({ connectors: structuredClone(connectors), composio_configured: true, google_configured: true }));
  api.connectorsCatalog.mockResolvedValue({ items: [toolkit, { ...toolkit, slug: "github", name: "GitHub" }], categories: [{ id: "docs", name: "Documents" }, { id: "other", name: "Other" }] });
  api.connectorsCategories.mockResolvedValue({ items: [{ id: "docs", name: "Documents" }] });
  api.connectorsTools.mockImplementation(async id => ({ items: tools.map(t => ({ ...t, enabled: !connectors.find(c => c.id === id)!.disabled_tools.includes(t.slug) })) }));
  api.connectorsSetTools.mockImplementation(async (id, changes) => { Object.assign(connectors.find(c => c.id === id)!, changes); return {}; });
});
afterEach(() => { closeMenu(); setLocale("zh-TW"); document.body.innerHTML = ""; vi.useRealTimers(); });

function setup() {
  const root = document.querySelector<HTMLElement>("#root")!;
  const c = createConnections({ root, visible: () => true, redraw: () => { root.innerHTML = connectionsHTML(c.view()); }, tryPrompt: vi.fn() });
  const click = (action: string, id?: string) => {
    const button = document.createElement("button"); button.dataset.conn = action;
    if (id) button.dataset.id = id;
    c.click(button);
  };
  return { c, root, click };
}
const settle = () => new Promise(resolve => setTimeout(resolve, 0));

test("unifies managed, private Google and custom MCP without bridge duplicates", async () => {
  const { c, root } = setup(); await c.load();
  expect(c.view().groups).toHaveLength(3);
  expect(root.querySelectorAll(".cs-list")).toHaveLength(1);
  for (const row of root.querySelectorAll(".cs-item")) {
    expect(row.querySelector('[data-conn="enable"]')).not.toBeNull();
    expect(row.querySelector(".chev")).not.toBeNull();
    expect(row.querySelector('[data-conn="server-menu"]')).toBeNull();
  }
  expect(root.textContent).toContain("Google (private)");
});

test("managed detail folds its tools into one line that unfolds into a switch each, and contains disconnect only in overflow", async () => {
  const { c, root, click } = setup(); await c.load(); click("open", "composio:notion"); await settle();
  expect(root.querySelectorAll('[data-conn="tool"]')).toHaveLength(0);
  click("tool-list");
  expect(root.querySelectorAll(".cs-plugin-tools section")).toHaveLength(2);
  expect(root.querySelectorAll('[data-conn="tool"]')).toHaveLength(3);
  expect(root.querySelector('[data-tool="NOTION_DELETE"]')?.getAttribute("aria-checked")).toBe("false");
  expect(root.querySelector('[data-conn="disconnect"]')).toBeNull();
  c.click(root.querySelector('[data-conn="server-menu"]')!);
  expect(document.querySelector('.menu [data-conn="disconnect"]')).not.toBeNull();
  expect(document.querySelector('.menu [data-conn="edit-server"]')).toBeNull(); c.reset();
});

test("a failed managed status read is visible and retry restores the complete list", async () => {
  api.connectorsStatus.mockRejectedValueOnce(new Error("offline"));
  const { c, root, click } = setup(); await c.load();
  expect(c.view().groups).toBe("failed");
  expect(root.querySelector('[data-conn="retry-connections"]')).not.toBeNull();
  click("retry-connections"); await settle();
  expect(c.view().groups).toHaveLength(3);
  expect(root.querySelectorAll(".cs-item")).toHaveLength(3); c.reset();
});

test("individual tool toggles preserve all other exclusions and refresh immediately", async () => {
  const { c, root, click } = setup(); await c.load(); click("open", "composio:notion"); await settle(); click("tool-list");
  c.click(root.querySelector('[data-tool="NOTION_SEARCH"]')!); await settle();
  expect(api.connectorsSetTools).toHaveBeenCalledWith("composio:notion", { disabled_tools: ["NOTION_DELETE", "NOTION_SEARCH"] });
  expect(root.querySelector('[data-tool="NOTION_SEARCH"]')?.getAttribute("aria-checked")).toBe("false"); c.reset();
});

test("managed app switches preserve exclusions when disabling and enabling the app", async () => {
  const { c, root, click } = setup(); await c.load();
  expect(root.querySelector('[data-conn="enable"][data-id="composio:notion"]')?.getAttribute("aria-checked")).toBe("true");
  click("enable", "composio:notion"); await settle();
  expect(api.connectorsSetTools).toHaveBeenLastCalledWith("composio:notion", { enabled: false });
  click("enable", "composio:notion"); await settle();
  expect(api.connectorsSetTools).toHaveBeenLastCalledWith("composio:notion", { enabled: true });
  expect(connectors[0]!.disabled_tools).toEqual(["NOTION_DELETE"]); c.reset();
});

test("managed kind switches preserve other and unknown exclusions, leave the app off, and turn a partial kind fully on", async () => {
  connectors[0]!.disabled_tools = ["NOTION_SEARCH", "NOTION_DELETE", "UNLISTED_TOOL"];
  connectors[0]!.enabled = false;
  const { c, root, click } = setup(); await c.load(); click("open", "composio:notion"); await settle();
  const press = async (kind: string) => { c.click(root.querySelector(`[data-conn="kind"][data-kind="${kind}"]`)!); await settle(); };
  await press("write");
  expect(api.connectorsSetTools).toHaveBeenLastCalledWith("composio:notion", { disabled_tools: ["NOTION_SEARCH", "UNLISTED_TOOL"] });
  await press("write");
  expect(new Set(connectors[0]!.disabled_tools)).toEqual(new Set(["NOTION_SEARCH", "UNLISTED_TOOL", "NOTION_CREATE", "NOTION_DELETE"]));
  await press("read");
  expect(connectors[0]!.disabled_tools).not.toContain("NOTION_SEARCH");
  expect(connectors[0]!.disabled_tools).toContain("NOTION_CREATE");
  expect(connectors[0]!.enabled).toBe(false);
  expect(root.querySelector('[aria-checked="mixed"]')).toBeNull(); c.reset();
});

test.each(["zh-TW", "en", "ja"] as const)("toolkit refusal has its own localized value-free login error in %s", async locale => {
  setLocale(locale);
  api.connectorsConnect.mockRejectedValue(new Error("toolkit_not_allowed"));
  const { c, root, click } = setup(); await c.load(); click("tab", "store"); await settle(); click("open-store", "github"); click("join", "github"); await settle();
  const error = root.querySelector('[role="alert"]')?.textContent;
  expect(error).toBe({ "zh-TW": "Composio 尚未允許這個應用程式登入，請重試連結。", en: "Composio has not allowed this app to sign in. Try connecting again.", ja: "Composio がこのアプリのログインを許可していません。もう一度接続してください。" }[locale]);
  expect(root.innerHTML).not.toContain("ToolRouterV2_ToolkitNotAllowed"); c.reset();
});

test("failed tool reads remain visible and can be retried", async () => {
  api.connectorsTools.mockRejectedValueOnce(new Error("network"));
  const { c, root, click } = setup(); await c.load(); click("open", "composio:notion"); await settle();
  expect(root.querySelector('[role="alert"]')).not.toBeNull();
  click("refresh", "composio:notion"); await settle(); click("tool-list");
  expect(root.querySelectorAll('[data-conn="tool"]')).toHaveLength(3); c.reset();
});

test("the whole store groups in provider order, deduplicates, and searches and filters locally", async () => {
  api.connectorsCatalog.mockResolvedValue({ items: [toolkit, toolkit,
    { ...toolkit, slug: "github", name: "GitHub", categories: [{ id: "other", name: "Other" }, "docs"] },
    { ...toolkit, slug: "uncategorized", name: "Uncategorized", categories: [] }],
    categories: [{ id: "other", name: "Other" }, { id: "docs", name: "Documents" }] });
  const { c, root, click } = setup(); await c.load(); click("tab", "store"); await settle();
  expect([...root.querySelectorAll(".cs-composio-category h3")].map(e => e.textContent)).toEqual(["Other", "Documents", "Other"]);
  expect(root.querySelectorAll(".cs-card")).toHaveLength(3);
  expect(root.querySelector('[data-category="docs"] [data-id="notion"]')).not.toBeNull();
  expect(root.querySelector('[data-category="other"] [data-id="notion"]')).toBeNull();
  expect(root.querySelector('[data-conn="load-more"]')).toBeNull();
  const requests = api.connectorsCatalog.mock.calls.length;
  c.menuFor("conn-category")!.pick("other");
  expect(root.querySelectorAll(".cs-card")).toHaveLength(1);
  expect(root.querySelector('[data-id="github"]')).not.toBeNull();
  c.input({ id: "conn-search", value: "GitHub" } as HTMLInputElement);
  expect(root.querySelectorAll(".cs-card")).toHaveLength(1);
  c.menuFor("conn-category")!.pick("");
  c.input({ id: "conn-search", value: "Workspace" } as HTMLInputElement);
  expect(root.querySelectorAll(".cs-card")).toHaveLength(3);
  expect(api.connectorsCatalog).toHaveBeenCalledTimes(requests);
  expect(api.connectorsCategories).not.toHaveBeenCalled(); c.reset();
});

test("local filters apply to an in-flight full catalog, and reset ignores a late reply", async () => {
  let finish!: (value: unknown) => void;
  api.connectorsCatalog.mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
  const { c, root, click } = setup(); const loaded = c.load(); await settle(); click("tab", "store");
  // Starting a newer full read supersedes the first; neither filter needs a request.
  await settle(); c.menuFor("conn-category")!.pick("other");
  c.input({ id: "conn-search", value: "GitHub" } as HTMLInputElement);
  finish({ items: [], categories: [] }); await loaded;
  expect(root.querySelectorAll(".cs-card")).toHaveLength(0);
  expect(c.view().composioCatalog).toHaveLength(2);
  let late!: (value: unknown) => void;
  api.connectorsCatalog.mockReturnValueOnce(new Promise(resolve => { late = resolve; }));
  click("retry-store"); c.reset(); late({ items: [], categories: [] }); await settle();
  expect(c.view().composioCatalog).toHaveLength(2);
});

test("a failed refresh preserves the last full store list", async () => {
  const { c, root, click } = setup(); await c.load(); click("tab", "store"); await settle();
  api.connectorsCatalog.mockRejectedValueOnce(new Error("offline"));
  click("retry-store"); await settle();
  expect(root.querySelectorAll(".cs-card")).toHaveLength(2); c.reset();
});

test("missing key offers an optional dialog without requesting managed apps", async () => {
  api.connectorsStatus.mockResolvedValue({ connectors: [], composio_configured: false });
  const { c, root, click } = setup(); await c.load(); click("tab", "store");
  expect(root.querySelector("#conn-key")).toBeNull();
  click("configure-composio");
  expect(root.querySelector<HTMLInputElement>("#conn-key")?.type).toBe("password");
  expect(root.querySelector(".cs-composio-section")).toBeNull();
  expect(api.connectorsCatalog).not.toHaveBeenCalled();
});

test.each([false, true])("saving a key never retains or echoes its value (failure %s)", async failed => {
  api.connectorsStatus.mockResolvedValue({ connectors: [], composio_configured: false });
  if (failed) api.connectorsSetKey.mockRejectedValue(new Error("ak_secret-provider-error"));
  else api.connectorsSetKey.mockResolvedValue({ configured: true });
  const { c, root, click } = setup(); await c.load(); click("tab", "store"); click("configure-composio");
  root.querySelector<HTMLInputElement>("#conn-key")!.value = "ak_secret";
  click("save-key");
  expect(root.innerHTML).not.toContain("ak_secret"); await settle();
  expect(api.connectorsSetKey).toHaveBeenCalledWith("ak_secret");
  expect(JSON.stringify(c.view())).not.toContain("ak_secret");
  expect(root.innerHTML).not.toContain("ak_secret");
  expect(vi.mocked(toaster.info)).not.toHaveBeenCalled();
  expect(!!root.querySelector('[role="alert"]')).toBe(failed);
  expect(!!root.querySelector(".cs-grid")).toBe(!failed); c.reset();
});

test("one login at a time, connecting state, notification refresh and installed result", async () => {
  let finish!: (value: unknown) => void;
  api.connectorsConnect.mockReturnValue(new Promise(resolve => { finish = resolve; }));
  const { c, root, click } = setup(); await c.load(); click("tab", "store"); await settle();
  click("join", "github"); click("join", "github");
  expect(api.connectorsConnect).toHaveBeenCalledTimes(1);
  expect(api.connectorsConnect).toHaveBeenCalledWith("github");
  expect(root.querySelector(".cs-card .sub.wait")).not.toBeNull();
  expect(root.querySelector<HTMLButtonElement>('[data-conn="cancel-login"]')?.disabled).toBe(true);
  click("cancel-login"); expect(api.integrationsConnectCancel).not.toHaveBeenCalled();
  connectors.push({ ...connectors[0]!, id: "composio:github", name: "GitHub", disabled_tools: [] });
  c.followEvent({ type: "ConnectionsChanged" }); await settle();
  expect(c.view().groups).toHaveLength(4);
  finish({ connected: true }); await settle();
  expect(c.view().tab).toBe("installed"); expect(c.view().login).toBeNull();
});

test("failed login restores actions without echoing provider text", async () => {
  api.connectorsConnect.mockRejectedValue(new Error("private provider payload"));
  const { c, click } = setup(); await c.load(); click("join", "github"); await settle();
  expect(c.view().login).toBeNull(); expect(c.view().loginError?.target).toBe("github");
  expect(vi.mocked(toaster.info).mock.calls.flat().join(" ")).not.toContain("private provider");
});

test("managed disconnect confirms twice and private Google reconnect uses its own RPC", async () => {
  api.connectorsDisconnect.mockResolvedValue({ disconnected: true });
  api.connectorsGoogleConnect.mockResolvedValue({ connected: true });
  const { c, click } = setup(); await c.load();
  click("disconnect", "composio:notion"); expect(api.connectorsDisconnect).not.toHaveBeenCalled();
  click("disconnect", "composio:notion"); await settle();
  expect(api.connectorsDisconnect).toHaveBeenCalledWith("composio:notion");
  expect(api.integrationsDisconnect).not.toHaveBeenCalled();
  click("relogin", "google"); await settle(); expect(api.connectorsGoogleConnect).toHaveBeenCalled(); c.reset();
});

test("only trusted remote logos are rendered and they never send a referrer", () => {
  for (const url of ["http://logos.composio.dev/a.svg", "https://composio.dev.evil/a.svg", "https://user@composio.dev/a", "javascript:alert(1)", "https://logos.composio.dev:8443/a.svg"])
    expect(pluginLogoURL(url)).toBeNull();
  const html = connectionsHTML({ ...emptyConnectionsView(), configured: true, tab: "store", catalog: [storePlugin(toolkit, [])] });
  expect(html).toContain('src="https://logos.composio.dev/notion.svg"');
  expect(html).toContain('referrerpolicy="no-referrer"');
});


test.each(["zh-TW", "en", "ja"] as const)("key failure copy is specific and private in %s", async locale => {
  const messages = {
    "zh-TW": ["這是 OpenRouter 的金鑰，請到「設定 → 模型與用量」輸入。", "Composio 不接受這把金鑰。請使用 Composio「Settings → Project Settings → API Keys」中的專案金鑰。", "連不上 Composio，請稍後再試。", "無法儲存金鑰，請檢查金鑰後重試。", "這是 Composio 的消費者金鑰（ck_ 開頭）。請改用專案金鑰：Composio「Settings → Project Settings → API Keys」。"],
    en: ["This is an OpenRouter key. Enter it in Settings → Models and usage.", "Composio did not accept this key. Use a project key from Composio Settings → Project Settings → API Keys.", "Could not reach Composio. Try again later.", "Could not save the key. Check it and retry.", "This is a Composio consumer key (starting with ck_). Use a project key from Composio Settings → Project Settings → API Keys."],
    ja: ["これは OpenRouter のキーです。「設定 → モデルと使用量」で入力してください。", "Composio がこのキーを受け付けませんでした。Composio の「Settings → Project Settings → API Keys」にあるプロジェクトキーを使用してください。", "Composio に接続できません。しばらくしてから再試行してください。", "キーを保存できません。確認して再試行してください。", "これは Composio のコンシューマーキー（ck_ で始まるキー）です。Composio の「Settings → Project Settings → API Keys」にあるプロジェクトキーを使用してください。"],
  };
  setLocale(locale);
  api.connectorsStatus.mockResolvedValue({ connectors: [], composio_configured: false });
  const { c, root, click } = setup(); await c.load(); click("tab", "store"); click("configure-composio");
  for (const [kind, index] of [["openrouter_key", 0], ["auth", 1], ["network", 2], ["timeout", 2], ["private-provider-payload", 3], ["consumer_key", 4]] as const) {
    api.connectorsSetKey.mockRejectedValueOnce(new Error(kind));
    const candidate = kind === "consumer_key" ? "ck_private-consumer-test-value" : "private-project-key";
    root.querySelector<HTMLInputElement>("#conn-key")!.value = candidate;
    click("save-key"); await settle();
    expect(root.querySelector('[role="alert"]')?.textContent).toBe(messages[locale][index]);
    expect(root.querySelector<HTMLInputElement>("#conn-key")?.value).toBe("");
    expect(root.innerHTML + JSON.stringify(c.view())).not.toContain(candidate);
    expect(root.textContent).not.toContain("private-provider-payload");
  }
  c.reset();
});


test("built-in services work without a key, search locally, and use their existing join RPC", async () => {
  const own = { ...storePlugin({ ...toolkit, slug: "sentry", name: "Sentry" }, []), source: undefined, popular: true };
  api.integrationsCatalog.mockResolvedValue({ services: [own] });
  api.connectorsStatus.mockResolvedValue({ connectors: [], composio_configured: false });
  api.integrationsConnect.mockReturnValue(new Promise(() => {}));
  const { c, root, click } = setup(); await c.load(); click("tab", "store");
  expect(root.querySelectorAll(".cs-card")).toHaveLength(1);
  expect(root.querySelector('[data-k="conn-tab:store"] .n')?.textContent).toBe("1");
  expect(root.querySelector('[data-conn="configure-composio"]')).not.toBeNull();
  c.input({ id: "conn-search", value: "unmatched" } as HTMLInputElement);
  expect(root.querySelectorAll(".cs-card")).toHaveLength(0);
  expect(root.querySelector('[data-k="conn-tab:store"] .n')?.textContent).toBe("0");
  c.input({ id: "conn-search", value: "Sentry" } as HTMLInputElement);
  click("join", "sentry");
  expect(api.integrationsConnect).toHaveBeenCalledWith({ catalog_id: "sentry" });
  expect(api.connectorsConnect).not.toHaveBeenCalled();
  expect(api.connectorsCatalog).not.toHaveBeenCalled(); c.reset();
});

test("own services win across slugs and names, and Composio categories leave them visible", async () => {
  api.integrationsCatalog.mockResolvedValue({ services: [{ ...storePlugin(toolkit, []), source: undefined, popular: true }] });
  api.connectorsCatalog.mockResolvedValue({ items: [toolkit, { ...toolkit, slug: "notion-alt" }, { ...toolkit, slug: "github", name: "GitHub" }], categories: [{ id: "docs", name: "Documents" }, { id: "other", name: "Other" }] });
  const { c, root, click } = setup(); await c.load(); click("tab", "store"); await settle();
  expect(root.querySelectorAll(".cs-card")).toHaveLength(2);
  expect(root.querySelectorAll('[data-conn="join"][data-id="notion"]')).toHaveLength(1);
  expect(root.querySelector('[data-k="conn-tab:store"] .n')?.textContent).toBe("2");
  c.menuFor("conn-category")!.pick("other"); await settle();
  expect(root.querySelectorAll(".cs-card")).toHaveLength(1);
  expect(root.querySelector('[data-conn="join"][data-id="notion"]')).not.toBeNull();
  expect(root.querySelector('[data-k="conn-tab:store"] .n')?.textContent).toBe("1"); c.reset();
});

test("a managed catalog failure leaves built-ins available and a late key reply cannot reopen a closed dialog", async () => {
  api.integrationsCatalog.mockResolvedValue({ services: [{ ...storePlugin(toolkit, []), source: undefined }] });
  api.connectorsCatalog.mockRejectedValue(new Error("offline"));
  const { c, root, click } = setup(); await c.load(); click("tab", "store"); await settle();
  expect(root.querySelector('[data-conn="join"][data-id="notion"]')).not.toBeNull();
  expect(root.querySelector('.cs-composio-section [role="alert"]')).not.toBeNull();
  api.connectorsStatus.mockResolvedValue({ connectors: [], composio_configured: false }); await c.load();
  let reject!: (error: Error) => void;
  api.connectorsSetKey.mockReturnValue(new Promise((_, no) => { reject = no; }));
  click("configure-composio"); root.querySelector<HTMLInputElement>("#conn-key")!.value = "private-key"; click("save-key");
  click("close"); reject(new Error("consumer_key")); await settle();
  expect(c.view()).toMatchObject({ keyOpen: false, keyBusy: false, keyError: "" });
  expect(root.querySelector("#conn-key")).toBeNull(); c.reset();
});


test.each(["available", "added", "connecting", "unavailable"])("both store sources share their row and action markup when %s", state => {
  const base = { ...storePlugin({ ...toolkit, logo: "" }, []), server_id: state === "added" ? "existing" : null, can_connect: state !== "unavailable" };
  const render = (managed: boolean) => {
    const item = { ...base, source: managed ? "composio" as const : undefined };
    const root = document.createElement("div");
    root.innerHTML = connectionsHTML({ ...emptyConnectionsView(), tab: "store", configured: true,
      catalog: managed ? [] : [item], composioCatalog: managed ? [item] : [],
      login: state === "connecting" ? { target: item.id, name: item.name } : null });
    return root.querySelector<HTMLElement>(".cs-card")!;
  };
  const own = render(false), managed = render(true);
  if (state === "connecting") {
    expect(own.querySelector<HTMLButtonElement>('[data-conn="cancel-login"]')!.disabled).toBe(false);
    const cancel = managed.querySelector<HTMLButtonElement>('[data-conn="cancel-login"]')!;
    expect(cancel.disabled).toBe(true); cancel.removeAttribute("disabled");
  }
  expect(managed.outerHTML).toBe(own.outerHTML);
  if (state === "unavailable") expect(managed.querySelector<HTMLButtonElement>('[data-conn="join"]')!.disabled).toBe(true);
});

test("managed store rows open the shared detail and join through their own provider", async () => {
  api.connectorsConnect.mockResolvedValue({ connected: false, error: "cancelled" });
  const { c, root, click } = setup(); await c.load(); click("tab", "store"); await settle();
  click("open-store", "github");
  expect(root.querySelector(".cs-page .cs-head")?.textContent).toContain("GitHub");
  expect(root.querySelector(".cs-meta, .cs-say")).toBeNull();
  click("join", "github"); await settle();
  expect(api.connectorsConnect).toHaveBeenCalledWith("github");
  expect(api.integrationsConnect).not.toHaveBeenCalled(); c.reset();
});
