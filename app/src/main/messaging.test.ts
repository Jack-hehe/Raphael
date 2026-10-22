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

import { type MessagingView, type TelegramStatus, createMessaging, emptyMessagingView, messagingHTML } from "./messaging";

afterEach(() => setLocale("zh-TW"));

const status = (over: Partial<TelegramStatus> = {}): TelegramStatus => ({
  connected: true, enabled: true, running: true, error: null, bot: { username: "raph_bot" }, allowed: [], pending: [], ...over,
});
const view = (over: Partial<MessagingView> = {}): MessagingView => ({ ...emptyMessagingView(), status: status({ connected: false, bot: null }), ...over });

/** What a sighted reader sees: the text outside the tags, without what only a screen reader hears. */
const visible = (html: string): string =>
  html.replace(/<span class="sr">.*?<\/span>/g, "").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();

describe("the setup", () => {
  test("two rows like the other settings: Telegram with BotFather, then the token with 連接; one line under them", () => {
    const html = messagingHTML(view());
    expect(html).toMatch(/^<section class="sgroup msg" data-msg-section aria-label="Telegram"><div class="slist">/);
    expect(html.match(/<div class="row /g)).toHaveLength(2);
    expect(html).toContain('data-msg="botfather" title="在 Telegram 找 @BotFather，傳 /newbot。"');
    expect(html).toContain('id="msg-token"');
    expect(html).toContain('type="password"');
    expect(html).toContain('data-msg="connect"');
    expect(html.match(/<p class="foot">/g)).toHaveLength(1);
    expect(visible(html)).toBe("Telegram BotFather 機器人權杖 連接 在 BotFather 建立機器人，把權杖貼在這裡。");
  });

  test("the first row carries Telegram's mark and a dot that says it is not connected", () => {
    const html = messagingHTML(view());
    expect(html).toMatch(/class="mono md logo-mono msg-mark" aria-hidden="true"><svg/);
    expect(html).toContain('class="dot idle" role="img" aria-label="尚未連接"');
  });

  test("never writes the typed token back into the page as text", () => {
    const html = messagingHTML(view({ token: "123456:uniquemarkeruniquemarker" }));
    expect(html).toContain('value="123456:uniquemarkeruniquemarker"'); // the field's own value, nothing else
    expect(html.split("123456:uniquemarker").length - 1).toBe(1);
  });

  test("a refused token is said in words", () => {
    expect(messagingHTML(view({ tokenError: "msg.tg.badToken" }))).toContain('role="alert"');
  });

  test("every action is a real button, and every icon-only one is named in its label and tooltip", () => {
    for (const v of [view(), view({ status: status({ pending: [{ code: "AB3DEF", name: "Ann", expires_in_s: 540 }], allowed: [{ user_id: 7, name: "Bo" }] }) })]) {
      const html = messagingHTML(v);
      for (const m of html.matchAll(/data-msg="[^"]+"/g)) {
        const tag = html.slice(html.lastIndexOf("<", m.index), m.index! + m[0].length);
        expect(tag.startsWith("<button")).toBe(true);
      }
      for (const tag of html.match(/<button[^>]*class="btn icon[^>]*>/g) ?? []) {
        expect(tag).toMatch(/aria-label="[^"]+"/);
        expect(tag).toMatch(/title="[^"]+"/);
      }
    }
  });
});

describe("when connected", () => {
  test("one row: the bot, a dot for its state, the switch and a button that opens the rest", () => {
    const html = messagingHTML(view({ status: status({ allowed: [{ user_id: 1, name: "Ann" }] }) }));
    expect(html).toContain("@raph_bot");
    expect(html).toContain('class="dot ok" role="img" aria-label="已連接"');
    expect(html).toContain('role="switch" aria-checked="true"');
    expect(html).toContain('data-msg="expand" aria-expanded="false"');
    expect(html).not.toContain('data-msg="disconnect"'); // closed: only the row
    expect(visible(html)).toBe("@raph_bot");
  });

  test("before anyone has paired it is open on the one line that says how", () => {
    const html = messagingHTML(view({ status: status() }));
    expect(html).toContain('data-msg="expand" aria-expanded="true"');
    expect(html).toContain("用 Telegram 傳訊息給 @raph_bot");
    expect(html).toContain('data-msg="disconnect"');
  });

  test("a waiting code opens it on a row of its own, with ✓ and ✗ named for the code", () => {
    const html = messagingHTML(view({ status: status({ allowed: [{ user_id: 1, name: "Bo" }], pending: [{ code: "AB3DEF", name: "Ann", expires_in_s: 540 }] }) }));
    expect(html).toContain('data-msg="expand" aria-expanded="true"');
    expect(html).toMatch(/<div class="row msg-sub">.*AB3DEF/);
    expect(html).toContain('aria-label="核准 AB3DEF" title="核准 AB3DEF" data-msg="approve" data-code="AB3DEF"');
    expect(html).toContain('aria-label="拒絕 AB3DEF" title="拒絕 AB3DEF" data-msg="reject" data-code="AB3DEF"');
    expect(html).toContain('class="msg-badge num"');
  });

  test("the people who may talk to her, each on a row with a remove button named for them", () => {
    const html = messagingHTML(view({ status: status({ allowed: [{ user_id: 42, name: "Ann <b>" }] }), expanded: true }));
    expect(html).toContain('aria-label="移除 Ann &lt;b&gt;"');
    expect(html).toContain('data-msg="remove" data-user="42"');
    expect(html).toContain("Ann &lt;b&gt;");
  });

  test("an off switch is a grey dot; a revoked token is a red dot and the reason in words", () => {
    const off = messagingHTML(view({ status: status({ enabled: false }) }));
    expect(off).toContain('aria-checked="false"');
    expect(off).toContain("dot idle");
    const revoked = messagingHTML(view({ status: status({ error: "unauthorized" }) }));
    expect(revoked).toContain("dot err");
    expect(revoked).toContain('role="alert"');
  });

  test("the section reads in all three languages", () => {
    for (const locale of ["zh-TW", "en", "ja"] as const) {
      setLocale(locale);
      expect(messagingHTML(view({ status: status() }))).not.toContain("msg.");
      expect(messagingHTML(view())).not.toContain("msg.");
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

  test("the row opens and closes on the reader's word", async () => {
    api.telegramStatus.mockResolvedValue(status({ allowed: [{ user_id: 1, name: "Ann" }] }));
    const c = make();
    await c.load();
    expect(c.view().expanded).toBeNull();
    click(c, "expand");
    expect(c.view().expanded).toBe(true);
    click(c, "expand");
    expect(c.view().expanded).toBe(false);
    c.reset();
    expect(c.view().expanded).toBeNull();
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
