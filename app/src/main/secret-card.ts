// A secret card: when a task in a chat needs a key that a connection declares and has none of, that turn
// shows the connection's card. The owner types the key into its masked field and saves it: it goes from the
// window straight to the core's vault (`integrations.card_save`) and nowhere else, and the task then resumes
// once. "Saved" and "resumed" are different things, and the card says which: a saved key whose task did not
// continue offers Try again, which resumes without asking for the key again.
// Every state comes from the core (`connectors.secret_cards`). Nothing here keeps what was typed: the field is
// read when Save is pressed and emptied at once, and a card drawn again takes the characters from the old
// field to the new one (`carryTyped`) so that a redraw does not cost what the owner was typing.

import { clausesHTML } from "./connector-card";
import { NAME, eventLineHTML } from "./event-line";
import { t } from "./i18n";
import type { MessageKey } from "./i18n/zh-TW";
import { SERVICE_ICONS, type MonoIconId, brandStyle, hasLogo, serviceIconOf, serviceIconSVG, serviceLogoHTML } from "./service-icons";
import type { AgentTurn, SecretCard } from "./types";
import { btn, esc, ico } from "./ui";

const FAILURES: Record<string, MessageKey> = {
  invalid: "card.keyInvalid",
  too_long: "card.keyTooLong",
  vault: "card.keyVault",
  gone: "card.keyGone",
};

const plain = (key: MessageKey): string => (t as (key: MessageKey) => string)(key);

/** The connection's mark: its own, bare, when its name says which program it is, else the generic plug (a line's icon, with `inline`). */
export function connectionMarkHTML(name: string, inline = false): string {
  const id = serviceIconOf({ name });
  if (id && hasLogo(id)) return `<span class="mono md logo-art">${serviceLogoHTML(id)}</span>`;
  if (id && id in SERVICE_ICONS) {
    const mono = id as MonoIconId;
    return `<span class="mono md logo-mono"${brandStyle(mono)}>${serviceIconSVG(mono)}</span>`;
  }
  return inline ? ico("plug") : `<span class="mono md" aria-hidden="true">${ico("plug")}</span>`;
}

/** Whether the card still waits for the owner to type the key. */
const typing = (card: SecretCard): boolean => card.state === "pending" || (card.state === "failed" && card.error !== "gone");

/** What the card says of where its key stands (the secret's own name while it waits). */
export function secretStateText(card: SecretCard): string {
  switch (card.state) {
    case "pending": return card.secret;
    case "failed": return plain(FAILURES[card.error ?? ""] ?? "card.keyVault");
    case "saved": return plain(card.error ? "card.keyNotResumed" : "card.keySaved");
    case "resuming": return plain("card.keyResuming");
    case "resumed": return plain("card.keyResumed");
    case "expired": return plain("card.expired");
    default: return plain("card.stopped");
  }
}

/** The field and Save (or Try again), for the states that have an action. */
function actionHTML(card: SecretCard): string {
  const on = (act: string): string => ` data-act="${act}" data-card="${esc(card.id)}"`;
  if (typing(card)) {
    const label = `${card.name} ${card.secret}`;
    return `<div class="sc-form"><label class="field sc-field">${ico("lock")}` +
      `<input type="password" data-secret-input="${esc(card.id)}" placeholder="${esc(t("card.keyField"))}" aria-label="${esc(label)}" ` +
      `autocomplete="off" spellcheck="false" autocapitalize="off"></label>` +
      btn(esc(t("card.keySave")), "sm pri", `${on("card-save")} disabled`) + `</div>`;
  }
  if (card.state === "saved" && card.error) return `<div class="cc-actions">${btn(esc(t("card.retry")), "sm pri", on("card-resume"))}</div>`;
  return "";
}

export function secretCardHTML(card: SecretCard): string {
  const busy = card.state === "resuming" ? `<span class="spin" aria-hidden="true"></span>` : "";
  const done = card.state === "saved" && !card.error || card.state === "resumed" ? `<span class="cc-done">${ico("check")}</span>` : "";
  const action = actionHTML(card);
  const second = typing(card) || (card.state === "saved" && !!card.error); // the field, or Try again, on a second row
  return `<div class="connector-card secret-card${second ? " has-form" : ""}" data-secret-card="${esc(card.id)}" data-card-state="${esc(card.state)}"` +
    `${card.error ? ` data-card-error="${esc(card.error)}"` : ""} role="group" aria-label="${esc(card.name)}">` +
    connectionMarkHTML(card.name) +
    `<div class="cc-meta"><div class="cc-name">${esc(card.name)}</div>` +
    `<div class="cc-state" aria-live="polite">${busy}${done}<span>${clausesHTML(secretStateText(card))}</span></div></div>` +
    action + `</div>`;
}

/** The cards of the keys a turn waits for, below what it said. */
export function secretCardsHTML(turn: AgentTurn): string {
  return (turn.secrets ?? []).map(secretCardHTML).join("");
}

/** The centred line a task resumed after its key was saved opens with, in place of the owner's words it carries on. */
export function secretResumedLineHTML(turn: AgentTurn): string {
  const names = turn.resumed_secrets ?? [];
  if (!names.length) return "";
  return `<div class="conversation-event-line" data-resumed="${esc(turn.id)}">` +
    eventLineHTML(t("card.keyResumedLine", { p0: NAME }), connectionMarkHTML(names[0], true), names.join(" · ")) + `</div>`;
}

/**
 * A card drawn again keeps what the owner was typing: the characters of each field of `before` go to the
 * field of the same card in `after`, which takes the cursor too if it had it. Call the result once `after`
 * is in the page. The characters pass from one field to the next and are kept nowhere else.
 */
export function carryTyped(before: Element, after: Element): () => void {
  const kept: { id: string; value: string; focused: boolean; start: number | null; end: number | null }[] = [];
  for (const old of before.querySelectorAll<HTMLInputElement>("input[data-secret-input]")) {
    if (!old.value) continue;
    kept.push({ id: old.dataset.secretInput ?? "", value: old.value, focused: old === document.activeElement, start: old.selectionStart, end: old.selectionEnd });
  }
  for (const item of kept) {
    const fresh = [...after.querySelectorAll<HTMLInputElement>("input[data-secret-input]")].find((input) => input.dataset.secretInput === item.id);
    if (!fresh) continue;
    fresh.value = item.value;
    const save = fresh.closest(".secret-card")?.querySelector<HTMLButtonElement>('[data-act="card-save"]');
    if (save) save.disabled = false;
  }
  return () => {
    for (const item of kept) {
      if (!item.focused) continue;
      const fresh = [...after.querySelectorAll<HTMLInputElement>("input[data-secret-input]")].find((input) => input.dataset.secretInput === item.id);
      if (!fresh) continue;
      fresh.focus({ preventScroll: true });
      if (item.start !== null && item.end !== null) fresh.setSelectionRange(item.start, item.end);
    }
  };
}
