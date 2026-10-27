// 「選擇主要成員」: the window where the owner chooses their primary contact, the member that new work is
// brought to and that hands it to the colleague it belongs to. The members to choose from are real
// buttons (a radio group made of them), one chosen at a time; 「取消」 and the primary 「設為主要」, which
// waits for a choice, close it. The member whose menu opened it is chosen already. Raphael is one of the
// choices (he is the primary until another is chosen, and the owner can give it back), and the member
// that is the primary now is not offered. Resolves with the id chosen (0 is Raphael), or null when it is left.

import { t } from "./i18n";
import { openDialog } from "./member-dialogs";
import { faceHTML, memberRole, memberMatches } from "./members";
import type { Member } from "./types";
import { btn, esc, ico } from "./ui";

export interface PrimaryPickerOptions {
  /** Who can be chosen, in the order the column shows them: not the primary now, and nobody hidden. */
  members: readonly Member[];
  /** The member chosen when the window opens, if it is one of them. */
  chosen?: number;
}

export function openPrimaryPicker(o: PrimaryPickerOptions): Promise<number | null> {
  return new Promise((resolve) => {
    let chosen: number | null = o.members.some((member) => member.id === o.chosen) ? o.chosen! : null;
    let answer: number | null = null;
    const done = (dialog: { close(): void }): void => {
      if (chosen === null) return;
      answer = chosen;
      dialog.close();
    };
    const dialog = openDialog({
      title: t("members.primaryTitle"),
      cls: "mb-chat-picker mb-primary-picker",
      body:
        `<input class="mb-fin" type="search" data-primary-search placeholder="${esc(t("members.sidePickerHint"))}" aria-label="${esc(t("members.sidePickerHint"))}" autocomplete="off" spellcheck="false">` +
        `<div class="mb-chat-choices" role="radiogroup" aria-label="${esc(t("members.primaryTitle"))}"></div>`,
      actions: {
        secondary: btn(esc(t("common.cancel")), "", ' data-dlg="cancel"'),
        primary: { label: esc(t("members.sidePrimary")), attrs: ' data-dlg="set" disabled' },
      },
      onAct(act, target, d) {
        if (act === "pick") {
          chosen = Number(target.dataset.id);
          draw();
        } else if (act === "set") done(d);
        else d.close();
      },
      onEnter: done,
      onClose: () => resolve(answer),
    });
    const input = dialog.el.querySelector<HTMLInputElement>("[data-primary-search]")!;
    const choices = dialog.el.querySelector<HTMLElement>(".mb-chat-choices")!;
    const set = dialog.el.querySelector<HTMLButtonElement>('[data-dlg="set"]')!;
    function draw(): void {
      const focused = document.activeElement instanceof HTMLElement && choices.contains(document.activeElement) ? document.activeElement.dataset.id : undefined;
      const shown = o.members.filter((member) => memberMatches(member, input.value));
      // A choice the search has taken out of sight is not kept: what 設為主要 sets is always what is shown chosen.
      if (!shown.some((member) => member.id === chosen)) chosen = null;
      choices.innerHTML = shown.length
        ? shown
            .map((member) =>
              btn(
                `${faceHTML(member, 34)}<span class="mb-chat-text"><b>${esc(member.name)}</b>${member.blurb || memberRole(member) ? `<small>${esc(member.blurb || memberRole(member))}</small>` : ""}</span>${member.id === chosen ? ico("check") : ""}`,
                "mb-chat-choice",
                ` role="radio" aria-checked="${member.id === chosen}" data-dlg="pick" data-id="${member.id}"`,
              ),
            )
            .join("")
        : `<p class="mb-none">${esc(t("members.noMatch"))}</p>`;
      set.disabled = chosen === null;
      if (focused !== undefined) choices.querySelector<HTMLElement>(`[data-id="${focused}"]`)?.focus({ preventScroll: true });
    }
    input.addEventListener("input", draw);
    draw();
  });
}
