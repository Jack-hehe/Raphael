import * as api from "./api";
import { describeError } from "./format";
import { getLocale, t } from "./i18n";
import type { Member } from "./types";
import { btn, esc, toaster } from "./ui";

export function createHiddenMembers(): { load(): Promise<void>; mount(root: HTMLElement): void } {
  let members: Member[] = [];
  let root: HTMLElement | null = null;
  let epoch = 0;
  function draw(): void {
    const slot = root?.querySelector<HTMLElement>("[data-hidden-members]");
    if (!slot) return;
    slot.innerHTML = `<h3>${esc(t("members.sideHidden"))}</h3>` + (members.length ? members.map(member =>
      `<div class="mb-hidden-row"><span>${esc(member.name)}</span>${btn(esc(t("members.sideRestore")), "sm", ` data-restore-member="${member.id}"`)}</div>`).join("") : `<p class="muted">${esc(t("members.sideNoHidden"))}</p>`);
    slot.onclick = event => {
      const button = (event.target as Element).closest<HTMLButtonElement>("[data-restore-member]");
      if (!button) return;
      button.disabled = true;
      void api.membersUpdate(Number(button.dataset.restoreMember), { hidden: false }).then(load)
        .catch(error => { button.disabled = false; toaster.info(describeError(error)); });
    };
  }
  async function load(): Promise<void> {
    const mine = ++epoch;
    try {
      const list = await api.membersList(getLocale());
      if (mine !== epoch) return;
      members = list?.members?.filter(member => member.id !== 0 && member.hidden) ?? [];
      draw();
    } catch { /* General settings remain available while the core starts. */ }
  }
  return { load, mount(next) { root = next; draw(); } };
}
