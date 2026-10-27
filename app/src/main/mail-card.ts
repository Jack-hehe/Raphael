// An email card: when a member writes an email for the owner it is not sent, it is a draft in the owner's private
// chat: 寄件者 (the app that sends it), 收件者, 副本, 主旨 and the body, all editable, with 傳送 and 捨棄. Only 傳送
// sends it, once, and the version the card holds is the one that goes (each edit makes a new version with a new
// digest; the core sends the digest it is given and no other). 捨棄 changes nothing outside the app. A member can
// offer the draft to a colleague with a choice card (「這封回信可以交給…寄嗎？」); on yes the first chat keeps one
// centred line and the same version is editable in the colleague's chat. Handing a draft over never sends it.
// Every state comes from the core (`agent.drafts`). What the owner types lives in the fields only until it is
// saved as a version (`mail-actions.ts`); a card drawn again takes it from the old field to the new one (`carryMail`).

import { NAME, eventLineHTML } from "./event-line";
import { t } from "./i18n";
import type { MessageKey } from "./i18n/zh-TW";
import { connectionMarkHTML } from "./secret-card";
import { serviceIconOf } from "./service-icons";
import type { AgentTurn, MailDraft, MailDraftCard, MailDraftHanded } from "./types";
import { btn, esc, ico, sel } from "./ui";

const plain = (key: MessageKey): string => (t as (key: MessageKey) => string)(key);

/** The fields the owner edits, in the order the card shows them. */
export const MAIL_FIELDS = ["to", "cc", "subject", "body"] as const;
export type MailField = (typeof MAIL_FIELDS)[number];

const ERRORS: Record<string, MessageKey> = {
  no_sender: "mail.noSender",
  invalid_recipient: "mail.errRecipient",
  cc_unsupported: "mail.errCc",
  empty: "mail.errEmpty",
  forbidden: "mail.errForbidden",
  interrupted: "mail.errInterrupted",
};

/** What the page gives the card besides the draft: the small face of a colleague, where it draws one. */
export interface MailLook {
  memberFace?: (id: number, size?: number) => string;
}

/** Whether the owner can still edit it: a draft, or a send of it that failed (an edit makes a new version, which can go). */
export const mailEditable = (draft: MailDraftCard): boolean => draft.state === "draft" || draft.state === "failed";

/** Why it was not sent, in words; "" when nothing is wrong. */
export function mailErrorText(draft: MailDraftCard): string {
  if (draft.state === "failed") return plain(draft.error === "interrupted" ? "mail.errInterrupted" : "mail.errFailed");
  if (!draft.error) return "";
  return plain(ERRORS[draft.error] ?? "mail.errOther");
}

/** The mark of the app that sends it: its own, bare, when it is a known one, else an envelope. */
export function senderMarkHTML(name: string): string {
  return name && serviceIconOf({ name }) ? connectionMarkHTML(name) : `<span class="mono md" aria-hidden="true">${ico("mail")}</span>`;
}

/** The recipients as one short line: the first, and how many more. */
function recipientsLine(to: string): string {
  const all = to.split(/[,;\s]+/u).filter(Boolean);
  return all.length > 1 ? `${all[0]} +${all.length - 1}` : all[0] ?? "";
}

function fieldRow(draft: MailDraftCard, field: Exclude<MailField, "body">, label: string, editable: boolean): string {
  const id = `${draft.id}-${field}`;
  return `<label class="mail-row" for="${esc(id)}"><span class="mail-k">${esc(label)}</span>` +
    `<input id="${esc(id)}" class="mb-fin mail-in" data-mail-field="${field}" value="${esc(draft[field])}" maxlength="${field === "subject" ? 300 : 1000}"` +
    ` autocomplete="off" spellcheck="false"${field === "subject" ? "" : ' inputmode="email" autocapitalize="off"'}${editable ? "" : " disabled"}></label>`;
}

function senderRow(draft: MailDraftCard, editable: boolean): string {
  const choices = draft.senders.map((sender) => ({ value: sender.key, label: sender.name }));
  const known = draft.senders.some((sender) => sender.key === draft.from.key);
  const label = plain("mail.from");
  const control = draft.senders.length === 0 || !known
    ? `<span class="mail-none">${esc(plain("mail.noSender"))}</span>`
    : sel(choices, draft.from.key, label, ` data-act="mail-from" data-via="${esc(draft.from.key)}"${editable ? "" : " disabled"}`);
  return `<div class="mail-row"><span class="mail-k">${esc(label)}</span>${control}</div>`;
}

