// A self-contained conversation list shared by the thread picker and the member details panel.
// The caller owns navigation; this component owns loading, searching and its CRUD dialogs.
import * as api from "./api";
import { onLocaleChange, t } from "./i18n";
import { describeError } from "./format";
import { openDialog, confirmDelete } from "./member-dialogs";
import { openActions } from "./member-column";
import { btn, closeMenu, esc, ico, iconButton, toaster } from "./ui";
import "./conversations.css";

export interface ConversationInfo {
  id: number;
  member_id: number;
  title: string;
  created_at_ms: number;
  updated_at_ms: number;
  is_default?: number;
}

export const conversationTitle = (conversation?: ConversationInfo): string => conversation?.title || t("conversations.untitled");
export const conversationChange = (memberId: number): void => {
  window.dispatchEvent(new CustomEvent("conversations-changed", { detail: memberId }));
};

export function relativeTime(ms: number, now = Date.now()): string {
  const minutes = Math.max(0, Math.floor((now - ms) / 60_000));
  if (minutes < 1) return t("conversations.now");
  if (minutes < 60) return t("conversations.minutes", { p0: minutes });
  if (minutes < 1440) return t("conversations.hours", { p0: Math.floor(minutes / 60) });
  return t("conversations.days", { p0: Math.floor(minutes / 1440) });
}

export function mountConversationList(host: HTMLElement, options: { memberId: number; onOpen(id: number): void; collapseWhenEmpty?: boolean; onVisibility?(visible: boolean): void }): { refresh(): Promise<void>; destroy(): void } {
  let conversations: ConversationInfo[] = [];
  let epoch = 0, destroyed = false;
  host.classList.add("conversation-list");
  host.innerHTML = btn(`${ico("plus")}<span>${esc(t("conversations.new"))}</span>`, "conversation-new", " data-conversation-new") +
    `<div class="conversation-tools"><input type="search" maxlength="200" autocomplete="off"></div><div class="conversation-rows" aria-live="polite"></div>`;
  const input = host.querySelector<HTMLInputElement>("input")!;
  const rows = host.querySelector<HTMLElement>(".conversation-rows")!;

  function draw(): void {
    host.hidden = !!options.collapseWhenEmpty && conversations.length === 0;
    options.onVisibility?.(!host.hidden);
    input.placeholder = t("conversations.search");
    input.setAttribute("aria-label", t("conversations.search"));
    const found = conversations.filter(c => conversationTitle(c).toLocaleLowerCase().includes(input.value.trim().toLocaleLowerCase()));
    rows.innerHTML = found.length ? found.map(c => `<div class="conversation-row">` +
      btn(`<span>${esc(conversationTitle(c))}</span><small>${esc(relativeTime(c.updated_at_ms))}</small>`, "conversation-open", ` data-conversation-open="${c.id}"`) +
      iconButton("more", t("chat.more"), ` data-conversation-more="${c.id}" aria-haspopup="menu"`, "conversation-more") + `</div>`).join("") : `<p class="conversation-empty">${esc(t("conversations.empty"))}</p>`;
  }

  async function refresh(): Promise<void> {
    const mine = ++epoch;
    try {
      const result = await api.core<{ conversations: ConversationInfo[] }>("conversations.list", { member_id: options.memberId });
      if (!destroyed && mine === epoch) { conversations = Array.isArray(result?.conversations) ? result.conversations : []; draw(); }
    } catch (error) {
      if (!destroyed && mine === epoch) rows.textContent = describeError(error);
    }
  }

  function rename(c: ConversationInfo): void {
    let busy = false;
    openDialog({ title: t("conversations.rename"),
      body: `<input class="conversation-name" maxlength="80" value="${esc(conversationTitle(c))}" aria-label="${esc(t("conversations.title"))}">`,
      canClose: () => !busy,
      actions: { secondary: btn(esc(t("common.cancel")), "", ' data-dlg="cancel"'), primary: { label: esc(t("members.save")), attrs: ' data-dlg="save"' } },
      onAct(act, _target, dialog) {
        if (act === "cancel") return dialog.close();
        const title = dialog.el.querySelector<HTMLInputElement>("input")!.value.trim();
        if (!title || busy) return;
        busy = true;
        void api.core("conversations.rename", { id: c.id, title }).then(() => {
          busy = false; dialog.close(); conversationChange(options.memberId);
        }).catch(error => { busy = false; toaster.info(describeError(error)); });
      },
      onEnter(dialog) { dialog.el.querySelector<HTMLButtonElement>('[data-dlg="save"]')!.click(); },
    });
  }

  const clicked = (event: MouseEvent): void => {
    const target = event.target as Element;
    const open = target.closest<HTMLElement>("[data-conversation-open]");
    if (open) { closeMenu(); options.onOpen(Number(open.dataset.conversationOpen)); return; }
    const more = target.closest<HTMLElement>("[data-conversation-more]");
    if (more) {
      const c = conversations.find(c => c.id === Number(more.dataset.conversationMore));
      if (!c) return;
      openActions(more, [{ id: "rename", label: t("conversations.rename"), icon: "pencil" }, { id: "delete", label: t("members.delete"), icon: "trash" }], act => {
        if (act === "rename") rename(c);
        else void confirmDelete(conversationTitle(c)).then(async yes => {
          if (!yes) return;
          await api.core("conversations.delete", { id: c.id }); conversationChange(options.memberId);
        }).catch(error => toaster.info(describeError(error)));
      });
      document.querySelector('[data-pick="delete"]')?.classList.add("danger");
    } else if (target.closest("[data-conversation-new]")) {
      void api.core<{ conversation: ConversationInfo }>("conversations.create", { member_id: options.memberId }).then(result => {
        conversationChange(options.memberId); closeMenu(); options.onOpen(result.conversation.id);
      }).catch(error => toaster.info(describeError(error)));
    }
  };
  host.addEventListener("click", clicked);
  input.addEventListener("input", draw);
  const changed = (event: Event): void => { if ((event as CustomEvent<number>).detail === options.memberId) void refresh(); };
  window.addEventListener("conversations-changed", changed);
  const stopLocale = onLocaleChange(draw);
  draw(); void refresh();
  return { refresh, destroy() { destroyed = true; epoch++; host.removeEventListener("click", clicked); input.removeEventListener("input", draw); stopLocale(); window.removeEventListener("conversations-changed", changed); host.replaceChildren(); } };
}
