import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { setLocale } from "./i18n";
import type { ConnectionGroup, MainEvent, StoreService } from "./types";

const api = vi.hoisted(() => ({
  toolsetsList: vi.fn(),
  integrationsCatalog: vi.fn(),
  integrationsAudit: vi.fn(),
  integrationsConnect: vi.fn(),
  integrationsConnectCancel: vi.fn(),
  integrationsDisconnect: vi.fn(),
  openLink: vi.fn(),
}));
vi.mock("./api", () => api);
vi.mock("./ui", async (original) => ({
  ...(await original<typeof import("./ui")>()),
  toaster: { info: vi.fn() },
  copyText: vi.fn(),
}));

import { connectionsHTML, createConnections, emptyConnectionsView } from "./connections";
import { copyText, toaster } from "./ui";

afterEach(() => setLocale("zh-TW"));

const words = (zh: string, en: string, ja: string) => ({ "zh-TW": zh, en, ja });
const github: StoreService = {
  id: "github", name: "GitHub", publisher: "GitHub", url: "https://api.githubcopilot.com/mcp/", category: "developer", icon: "github",
  popular: false, auth: "device", server_id: null,
  summary: words("程式庫", "Repos", "リポジトリ"), about: words("說明", "About", "説明"),
  examples: { "zh-TW": ["一", "二"], en: ["one", "two"], ja: ["いち", "に"] },
};
const connected: ConnectionGroup = {
  id: "gh1", name: "GitHub", enabled: true, status: "ready", error: null, auth: "device", catalog_id: "github", transport: "http",
  command: "", url: github.url, env: [], headers: [], args: [], tool_count: 0, enabled_count: 0, write_count: 0, read_count: 0,
  checked_at_ms: 1, tools: [],
};
const code: MainEvent = {
  type: "DeviceCode", catalog_id: "github", user_code: "WDJB-MJHT", verification_uri: "https://github.com/login/device", expires_in: 900,
};

const host = { visible: () => true, redraw: vi.fn(), tryPrompt: vi.fn() };
const button = (conn: string, extra: Record<string, string> = {}): Element =>
  ({ closest: () => ({ dataset: { conn, ...extra } }) }) as unknown as Element;
const settle = (): Promise<void> => new Promise((resolve) => setTimeout(resolve, 0));

beforeEach(() => {
  for (const fn of Object.values(api)) fn.mockReset();
  vi.mocked(toaster.info).mockReset();
  vi.mocked(copyText).mockReset();
  host.redraw.mockReset();
  api.toolsetsList.mockResolvedValue({ groups: [] });
  api.integrationsCatalog.mockResolvedValue({ services: [github] });
  api.integrationsAudit.mockResolvedValue({ entries: [] });
  api.integrationsConnectCancel.mockResolvedValue({ cancelled: true });
  api.openLink.mockResolvedValue(undefined);
});

async function waitingForCode() {
  let finish!: (value: unknown) => void;
  api.integrationsConnect.mockReturnValue(new Promise((resolve) => (finish = resolve)));
  const c = createConnections(host);
  await c.load();
  c.click(button("join", { id: "github" }));
  return { c, finish };
}