/** The editable card, or while it is sent, the same card with its fields still and a ring. */
function editableHTML(draft: MailDraftCard): string {
  const editable = mailEditable(draft);
  const sending = draft.state === "sending";
  const note = mailErrorText(draft);
  const canSend = draft.state === "draft" && draft.senders.some((sender) => sender.key === draft.from.key);
  return `<div class="mail-card" data-mail-card="${esc(draft.id)}" data-mail-state="${esc(draft.state)}" data-digest="${esc(draft.digest)}"` +
    ` data-version="${draft.version}" data-from="${esc(draft.from.key)}" data-senders="${esc(JSON.stringify(draft.senders))}"` +
    ` role="group" aria-label="${esc(plain("mail.title"))}">` +
    `<div class="mail-head">${senderMarkHTML(draft.from.name)}<b class="mail-title">${esc(plain("mail.title"))}</b>` +
    (draft.version > 1 ? `<span class="mail-ver">${esc(t("mail.version", { p0: draft.version }))}</span>` : "") +
    (draft.author_name ? `<span class="mail-by">${esc(t("mail.handedBy", { p0: draft.author_name }))}</span>` : "") + `</div>` +
    `<div class="mail-rows">` + senderRow(draft, editable) +
    fieldRow(draft, "to", plain("mail.to"), editable) + fieldRow(draft, "cc", plain("mail.cc"), editable) +
    fieldRow(draft, "subject", plain("mail.subject"), editable) + `</div>` +
    `<textarea class="mb-fin mb-ta mail-body" data-mail-field="body" aria-label="${esc(plain("mail.body"))}" rows="7" maxlength="50000"` +
    ` spellcheck="false"${editable ? "" : " disabled"}>${esc(draft.body)}</textarea>` +
    (note ? `<div class="mail-note" role="status" data-mail-error="${esc(draft.error ?? draft.state)}">${esc(note)}</div>` : "") +
    (sending ? `<div class="mail-note is-busy" role="status"><span class="spin" aria-hidden="true"></span><span>${esc(plain("mail.sending"))}</span></div>` : "") +
    (sending ? "" : `<div class="mail-actions">` +
      btn(esc(plain("mail.discard")), "sm", ` data-act="mail-discard" data-mail="${esc(draft.id)}"`) +
      btn(esc(plain("mail.send")), "sm pri", ` data-act="mail-send" data-mail="${esc(draft.id)}"${canSend ? "" : " disabled"}`) + `</div>`) +
    `</div>`;
}

/** A draft that is over, in one quiet line: what it was about and what became of it. */
function settledHTML(draft: MailDraftCard): string {
  const sent = draft.state === "sent";
  const words = sent ? t("mail.sent", { p0: recipientsLine(draft.to) }) : plain("mail.discarded");
  const done = sent ? `<span class="cc-done">${ico("check")}</span>` : "";
  return `<div class="connector-card mail-done" data-mail-card="${esc(draft.id)}" data-mail-state="${esc(draft.state)}" role="group" aria-label="${esc(plain("mail.title"))}">` +
    senderMarkHTML(draft.from.name) +
    `<div class="cc-meta"><div class="cc-name">${esc(draft.subject || plain("mail.title"))}</div>` +
    `<div class="cc-state" aria-live="polite">${done}<span>${esc(words)}</span></div></div></div>`;
}

/** The choice card of a draft offered to a colleague: they may send it, or not. Answering it never sends the mail. */
function offerHTML(draft: MailDraftCard, look: MailLook): string {
  if (!draft.offer || draft.state !== "draft") return "";
  const on = (act: string): string => ` data-act="${act}" data-mail="${esc(draft.id)}"`;
  return `<div class="connector-card mail-offer" data-mail-offer="${esc(draft.id)}" role="group" aria-label="${esc(draft.offer.name)}">` +
    `<span class="mail-face">${look.memberFace?.(draft.offer.member_id, 26) ?? ""}</span>` +
    `<div class="cc-meta"><div class="cc-name mail-ask">${esc(t("mail.handover", { p0: draft.offer.name }))}</div></div>` +
    `<div class="cc-actions">${btn(esc(plain("mail.handoverNo")), "sm", on("mail-handover-no"))}${btn(esc(plain("mail.handoverYes")), "sm pri", on("mail-handover-yes"))}</div></div>`;
}

