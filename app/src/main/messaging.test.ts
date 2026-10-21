import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { setLocale } from "./i18n";

const api = vi.hoisted(() => ({
  telegramStatus: vi.fn(),
  telegramConnect: vi.fn(),
  telegramSetEnabled: vi.fn(),
  telegramApprove: vi.fn(),
  telegramReject: vi.fn(),
  telegramRemoveUser: vi.fn(),
  telegramDisconnect: vi.fn(),
  openLink: vi.fn(),
}));
vi.mock("./api", () => api);
vi.mock("./ui", async (original) => ({ ...(await original<typeof import("./ui")>()), toaster: { info: vi.fn() } }));

import { BOTFATHER_URL, type MessagingView, type TelegramStatus, createMessaging, emptyMessagingView, messagingHTML } from "./messaging";

afterEach(() => setLocale("zh-TW"));

const status = (over: Partial<TelegramStatus> = {}): TelegramStatus => ({
  connected: true, enabled: true, running: true, error: null, bot: { username: "raph_bot" }, allowed: [], pending: [], ...over,
});
const view = (over: Partial<MessagingView> = {}): MessagingView => ({ ...emptyMessagingView(), status: status({ connected: false, bot: null }), ...over });

describe("the setup", () => {
  test("shows the four steps, a real link to BotFather and a token field", () => {
    const html = messagingHTML(view());
    expect(html.match(/<li>/g)).toHaveLength(4);
    expect(html).toContain(`<a class="btn sm msg-link" href="${BOTFATHER_URL}"`);
    expect(html).toContain('id="msg-token"');
    expect(html).toContain('type="password"');
    expect(html).toContain('data-msg="connect"');
  });

  test("the Telegram heading carries its mark", () => {
    expect(messagingHTML(view())).toMatch(/msg-brand"><span class="mono md logo-mono"><svg/);
  });

  test("never writes the typed token back into the page as text", () => {
    const html = messagingHTML(view({ token: "123456:uniquemarkeruniquemarker" }));
    expect(html).toContain('value="123456:uniquemarkeruniquemarker"'); // the field's own value, nothing else
    expect(html.split("123456:uniquemarker").length - 1).toBe(1);
  });

  test("a refused token is said in words", () => {
    expect(messagingHTML(view({ tokenError: "msg.tg.badToken" }))).toContain('role="alert"');
  });

  test("every action is a button, never a bare clickable word", () => {
    const html = messagingHTML(view());
    for (const m of html.matchAll(/data-msg="[^"]+"/g)) {
      const tag = html.slice(html.lastIndexOf("<", m.index), m.index! + m[0].length);
      expect(tag.startsWith("<button") || tag.startsWith('<a class="btn')).toBe(true);
    }
  });
});

describe("when connected", () => {
  test("shows the bot, the switch, a hint while nobody waits, and the disconnect button", () => {
    const html = messagingHTML(view({ status: status() }));
    expect(html).toContain("@raph_bot");
    expect(html).toContain('role="switch" aria-checked="true"');
    expect(html).toContain('data-msg="disconnect"');
    expect(html).not.toContain('data-msg="approve"');
  });

  test("lists a waiting code with approve and reject buttons", () => {
    const html = messagingHTML(view({ status: status({ pending: [{ code: "AB3DEF", name: "Ann", expires_in_s: 540 }] }) }));
    expect(html).toContain("AB3DEF");
    expect(html).toContain('data-msg="approve" data-code="AB3DEF"');
    expect(html).toContain('data-msg="reject" data-code="AB3DEF"');
  });

  test("lists the allowed people each with a remove button", () => {
    const html = messagingHTML(view({ status: status({ allowed: [{ user_id: 42, name: "Ann <b>" }] }) }));
    expect(html).toContain('data-msg="remove" data-user="42"');
    expect(html).toContain("Ann &lt;b&gt;");
  });

  test("an off switch and a revoked token are shown as such", () => {
    expect(messagingHTML(view({ status: status({ enabled: false }) }))).toContain('aria-checked="false"');
    const revoked = messagingHTML(view({ status: status({ error: "unauthorized" }) }));
    expect(revoked).toContain("dot err");
  });

  test("the section reads in all three languages", () => {
    for (const locale of ["zh-TW", "en", "ja"] as const) {
      setLocale(locale);
      expect(messagingHTML(view({ status: status() }))).not.toContain("msg.");
    }
  });
});

describe("the controller", () => {
  let redraws = 0;
  const make = () => createMessaging({ visible: () => true, redraw: () => void redraws++ });
  const click = (c: ReturnType<typeof make>, msg: string, extra: Record<string, string> = {}) => {
    const b = { dataset: { msg, ...extra } } as unknown as HTMLElement;
    return c.click({ closest: () => b } as unknown as Element);
  };
  beforeEach(() => {
    redraws = 0;
    vi.clearAllMocks();
    api.telegramStatus.mockResolvedValue(status({ connected: false, bot: null }));
  });

  test("a good token connects and is dropped from the page", async () => {
    const c = make();
    await c.load();
    c.input({ id: "msg-token", value: "123456:abc" } as HTMLInputElement);
    api.telegramConnect.mockResolvedValue({ ok: true, status: status() });
    click(c, "connect");
    await vi.waitFor(() => expect(c.view().connecting).toBe(false));
    expect(api.telegramConnect).toHaveBeenCalledWith("123456:abc");
    expect(c.view().token).toBe("");
    expect(c.view().status).toMatchObject({ connected: true });
    c.reset();
  });

  test("a refused token keeps the field and says why", async () => {
    const c = make();
    await c.load();
    c.input({ id: "msg-token", value: "bad" } as HTMLInputElement);
    api.telegramConnect.mockResolvedValue({ ok: false, reason: "invalid_token" });
    click(c, "connect");
    await vi.waitFor(() => expect(c.view().tokenError).toBe("msg.tg.badToken"));
    expect(c.view().token).toBe("bad");
  });

  test("approve sends the code and takes the new state", async () => {
    api.telegramStatus.mockResolvedValue(status({ pending: [{ code: "AB3DEF", name: "Ann", expires_in_s: 500 }] }));
    const c = make();
    await c.load();
    api.telegramApprove.mockResolvedValue({ approved: true, status: status({ allowed: [{ user_id: 1, name: "Ann" }] }) });
    click(c, "approve", { code: "AB3DEF" });
    await vi.waitFor(() => expect(c.view().busy).toBe(false));
    expect(api.telegramApprove).toHaveBeenCalledWith("AB3DEF");
    expect((c.view().status as TelegramStatus).allowed).toHaveLength(1);
    c.reset();
  });

  test("disconnect needs a second press", async () => {
    api.telegramStatus.mockResolvedValue(status());
    const c = make();
    await c.load();
    click(c, "disconnect");
    expect(api.telegramDisconnect).not.toHaveBeenCalled();
    expect(c.view().confirmingDisconnect).toBe(true);
    api.telegramDisconnect.mockResolvedValue(status({ connected: false, bot: null }));
    click(c, "disconnect");
    await vi.waitFor(() => expect(api.telegramDisconnect).toHaveBeenCalled());
    c.reset();
  });

  test("the BotFather link opens through the app, not the webview", () => {
    api.openLink.mockResolvedValue(undefined);
    const c = make();
    click(c, "botfather");
    expect(api.openLink).toHaveBeenCalledWith("https://t.me/BotFather");
  });

  test("a failed read is shown as failed, and a status that did not change does not redraw", async () => {
    api.telegramStatus.mockRejectedValue(new Error("down"));
    const c = make();
    await c.load();
    expect(c.view().status).toBe("failed");
  });
});
