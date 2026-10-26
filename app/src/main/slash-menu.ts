// Only command discovery belongs in the composer. The core expands a command when sending,
// preserving the owner's original /name text in the conversation.
import { membersCommandsList } from "./api";
import { t } from "./i18n";
import type { MemberCommand } from "./types";
import { textPositionRect } from "./text-position";
import { closeMenu, esc, menuIsOpenFor, openPopover, placeMenu } from "./ui";

export interface SlashMenuOptions { memberId(): number }
export interface SlashMenu { refresh(): Promise<void>; destroy(): void }
export const matchingCommands = (commands: readonly MemberCommand[], text: string): MemberCommand[] => {
  const query = /^\/([\w-]*)$/.exec(text)?.[1];
  return query === undefined ? [] : commands.filter(command => command.name.toLowerCase().startsWith(query.toLowerCase()));
};

export function mountSlashMenu(input: HTMLTextAreaElement | HTMLInputElement, options: SlashMenuOptions): SlashMenu {
  let alive = true;
  let generation = 0;
  let loadedFor: number | null = null;
  let commands: MemberCommand[] = [];
  let shown: MemberCommand[] = [];
  let selected = 0;
  let panel: HTMLElement | null = null;
  function close(): void {
    if (panel?.isConnected && menuIsOpenFor(input)) closeMenu();
    panel = null;
    input.removeAttribute("aria-activedescendant");
  }
  function choose(index: number): void {
    const command = shown[index];
    if (!command) return;
    input.value = `/${command.name} `;
    input.setSelectionRange(input.value.length, input.value.length);
    close();
    input.focus();
    input.dispatchEvent(new Event("input", { bubbles: true }));
  }
  function highlight(): void {
    panel?.querySelectorAll<HTMLElement>("[data-slash]").forEach((button, index) => button.setAttribute("aria-selected", String(index === selected)));
    const active = panel?.querySelector<HTMLElement>(`[data-slash="${selected}"]`);
    if (active) { input.setAttribute("aria-activedescendant", active.id); active.scrollIntoView?.({ block: "nearest" }); }
  }
  function draw(): void {
    if (!alive || document.activeElement !== input || loadedFor !== options.memberId()) { close(); return; }
    shown = matchingCommands(commands, input.value);
    if (!shown.length) { close(); return; }
    selected = Math.min(selected, shown.length - 1);
    panel = openPopover(input, shown.map((command, index) => `<button type="button" role="option" tabindex="-1" id="slash-command-${command.id}" data-slash="${index}" aria-selected="${selected === index}"><span class="mtd"><span class="mt">/${esc(command.name)}</span><span class="mdesc">${esc(command.description)}</span></span></button>`).join(""), { className: "menu slash-menu", role: "listbox", label: t("members.profile.slashCommands"), placement: "start", anchorRect: () => textPositionRect(input, 0) });
    panel.style.minWidth = `${Math.min(360, input.getBoundingClientRect().width, innerWidth - 16)}px`;
    placeMenu();
    panel.addEventListener("pointerdown", event => event.preventDefault());
    panel.addEventListener("click", event => {
      const button = (event.target as Element).closest<HTMLElement>("[data-slash]");
      if (button) choose(Number(button.dataset.slash));
    });
    highlight();
  }
  async function refresh(): Promise<void> {
    const mine = ++generation;
    const memberId = options.memberId();
    close();
    try {
      const answer = await membersCommandsList(memberId);
      if (!alive || mine !== generation || memberId !== options.memberId()) return;
      commands = answer.commands;
      loadedFor = memberId;
      draw();
    } catch { if (mine === generation) { loadedFor = null; close(); } }
  }
  const onInput = (): void => {
    selected = 0;
    if (!/^\/[\w-]*$/.test(input.value)) { close(); return; }
    if (loadedFor === options.memberId()) draw(); else void refresh();
  };
  const onBlur = (): void => { generation++; close(); };
  const key = (raw: Event): void => {
    const event = raw as KeyboardEvent;
    if (event.isComposing || event.keyCode === 229 || !panel?.isConnected || !menuIsOpenFor(input)) return;
    if (!["ArrowUp", "ArrowDown", "Enter", "Tab", "Escape"].includes(event.key)) return;
    if (event.key === "Tab" || event.key === "Escape") close();
    else if (event.key === "Enter") choose(selected);
    else { selected = (selected + (event.key === "ArrowDown" ? 1 : -1) + shown.length) % shown.length; highlight(); }
    if (event.key !== "Tab") event.preventDefault();
    event.stopImmediatePropagation();
  };
  input.addEventListener("input", onInput);
  input.addEventListener("focus", onInput);
  input.addEventListener("blur", onBlur);
  input.addEventListener("keydown", key, true);
  return { refresh, destroy() { alive = false; generation++; close(); input.removeEventListener("input", onInput); input.removeEventListener("focus", onInput); input.removeEventListener("blur", onBlur); input.removeEventListener("keydown", key, true); } };
}
