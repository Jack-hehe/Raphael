// Telegram, in the 拉斐爾 tab of Settings: Raphael can be reached from a phone. It is drawn with the
// rows of every other tab, name on the left and controls on the right. Connected, it is one row (the
// mark, the bot, a dot for its state, a switch) that opens on a row for each pairing code and each
// person who may talk to her; before it is set up, a row with a way to BotFather and a row with the
// token field. `messagingHTML` draws it from a snapshot (pure, so its rules can be tested without a
// browser); `createMessaging` holds the state and does what the buttons say, through the core. The
// bot token is typed here, sent to the core once and dropped: it is kept in the vault and never comes
// back to this page. Words are kept to what cannot be shown: errors, the codes and the confirmation
// of 中斷連線; every icon button says what it does in its label and tooltip.

import * as api from "./api";
import { describeError } from "./format";
import { t } from "./i18n";
import type { MessageKey } from "./i18n/zh-TW";
import { monogram, serviceIconSVG } from "./service-icons";
import { DISCONNECT_CONFIRM_MS, btn, esc, fieldHTML, ico, iconButton, note, row, st, tg, toaster } from "./ui";

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
  allowed: { user_id: string; name: string }[];
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
  /** The reader opened or closed the pairing and the people; null: as the state suggests. */
  expanded: boolean | null;
}

/** A view of nothing read yet. */
export const emptyMessagingView = (): MessagingView => ({
  status: "loading", token: "", connecting: false, tokenError: null, confirmingDisconnect: false, busy: false, expanded: null,
});

const tr = t as unknown as (key: MessageKey) => string;

/** The pairing and the people are open when someone waits, or before anyone may talk to her. */
export const isExpanded = (v: MessagingView, s: TelegramStatus): boolean => v.expanded ?? (s.pending.length > 0 || s.allowed.length === 0);

/** The state of a connected bot: a dot that names itself, and the words only for a fault. */
function state(s: TelegramStatus): { dot: "ok" | "warn" | "err" | "idle"; label: string; fault: boolean } {
  if (!s.enabled) return { dot: "idle", label: t("msg.tg.off"), fault: false };
  if (s.error === "unauthorized") return { dot: "err", label: t("msg.tg.revoked"), fault: true };
  if (s.error === "conflict") return { dot: "warn", label: t("msg.tg.conflict"), fault: true };
  if (s.error) return { dot: "warn", label: t("msg.tg.offline"), fault: true };
  return { dot: "ok", label: t("msg.tg.on"), fault: false };
}

const MARK = `<span class="mono md logo-mono msg-mark" aria-hidden="true">${serviceIconSVG("telegram")}</span>`;
const dotHTML = (dot: string, label: string): string => `<span class="dot ${dot}" role="img" aria-label="${esc(label)}" title="${esc(label)}"></span>`;

/** The name of the group's first row: the mark, the bot (or Telegram) and a dot for its state. */
const headName = (name: string, dot: string): string => `<span class="msg-name">${MARK}<span class="msg-bot">${esc(name)}</span>${dot}</span>`;

function setupRows(v: MessagingView): string[] {
  const field = fieldHTML(t("msg.tg.tokenHint"), v.token, {
    id: "msg-token",
    type: "password",
    label: t("msg.tg.token"),
    invalid: v.tokenError !== null,
    disabled: v.connecting,
  });
  const go = btn(esc(v.connecting ? t("msg.tg.checking") : t("msg.tg.connect")), "pri", ` data-msg="connect"${v.connecting ? " disabled" : ""}`);
  const botfather = btn(`${ico("bot")}<span>BotFather</span>${ico("link")}`, "", ` data-msg="botfather" title="${esc(t("msg.tg.step1"))}"`);
  return [
    row(headName("Telegram", dotHTML("idle", t("msg.tg.notSet"))), "", botfather, { cls: "msg-head" }),
    row(esc(t("msg.tg.token")), "", `<span class="msg-token">${field}${go}</span>`, { cls: "msg-token-row" }),
    ...(v.tokenError ? [note(st("err", tr(v.tokenError)), ' role="alert"')] : []),
  ];
}

