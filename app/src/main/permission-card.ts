// A permission card: when a step in a typed private chat needs the owner's yes (a connection's tool that sends or
// changes something, the first use of an app on the computer, a step that sends, deletes or buys), that turn shows
// the exact action in plain words with 允許一次, 一律允許 and 拒絕. Always allow is a narrow rule for that kind of
// action only, and it is offered only where it can take effect: where one of the owner's rules asks first, that rule
// has the last word and the card has no 一律允許. A yes is for this action alone (its digest); a card whose time ran
// out, whose action changed or that was answered answers nothing.
// Every state comes from the core (`agent.permissions`). Nothing here keeps what the action was given: the card says
// it in the core's words. The question itself is the one the window already holds (the shell keeps it, `source`
// "ask"), or the card's own door for a task that went on after the computer was given back (`source` "run").

import { clausesHTML } from "./connector-card";
import { t } from "./i18n";
import type { MessageKey } from "./i18n/zh-TW";
import { connectionMarkHTML } from "./secret-card";
import type { AgentTurn, PermissionCard } from "./types";
import { btn, esc, ico } from "./ui";

const plain = (key: MessageKey): string => (t as (key: MessageKey) => string)(key);

/** Whether the card still waits for the owner's answer. */
export const waitsForYes = (card: PermissionCard): boolean => card.state === "pending";

/** The card that waits for the answer to question `id`, if a turn shows one. */
export function permissionFor(turns: readonly AgentTurn[], id: string): PermissionCard | undefined {
  for (const turn of turns) {
    const card = turn.permissions?.find((item) => item.id === id && waitsForYes(item));
    if (card) return card;
  }
  return undefined;
}

/** What the card says of where its question stands: the action itself while it waits, else what became of it. */
export function permissionStateText(card: PermissionCard): string {
  switch (card.state) {
    case "pending": return card.summary;
    case "allowed": return plain("perm.allowed");
    case "always": return plain("perm.alwaysDone");
    case "denied": return plain("perm.denied");
    case "expired": return plain("perm.expired");
    default: return plain("perm.cancelled");
  }
}

/** Who asks: the connection's name, or the computer when the step is on it. */
const nameOf = (card: PermissionCard): string => (card.kind === "tool" && card.service ? card.service : t("card.pcTitle"));

/** The mark: the connection's own, bare, or the computer's icon. */
const markOf = (card: PermissionCard): string =>
  card.kind === "tool" && card.service ? connectionMarkHTML(card.service) : `<span class="mono md" aria-hidden="true">${ico("monitor")}</span>`;

/** The answers the card can take: 拒絕 first, the primary 允許一次 last; 一律允許 only where it can take effect. */
function actionHTML(card: PermissionCard, answerable: boolean): string {
  if (!waitsForYes(card)) return "";
  const on = (act: string): string => ` data-act="${act}" data-card="${esc(card.id)}" data-source="${esc(card.source)}"${answerable ? "" : " disabled"}`;
  return `<div class="cc-actions">` +
    btn(esc(t("perm.deny")), "sm", on("perm-deny")) +
    (card.always ? btn(esc(t("perm.always")), "sm", on("perm-always")) : "") +
    btn(esc(t("perm.once")), "sm pri", on("perm-once")) + `</div>`;
}

/**
 * One card. `answerable` is false while the window holds no question to answer it with (an `ask` card whose
 * question has not come yet, or is being answered): the buttons are there and cannot be pressed.
 */
export function permissionCardHTML(card: PermissionCard, answerable = true): string {
  const waiting = waitsForYes(card);
  const done = card.state === "allowed" || card.state === "always" ? `<span class="cc-done">${ico("check")}</span>` : "";
  const text = permissionStateText(card);
  return `<div class="connector-card permission-card${waiting ? " has-form" : ""}" data-permission-card="${esc(card.id)}" data-card-state="${esc(card.state)}"` +
    ` data-source="${esc(card.source)}" data-kind="${esc(card.kind)}" data-digest="${esc(card.digest)}" role="group" aria-label="${esc(t("perm.title"))}">` +
    markOf(card) +
    `<div class="cc-meta"><div class="cc-name">${esc(nameOf(card))}</div>` +
    `<div class="cc-state cc-ask" aria-live="polite">${done}<span>${waiting ? esc(text) : clausesHTML(text)}</span></div>` +
    // A settled card keeps the action it was about; a waiting one says which rule had it asked.
    (waiting ? (card.reason ? `<div class="cc-state cc-hint"><span>${esc(card.reason)}</span></div>` : "")
      : `<div class="cc-state cc-hint cc-was"><span>${esc(card.summary)}</span></div>`) +
    `</div>` + actionHTML(card, answerable) + `</div>`;
}

/** The cards a turn shows, below what it said. `answerable` says whether the window can answer a question by its id now. */
export function permissionCardsHTML(turn: AgentTurn, answerable: (id: string) => boolean = () => true): string {
  return (turn.permissions ?? []).map((card) => permissionCardHTML(card, card.source === "run" || answerable(card.id))).join("");
}