describe("the code of a device login", () => {
  test("shows the code in keycaps with one copy-and-open button and a cancel button", async () => {
    const { c } = await waitingForCode();
    expect(connectionsHTML(c.view())).not.toContain("WDJB");
    c.followEvent(code);
    expect(c.view().device).toEqual({ catalog_id: "github", user_code: "WDJB-MJHT", verification_uri: "https://github.com/login/device" });
    const html = connectionsHTML(c.view());
    expect(html.match(/class="cs-cap"/g)).toHaveLength(8);
    expect(html).toContain('aria-label="WDJB-MJHT"');
    expect(html.match(/data-conn="device-open"/g)).toHaveLength(1);
    expect(html).toContain("複製代碼並開啟 GitHub");
    expect(html).toContain('data-conn="cancel-login"');
    expect(html.indexOf('data-conn="cancel-login"')).toBeLessThan(html.indexOf('data-conn="device-open"'));
    // buttons are real buttons, never clickable text
    expect(html).not.toMatch(/<(a|span)[^>]*data-conn="device-open"/);
  });

  test.each([
    ["en", "Copy code and open GitHub"],
    ["ja", "コードをコピーして GitHub を開く"],
  ] as const)("says it in %s", async (locale, label) => {
    setLocale(locale);
    const { c } = await waitingForCode();
    c.followEvent(code);
    expect(connectionsHTML(c.view())).toContain(label);
  });

  test("the button copies the code and opens the address", async () => {
    const { c } = await waitingForCode();
    c.followEvent(code);
    c.click(button("device-open"));
    await settle();
    expect(copyText).toHaveBeenCalledWith("WDJB-MJHT");
    expect(api.openLink).toHaveBeenCalledWith("https://github.com/login/device");
  });

  test("a link that may not be opened is told to the user", async () => {
    api.openLink.mockRejectedValue(new Error("that link may not be opened"));
    const { c } = await waitingForCode();
    c.followEvent(code);
    c.click(button("device-open"));
    await settle();
    expect(toaster.info).toHaveBeenCalled();
  });

  test("a code with no login waiting for it is ignored, and other events too", async () => {
    const c = createConnections(host);
    await c.load();
    c.followEvent(code);
    expect(c.view().device).toBeNull();
    c.followEvent({ type: "Level", value: 0.5 });
    expect(c.view().device).toBeNull();
  });

  test("the code goes when the login ends, whichever way", async () => {
    const { c, finish } = await waitingForCode();
    c.followEvent(code);
    api.toolsetsList.mockResolvedValue({ groups: [connected] });
    finish({ connected: true, server: { id: "gh1" } });
    await settle();
    await settle();
    expect(c.view().device).toBeNull();
    expect(connectionsHTML(c.view())).not.toContain("WDJB");
  });

  test("leaving the tab keeps the code while the login is still waiting", async () => {
    const { c } = await waitingForCode();
    c.followEvent(code);
    c.reset();
    expect(c.view().login).not.toBeNull();
    expect(c.view().device?.user_code).toBe("WDJB-MJHT");
    expect(connectionsHTML(c.view())).toContain("WDJB");
  });

  test("cancelling asks the core to end the wait", async () => {
    const { c } = await waitingForCode();
    c.followEvent(code);
    c.click(button("cancel-login"));
    expect(api.integrationsConnectCancel).toHaveBeenCalled();
  });
});

describe("a connected device service", () => {
  test("its dialog has a button that opens GitHub's list of authorised apps", async () => {
    api.toolsetsList.mockResolvedValue({ groups: [connected] });
    const c = createConnections(host);
    await c.load();
    c.click(button("open", { id: "gh1" }));
    const html = connectionsHTML(c.view());
    expect(html).toContain('data-conn="open-grants"');
    expect(html).toContain("到 GitHub 移除授權");
    c.click(button("open-grants", { id: "gh1" }));
    await settle();
    expect(api.openLink).toHaveBeenCalledWith("https://github.com/settings/applications");
  });

  test("a token that GitHub turned away offers the sign-in again", async () => {
    const revoked: ConnectionGroup = { ...connected, status: "error", error: "auth" };
    api.toolsetsList.mockResolvedValue({ groups: [revoked] });
    const c = createConnections(host);
    await c.load();
    c.click(button("open", { id: "gh1" }));
    expect(connectionsHTML(c.view())).toContain('data-conn="relogin"');
    api.integrationsConnect.mockReturnValue(new Promise(() => undefined));
    c.click(button("relogin", { id: "gh1" }));
    expect(api.integrationsConnect).toHaveBeenCalledWith({ id: "gh1" });
  });

  test("a view with no code draws no code panel", () => {
    expect(connectionsHTML({ ...emptyConnectionsView(), groups: [], catalog: [github] })).not.toContain("cs-device");
  });
});
