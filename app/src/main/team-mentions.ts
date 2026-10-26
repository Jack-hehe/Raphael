import { iconHTML } from "./connections";
import { t } from "./i18n";
import { faceHTML } from "./members";
import { serviceIconOf } from "./service-icons";
import type { MembersList } from "./types";
import { closeMenu, ico, menuIsOpenFor, openMenu } from "./ui";
import { textPositionRect } from "./text-position";

const APP_STATES = { ready: "rooms.appConnected", needs_sign_in: "rooms.appSignIn" } as const;

/**
 * Prompts and menus consume the same public inventory; apps never become bot recipients. Each row leads
 * with who or what it names: a member's face, the app's connection mark, the team icon for everyone and
 * groups.
 */
export function teamMentions(input: HTMLTextAreaElement, list: MembersList, allowed?: number[]): void {
  const before = input.value.slice(0, input.selectionStart), match = /@([^@\n]*)$/.exec(before);
  if (!match || match[1].length > 40) {
    if (menuIsOpenFor(input) && document.querySelector("#app-popover[data-team-mentions]")) closeMenu();
    return;
  }
  const people = list.team_context?.members ?? list.members;
  const face = (id: number, name: string): string => faceHTML(list.members.find(m => m.id === id) ?? { id, name }, 20);
  const choices = [
    { value: t("team.everyone"), label: "@" + t("team.everyone"), lead: ico("grid") },
    ...people.filter(m => !allowed || allowed.includes(m.id)).map(m => ({ value: m.name, label: "@" + m.name, lead: face(m.id, m.name) })),
    ...(!allowed ? list.groups.map(g => ({ value: g.name, label: "@" + g.name, lead: ico("grid") })) : []),
    ...(list.team_context?.apps ?? []).map(app => ({ value: app.name, label: "@" + app.name, lead: iconHTML(app.name, serviceIconOf({ name: app.name }), "md"),
      tag: t(APP_STATES[app.state as keyof typeof APP_STATES] ?? "rooms.appUnknown") })),
  ].filter(c => c.value.toLocaleLowerCase().includes(match[1].toLocaleLowerCase()));
  if (!choices.length) { closeMenu(); return; }
  const caret = input.selectionStart;
  openMenu(input, choices, "", value => {
    const prefix = before.slice(0, match.index);
    input.value = prefix + "@" + value + " " + input.value.slice(caret);
    input.focus(); input.setSelectionRange(prefix.length + value.length + 2, prefix.length + value.length + 2);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  }, undefined, [], { placement: "start", anchorRect: () => textPositionRect(input, match.index), selectionIndicator: false });
  document.querySelector<HTMLElement>("#app-popover")!.dataset.teamMentions = "true";
  input.focus();
}
