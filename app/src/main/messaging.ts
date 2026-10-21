// 通訊軟體: the messaging apps Raphael can be reached by from a phone, under the services of the
// 「連接」 tab. Telegram is the first. `messagingHTML` draws the section from a snapshot (pure, so the
// rules of the section can be tested without a browser); `createMessaging` holds the state and does
// what the buttons say, through the core. The bot token is typed here, sent to the core once and
// dropped: it is kept in the vault and never comes back to this page.

import * as api from "./api";
import { describeError } from "./format";
import { t } from "./i18n";
import type { MessageKey } from "./i18n/zh-TW";
import { serviceIconSVG } from "./service-icons";
import { btn, esc, fieldHTML, ico, row, st, tg, toaster } from "./ui";

/** How long 「中斷連線」 waits for its second press. */
const DISCONNECT_CONFIRM_MS = 5000;
/** How often a connected bot is asked who is waiting to pair. */
const POLL_MS = 3000;
/** The only link this section opens (commands.rs lets nothing else through). */
export const BOTFATHER_URL = "https://t.me/BotFather";

export interface TelegramStatus {
  connected: boolean;
  enabled: boolean;
  running: boolean;
  /** Why the latest poll failed: unauthorized, network, conflict, rate_limited. */
  error: string | null;
  bot: { username: string | null } | null;
  allowed: { user_id: number; name: string }[];
  pending: { code: string; name: string; expires_in_s: number }[];
}

export interface MessagingView {
  status: TelegramStatus | "loading" | "failed";
  token: string;
  connecting: boolean;
  /** Why the token was refused, as a message key. */
  tokenError: MessageKey | null;
  confirmingDisconnect: boolean;
  busy: boolean;
}

/** A view of nothing read yet. */
export const emptyMessagingView = (): MessagingView => ({
  status: "loading", token: "", connecting: false, tokenError: null, confirmingDisconnect: false, busy: false,
});

const tr = t as unknown as (key: MessageKey) => string;

const STEPS: MessageKey[] = ["msg.tg.step1", "msg.tg.step2", "msg.tg.step3", "msg.tg.step4"];

function howTo(): string {
  const items = STEPS.map((key, i) => {
    const link =
      i === 0
        ? ` <a class="btn sm msg-link" href="${BOTFATHER_URL}" data-msg="botfather">${esc(t("msg.tg.openBotFather"))}${ico("link")}</a>`
        : "";
    return `<li>${esc(tr(key))}${link}</li>`;
  });
  return `<ol class="msg-steps">${items.join("")}</ol>`;
}

function setupHTML(v: MessagingView): string {
  const field = fieldHTML(t("msg.tg.tokenHint"), v.token, {
    id: "msg-token",
    type: "password",
    label: t("msg.tg.token"),
    invalid: v.tokenError !== null,
    disabled: v.connecting,
  });
  const go = btn(esc(v.connecting ? t("msg.tg.checking") : t("msg.tg.connect")), "pri", ` data-msg="connect"${v.connecting ? " disabled" : ""}`);
  return (
    `<div class="slist msg-setup">` +
    row(esc(t("msg.tg.setupTitle")), esc(t("msg.tg.setupLead"))) +
    `<div class="msg-howto">${howTo()}</div>` +
    `<div class="msg-token">${field}${go}</div>` +
    (v.tokenError ? `<div class="note" role="alert">${st("err", tr(v.tokenError))}</div>` : "") +
    `</div>`
  );
}

function statusLine(s: TelegramStatus): string {
  if (!s.enabled) return st("idle", t("msg.tg.off"));
  if (s.error === "unauthorized") return st("err", t("msg.tg.revoked"));
  if (s.error === "conflict") return st("warn", t("msg.tg.conflict"));
  if (s.error) return st("warn", t("msg.tg.offline"));
  return st("ok", t("msg.tg.on"));
}

