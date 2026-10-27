// What the owner does on an email card (mail-card.ts): they edit its fields, choose the app that sends it, press 傳送
// or 捨棄, or answer the choice card that offers it to a colleague. Every edit is saved as a new version on top of the
// version the card holds; 傳送 first saves what is in the fields, and sends the digest of the version that results
// only if nothing in the fields differs from it, so that what goes is what the owner sees. The saves of one draft
// run one after the other, each on the digest the one before came back with. The core says how every card stands
// (`agent.turn`); nothing here keeps the words of a mail beyond the call that carries them.

import * as api from "./api";
import { t } from "./i18n";
import { mailDirty, readMail } from "./mail-card";
import { type Choice, openMenu } from "./ui";

export interface MailActions {
  /** Save what card `id` holds if it differs from its saved version; true when the saved version is what the fields say. */
  save(id: string): Promise<boolean>;
  destroy(): void;
}

/** Attach the card's behaviour to the thread that holds the cards. `fail` shows an error to the owner. */
export function mountMailActions(thread: HTMLElement, fail: (error: unknown) => void, notice: (words: string) => void): MailActions {
  const queues = new Map<string, Promise<unknown>>();
  const latest = new Map<string, { digest: string; version: number }>();
  const cardOf = (id: string): HTMLElement | undefined =>
    [...thread.querySelectorAll<HTMLElement>("[data-mail-card]")].find((card) => card.dataset.mailCard === id);

  /** The digest of the newest version known: the card's own, or a newer one that a save came back with. */
  function digestOf(card: HTMLElement): string {
    const known = latest.get(card.dataset.mailCard ?? "");
    return known && known.version > Number(card.dataset.version ?? 0) ? known.digest : card.dataset.digest ?? "";
  }

  function inOrder<T>(id: string, job: () => Promise<T>): Promise<T> {
    const next = (queues.get(id) ?? Promise.resolve()).then(job, job);
    queues.set(id, next.catch(() => undefined));
    return next;
  }

  function save(id: string): Promise<boolean> {
    return inOrder(id, async () => {
      const card = cardOf(id);
      if (!card) return false;
      if (!mailDirty(card)) return true;
      const base = digestOf(card);
      try {
        const result = await api.mailSave(id, base, readMail(card));
        if (!result.draft) return false;
        latest.set(id, { digest: result.draft.digest, version: result.draft.version });
        // Saved, or already what the fields say. A draft that moved on under the card (a colleague revised it) is neither.
        return result.saved || result.draft.digest === base;
      } catch (error) {
        fail(error);
        return false;
      }
    });
  }

  const buttonsOf = (card: Element | null): HTMLButtonElement[] => [...(card?.querySelectorAll<HTMLButtonElement>("button") ?? [])];

  async function send(button: HTMLElement): Promise<void> {
    const id = button.closest<HTMLElement>("[data-mail-card]")?.dataset.mailCard ?? "";
    const card = cardOf(id);
    if (!card) return;
    const buttons = buttonsOf(card);
    for (const each of buttons) each.disabled = true;
    try {
      if (!(await save(id))) {
        notice(t("mail.updated"));
        return;
      }
      const current = cardOf(id);
      if (!current) return;
      await api.mailSend(id, digestOf(current));
    } catch (error) {
      fail(error);
    } finally {
      for (const each of buttons) if (each.isConnected) each.disabled = false;
    }
  }

  /** A call that needs only the draft's id: the card's (捨棄), or the choice card's (the hand-over). */
  async function callOn(button: HTMLElement, root: "card" | "offer", call: (id: string) => Promise<unknown>): Promise<void> {
    const node = button.closest<HTMLElement>(root === "card" ? "[data-mail-card]" : "[data-mail-offer]");
    const id = (root === "card" ? node?.dataset.mailCard : node?.dataset.mailOffer) ?? "";
    if (!node || !id) return;
    const buttons = buttonsOf(node);
    for (const each of buttons) each.disabled = true;
    try {
      await call(id);
    } catch (error) {
      fail(error);
      for (const each of buttons) if (each.isConnected) each.disabled = false;
    }
  }

  function chooseSender(button: HTMLElement): void {
    const card = button.closest<HTMLElement>("[data-mail-card]");
    if (!card) return;
    let senders: { key: string; name: string }[] = [];
    try {
      senders = JSON.parse(card.dataset.senders ?? "[]");
    } catch {
      return;
    }
    const choices: Choice[] = senders.map((sender) => ({ value: sender.key, label: sender.name }));
    openMenu(button, choices, button.dataset.via ?? "", (value) => {
      const picked = senders.find((sender) => sender.key === value);
      if (!picked || value === button.dataset.via) return;
      button.dataset.via = value;
      const shown = button.querySelector("span");
      if (shown) shown.textContent = picked.name;
      void save(card.dataset.mailCard ?? "");
    });
  }

  const onClick = (event: Event): void => {
    const act = (event.target as Element).closest<HTMLElement>("[data-act^='mail-']");
    if (!act || (act as HTMLButtonElement).disabled) return;
    switch (act.dataset.act) {
      case "mail-send": void send(act); break;
      case "mail-discard": void callOn(act, "card", api.mailDiscard); break;
      case "mail-from": chooseSender(act); break;
      case "mail-handover-yes": void callOn(act, "offer", (id) => api.mailHandover(id, "yes")); break;
      case "mail-handover-no": void callOn(act, "offer", (id) => api.mailHandover(id, "no")); break;
    }
  };
  const onChange = (event: Event): void => {
    const field = (event.target as Element).closest<HTMLElement>("[data-mail-field]");
    const id = field?.closest<HTMLElement>("[data-mail-card]")?.dataset.mailCard;
    if (field && id) void save(id);
  };
  thread.addEventListener("click", onClick);
  thread.addEventListener("change", onChange);
  return {
    save,
    destroy() {
      thread.removeEventListener("click", onClick);
      thread.removeEventListener("change", onChange);
    },
  };
}