export function mailCardHTML(draft: MailDraftCard, look: MailLook = {}): string {
  const over = draft.state === "sent" || draft.state === "discarded";
  return (over ? settledHTML(draft) : editableHTML(draft)) + offerHTML(draft, look);
}

/** The centred line the first chat of a draft handed to a colleague keeps: 「已轉交給 ◎ 焦媽媽」. */
export function mailHandedLineHTML(draft: MailDraftHanded, face: string): string {
  return `<div class="conversation-event-line" data-mail-handed="${esc(draft.id)}">` +
    eventLineHTML(t("handoff.forwarded", { p0: NAME }), face, draft.holder_name) + `</div>`;
}

/** The cards a turn shows, below what it said (a draft handed on has none here: its line is outside the reply). */
export function mailCardsHTML(turn: AgentTurn, look: MailLook = {}): string {
  return (turn.drafts ?? []).map((draft: MailDraft) => (draft.mode === "card" ? mailCardHTML(draft, look) : "")).join("");
}

/** The centred lines of the drafts this turn handed on, between the question and the reply. */
export function mailHandedLinesHTML(turn: AgentTurn, look: MailLook = {}): string {
  return (turn.drafts ?? []).map((draft: MailDraft) =>
    draft.mode === "handed" ? mailHandedLineHTML(draft, look.memberFace?.(draft.holder_id, 18) ?? "") : "").join("");
}

/** What the card's fields hold now: the owner's words, saved or not, and the app they chose to send with. */
export function readMail(card: Element): { to: string; cc: string; subject: string; body: string; via: string } {
  const value = (field: MailField): string =>
    card.querySelector<HTMLInputElement | HTMLTextAreaElement>(`[data-mail-field="${field}"]`)?.value ?? "";
  const via = card.querySelector<HTMLElement>('[data-act="mail-from"]')?.dataset.via ?? (card as HTMLElement).dataset.from ?? "";
  return { to: value("to"), cc: value("cc"), subject: value("subject"), body: value("body"), via };
}

/** Whether something was typed or chosen in the card that is not in a saved version yet. */
export function mailDirty(card: Element): boolean {
  for (const field of card.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>("[data-mail-field]")) {
    if (field.value !== field.defaultValue) return true;
  }
  const from = card.querySelector<HTMLElement>('[data-act="mail-from"]');
  return from !== null && from.dataset.via !== (card as HTMLElement).dataset.from;
}

/**
 * A card drawn again keeps what the owner was typing: the words of each field that differ from the saved version go from
 * the field of `before` to the field of the same card in `after`, which takes the cursor too if the old field had it.
 * Call the result once `after` is in the page. The words pass from one field to the next and are kept nowhere else.
 */
export function carryMail(before: Element, after: Element): () => void {
  const focused: { id: string; field: string; start: number | null; end: number | null }[] = [];
  for (const old of before.querySelectorAll<HTMLElement>("[data-mail-card]")) {
    const id = old.dataset.mailCard ?? "";
    const fresh = [...after.querySelectorAll<HTMLElement>("[data-mail-card]")].find((card) => card.dataset.mailCard === id);
    if (!fresh || fresh.dataset.mailState === undefined || !fresh.querySelector("[data-mail-field]:not([disabled])")) continue;
    for (const field of old.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>("[data-mail-field]")) {
      const name = field.dataset.mailField ?? "";
      const next = fresh.querySelector<HTMLInputElement | HTMLTextAreaElement>(`[data-mail-field="${name}"]`);
      if (!next) continue;
      if (field.value !== field.defaultValue) next.value = field.value;
      if (field === document.activeElement) focused.push({ id, field: name, start: field.selectionStart, end: field.selectionEnd });
    }
  }
  return () => {
    for (const item of focused) {
      const card = [...after.querySelectorAll<HTMLElement>("[data-mail-card]")].find((node) => node.dataset.mailCard === item.id);
      const field = card?.querySelector<HTMLInputElement | HTMLTextAreaElement>(`[data-mail-field="${item.field}"]`);
      if (!field || field.disabled) continue;
      field.focus({ preventScroll: true });
      if (item.start !== null && item.end !== null) field.setSelectionRange(Math.min(item.start, field.value.length), Math.min(item.end, field.value.length));
    }
  };
}