function pendingRows(s: TelegramStatus, busy: boolean): string {
  if (!s.pending.length) {
    return row(esc(t("msg.tg.waitingTitle")), esc(t("msg.tg.waitingLead", { p0: s.bot?.username ? `@${s.bot.username}` : "" })));
  }
  return s.pending
    .map((p) =>
      row(
        `<span class="code msg-code">${esc(p.code)}</span>`,
        esc(t("msg.tg.pendingLead", { p0: p.name || t("msg.tg.someone"), p1: Math.max(1, Math.ceil(p.expires_in_s / 60)) })),
        btn(esc(t("msg.tg.reject")), "sm", ` data-msg="reject" data-code="${esc(p.code)}"${busy ? " disabled" : ""}`) +
          btn(esc(t("msg.tg.approve")), "sm pri", ` data-msg="approve" data-code="${esc(p.code)}"${busy ? " disabled" : ""}`),
        { cls: "msg-pending" },
      ),
    )
    .join("");
}

function connectedHTML(v: MessagingView, s: TelegramStatus): string {
  const bot = s.bot?.username ? `@${s.bot.username}` : t("msg.tg.bot");
  const allowed = s.allowed.length
    ? s.allowed.map((u) =>
        row(
          esc(u.name || t("msg.tg.someone")),
          `<span class="code">${esc(String(u.user_id))}</span>`,
          btn(esc(t("msg.tg.remove")), "sm", ` data-msg="remove" data-user="${u.user_id}" aria-label="${esc(t("msg.tg.removeUser", { p0: u.name || String(u.user_id) }))}"${v.busy ? " disabled" : ""}`),
        ),
      ).join("")
    : row(esc(t("msg.tg.nobody")), "");
  return (
    `<div class="slist msg-connected">` +
    row(esc(bot), statusLine(s), tg(s.enabled, t("msg.tg.switch"), ` data-msg="enable"${v.busy ? " disabled" : ""}`)) +
    `</div>` +
    `<h3 class="msg-sub">${esc(t("msg.tg.pairing"))}</h3><div class="slist">${pendingRows(s, v.busy)}</div>` +
    `<h3 class="msg-sub">${esc(t("msg.tg.allowed"))}</h3><div class="slist">${allowed}</div>` +
    `<div class="msg-foot">${btn(esc(v.confirmingDisconnect ? t("msg.tg.disconnectConfirm") : t("msg.tg.disconnect")), "", ` data-msg="disconnect"${v.busy ? " disabled" : ""}`)}</div>`
  );
}

/** The section: a heading, then Telegram's setup steps or, once connected, its state. */
export function messagingHTML(v: MessagingView): string {
  let body: string;
  if (v.status === "loading") body = `<p class="settings-loading" role="status">${esc(t("conn.loading"))}</p>`;
  else if (v.status === "failed") body = `<div class="alert core-note">${st("err", t("msg.loadFailed"))}</div>`;
  else body = v.status.connected ? connectedHTML(v, v.status) : setupHTML(v);
  return (
    `<section class="sgroup msg" data-msg-section><h2>${esc(t("msg.title"))}</h2>` +
    `<p class="foot msg-lead">${esc(t("msg.lead"))}</p>` +
    `<h3 class="msg-sub msg-brand"><span class="mono md logo-mono">${serviceIconSVG("telegram")}</span>Telegram</h3>${body}</section>`
  );
}

// ---------- state ----------

export interface MessagingHost {
  visible(): boolean;
  redraw(): void;
}

export interface Messaging {
  view(): MessagingView;
  load(): Promise<void>;
  click(target: Element): boolean;
  input(input: HTMLInputElement): boolean;
  key(e: KeyboardEvent): boolean;
  /** The panel is closed or leaves the tab: forget the draft token and stop asking. */
  reset(): void;
}

