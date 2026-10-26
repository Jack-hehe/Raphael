// A connector card: when a task in a chat needs an app that is not connected (Gmail, Google Calendar),
// that turn shows the app's card. The owner signs in from it; the core then resumes that task once,
// as a turn of its own that opens with one centred line naming the apps it waited for.
// Every state comes from the core (`connectors.cards`); nothing here keeps a URL or a token.

import { NAME, eventLineHTML } from "./event-line";
import { getLocale, t } from "./i18n";
import type { MessageKey } from "./i18n/zh-TW";
import { brandStyle, serviceIconSVG, type MonoIconId } from "./service-icons";
import type { AgentTurn, ConnectorCard } from "./types";
import { btn, esc, ico } from "./ui";

/** The Simple Icons mark of each app a card can name. */
const MARKS: Record<string, MonoIconId> = { gmail: "gmail", googlecalendar: "googlecalendar" };

const FAILURES: Record<string, MessageKey> = {
  denied: "card.denied",
  timeout: "card.timeout",
  scope: "card.scope",
  state: "card.state",
  not_configured: "card.notConfigured",
  interrupted: "card.interrupted",
};

const plain = (key: MessageKey): string => (t as (key: MessageKey) => string)(key);

/** The app's bare mark: its brand path in its brand colour, or the generic plug. */
export function appMarkHTML(app: string): string {
  const mark = MARKS[app];
  return mark
    ? `<span class="mono md logo-mono"${brandStyle(mark)}>${serviceIconSVG(mark)}</span>`
    : `<span class="mono md" aria-hidden="true">${ico("plug")}</span>`;
}

/** What the card says of where its sign-in stands. */
export function cardStateText(card: ConnectorCard): string {
  switch (card.state) {
    case "pending": return plain(card.reconnect ? "card.expiredAuth" : "card.pending");
    case "authorizing": return plain("card.authorizing");
    case "connected": return t(card.reconnect ? "card.reconnected" : "card.connected", { p0: card.tool_count ?? 0 });
    case "cancelled": return plain("card.cancelled");
    case "expired": return plain("card.expired");
    default: return plain(FAILURES[card.error ?? ""] ?? "card.error");
  }
}

/** The card's one action, if its state has one. */
function actionHTML(card: ConnectorCard): string {
  const on = (act: string): string => ` data-act="${act}" data-card="${esc(card.id)}"`;
  switch (card.state) {
    case "pending": return btn(esc(plain(card.reconnect ? "card.reconnect" : "card.connect")), "sm pri", on("card-connect"));
    case "authorizing": return btn(esc(plain("card.cancel")), "sm", on("card-cancel"));
    case "cancelled": return btn(esc(plain("card.connect")), "sm pri", on("card-connect"));
    case "failed": return card.error === "not_configured"
      ? btn(esc(plain("card.openConnections")), "sm", on("card-settings"))
      : btn(esc(plain("card.retry")), "sm pri", on("card-connect"));
    default: return "";
  }
}

export function connectorCardHTML(card: ConnectorCard): string {
  const busy = card.state === "authorizing" ? `<span class="spin" aria-hidden="true"></span>` : "";
  const done = card.state === "connected" ? `<span class="cc-done">${ico("check")}</span>` : "";
  const action = actionHTML(card);
  return `<div class="connector-card" data-connector="${esc(card.id)}" data-card-state="${esc(card.state)}" role="group" aria-label="${esc(card.name)}">` +
    appMarkHTML(card.app) +
    `<div class="cc-meta"><div class="cc-name">${esc(card.name)}</div>` +
    `<div class="cc-state" aria-live="polite">${busy}${done}<span>${esc(cardStateText(card))}</span></div></div>` +
    (action ? `<div class="cc-actions">${action}</div>` : "") +
    `</div>`;
}

/** The cards a turn showed, below what it said. */
export function connectorCardsHTML(turn: AgentTurn): string {
  return (turn.connectors ?? []).map(card => `<div class="chat-message" data-message="connector">${connectorCardHTML(card)}</div>`).join("");
}

/** The brand names of the apps (`connectors/apps.py`), never translated. */
const APP_NAMES: Record<string, string> = { gmail: "Gmail", googlecalendar: "Google Calendar" };

/** The centred line a resumed task opens with, in place of the owner's words it carries on. */
export function resumedLineHTML(turn: AgentTurn): string {
  const apps = turn.resumed_apps ?? [];
  if (!apps.length) return "";
  const names = apps.map(app => APP_NAMES[app] ?? app).join(getLocale() === "en" ? ", " : "、");
  return `<div class="conversation-event-line" data-resumed="${esc(turn.id)}">` +
    eventLineHTML(t("card.resumed", { p0: NAME }), appMarkHTML(apps[0]), names) + `</div>`;
}