function detailRows(v: MessagingView, s: TelegramStatus): string[] {
  const off = v.busy ? " disabled" : "";
  const pending = s.pending.map((p) => {
    const who = p.name || t("msg.tg.someone");
    const minutes = Math.max(1, Math.ceil(p.expires_in_s / 60));
    return row(
      `<span class="msg-pair"><span class="code msg-code">${esc(p.code)}</span><span class="msg-who">${esc(who)}</span>` +
        `<span class="msg-mins num" title="${esc(t("msg.tg.pendingLead", { p0: who, p1: minutes }))}">${ico("clock")}${esc(t("msg.tg.minutes", { p0: minutes }))}</span></span>`,
      "",
      iconButton("x", t("msg.tg.rejectCode", { p0: p.code }), ` data-msg="reject" data-code="${esc(p.code)}"${off}`) +
        iconButton("check", t("msg.tg.approveCode", { p0: p.code }), ` data-msg="approve" data-code="${esc(p.code)}"${off}`, "pri"),
      { cls: "msg-sub" },
    );
  });
  const people = s.allowed.map((u) => {
    const name = u.name || String(u.user_id);
    return row(
      `<span class="msg-pair"><span class="msg-avatar" aria-hidden="true">${monogram(name)}</span><span class="msg-who" title="${esc(String(u.user_id))}">${esc(name)}</span></span>`,
      "",
      iconButton("x", t("msg.tg.removeUser", { p0: name }), ` data-msg="remove" data-user="${u.user_id}"${off}`),
      { cls: "msg-sub" },
    );
  });
  return [...pending, ...people];
}

/** Telegram: its rows, then under the card the one line before anyone has paired and 中斷連線. */
export function messagingHTML(v: MessagingView): string {
  const group = (rows: string[], after = ""): string =>
    `<section class="sgroup msg" data-msg-section aria-label="Telegram"><div class="slist">${rows.join("")}</div>${after}</section>`;
  if (v.status === "loading") {
    return group([row(headName("Telegram", ""), "", `<span class="spin" role="status" aria-label="${esc(t("conn.loading"))}"></span>`, { cls: "msg-head" })]);
  }
  if (v.status === "failed") return group([row(headName("Telegram", ""), "", "", { cls: "msg-head" }), note(st("err", t("msg.loadFailed")), ' role="alert"')]);
  if (!v.status.connected) return group(setupRows(v), `<p class="foot">${esc(t("msg.tg.setupLine"))}</p>`);
  const s = v.status;
  const { dot, label, fault } = state(s);
  const open = isExpanded(v, s);
  const bot = s.bot?.username ? `@${s.bot.username}` : t("msg.tg.bot");
  const badge = s.pending.length ? `<span class="msg-badge num" title="${esc(t("msg.tg.pairing"))}">${s.pending.length}</span>` : "";
  const head = row(
    headName(bot, dotHTML(dot, label) + badge),
    "",
    tg(s.enabled, t("msg.tg.switch"), ` data-msg="enable"${v.busy ? " disabled" : ""}`) +
      iconButton("down", t("msg.tg.details"), ` data-msg="expand" aria-expanded="${open}"`, "msg-more"),
    { cls: "msg-head" },
  );
  const rows = [head, ...(fault ? [note(st(dot === "err" ? "err" : "warn", label), ' role="alert"')] : []), ...(open ? detailRows(v, s) : [])];
  // Before anyone has paired there is nothing to show but how to begin: the one line that says it.
  const waiting = open && !s.pending.length && !s.allowed.length ? `<p class="foot">${esc(t("msg.tg.waitingLead", { p0: bot }))}</p>` : "";
  const disconnect = open
    ? `<div class="msg-foot">${btn(esc(v.confirmingDisconnect ? t("msg.tg.disconnectConfirm") : t("msg.tg.disconnect")), v.confirmingDisconnect ? "sm danger" : "sm", ` data-msg="disconnect"${v.busy ? " disabled" : ""}`)}</div>`
    : "";
  return group(rows, waiting + disconnect);
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
  let expanded: boolean | null = null;
  let confirmTimer: ReturnType<typeof setTimeout> | undefined;
  let poller: ReturnType<typeof setInterval> | undefined;
  let loads = 0;

  const redraw = (): void => {
    if (host.visible()) host.redraw();
  };
  const view = (): MessagingView => ({ status, token, connecting, tokenError, confirmingDisconnect: confirming, busy, expanded });

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
      case "expand":
        if (typeof status === "object") {
          expanded = !isExpanded(view(), status);
          redraw();
        }
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
        void act(() => api.telegramRemoveUser(button.dataset.user ?? ""));
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
      el.closest(".msg")?.querySelectorAll(".note[role=alert]").forEach((n) => n.remove());
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
    expanded = null;
    loads++;
  }

  return { view, load, click, input, key, reset };
}