export function createMessaging(host: MessagingHost): Messaging {
  let status: MessagingView["status"] = "loading";
  let token = "";
  let connecting = false;
  let tokenError: MessageKey | null = null;
  let confirming = false;
  let busy = false;
  let confirmTimer: ReturnType<typeof setTimeout> | undefined;
  let poller: ReturnType<typeof setInterval> | undefined;
  let loads = 0;

  const redraw = (): void => {
    if (host.visible()) host.redraw();
  };
  const view = (): MessagingView => ({ status, token, connecting, tokenError, confirmingDisconnect: confirming, busy });

  function schedule(): void {
    clearInterval(poller);
    poller = undefined;
    // Only a connected bot has anything to wait for; a field being typed in is never redrawn.
    if (typeof status === "object" && status.connected && status.enabled) poller = setInterval(() => void refresh(), POLL_MS);
  }

  /** Takes a new status; the page is drawn again only if something changed. */
  function take(next: TelegramStatus): void {
    const shape = (s: TelegramStatus): string => JSON.stringify({ ...s, pending: s.pending.map((p) => [p.code, p.name]) });
    const same = typeof status === "object" && shape(status) === shape(next);
    status = next;
    schedule();
    if (!same) redraw();
  }

  async function refresh(): Promise<void> {
    if (!host.visible()) return;
    const mine = ++loads;
    try {
      const next = await api.telegramStatus();
      if (mine === loads && !busy) take(next);
    } catch {
      // a poll that fails is tried again; the state on screen stays
    }
  }

  async function load(): Promise<void> {
    const mine = ++loads;
    try {
      const next = await api.telegramStatus();
      if (mine !== loads) return;
      status = next;
      schedule();
    } catch {
      if (mine !== loads) return;
      if (typeof status !== "object") status = "failed";
    }
    redraw();
  }

  async function connect(): Promise<void> {
    if (connecting || !token.trim()) {
      if (!token.trim()) {
        tokenError = "msg.tg.needToken";
        redraw();
      }
      return;
    }
    connecting = true;
    tokenError = null;
    redraw();
    try {
      const reply = await api.telegramConnect(token.trim());
      if (reply.ok) {
        token = ""; // it is in the vault now: nothing of it stays on this page
        status = reply.status;
        schedule();
        toaster.info(t("msg.tg.connected", { p0: reply.status.bot?.username ? `@${reply.status.bot.username}` : "Telegram" }));
      } else {
        tokenError = reply.reason === "network" ? "msg.tg.networkError" : "msg.tg.badToken";
      }
    } catch (err) {
      toaster.info(describeError(err));
    }
    connecting = false;
    redraw();
  }

  async function act(work: () => Promise<TelegramStatus | null>): Promise<void> {
    busy = true;
    redraw();
    try {
      const next = await work();
      if (next) {
        status = next;
        schedule();
      }
    } catch (err) {
      toaster.info(describeError(err));
    }
    busy = false;
    redraw();
  }

  function click(target: Element): boolean {
    const button = target.closest<HTMLElement>("[data-msg]");
    if (!button) return false;
    switch (button.dataset.msg) {
      case "botfather":
        void api.openLink(BOTFATHER_URL).catch((err) => toaster.info(describeError(err)));
        return true;
      case "connect":
        void connect();
        return true;
      case "enable":
        if (typeof status === "object") {
          const on = !status.enabled;
          void act(() => api.telegramSetEnabled(on));
        }
        return true;
      case "approve": {
        const code = button.dataset.code ?? "";
        void act(async () => {
          const reply = await api.telegramApprove(code);
          if (!reply.approved) toaster.info(t("msg.tg.codeGone"));
          return reply.status;
        });
        return true;
      }
      case "reject":
        void act(() => api.telegramReject(button.dataset.code ?? ""));
        return true;
      case "remove":
        void act(() => api.telegramRemoveUser(Number(button.dataset.user)));
        return true;
      case "disconnect":
        if (!confirming) {
          confirming = true;
          clearTimeout(confirmTimer);
          confirmTimer = setTimeout(() => {
            confirming = false;
            redraw();
          }, DISCONNECT_CONFIRM_MS);
          redraw();
          return true;
        }
        clearTimeout(confirmTimer);
        confirming = false;
        void act(async () => {
          const next = await api.telegramDisconnect();
          toaster.info(t("msg.tg.disconnected"));
          return next;
        });
        return true;
    }
    return false;
  }

  function input(el: HTMLInputElement): boolean {
    if (el.id !== "msg-token") return false;
    token = el.value;
    if (tokenError) {
      // Taken off the page in place: drawing it again would drop the focus from the field.
      tokenError = null;
      el.closest(".msg")?.querySelectorAll(".msg-setup > .note[role=alert]").forEach((n) => n.remove());
      el.closest("label")?.removeAttribute("data-invalid");
    }
    return true;
  }

  function key(e: KeyboardEvent): boolean {
    const el = e.target as HTMLElement | null;
    if (e.key !== "Enter" || el?.id !== "msg-token") return false;
    e.preventDefault();
    void connect();
    return true;
  }

  function reset(): void {
    clearTimeout(confirmTimer);
    clearInterval(poller);
    poller = undefined;
    confirming = false;
    token = "";
    tokenError = null;
    loads++;
  }

  return { view, load, click, input, key, reset };
}
