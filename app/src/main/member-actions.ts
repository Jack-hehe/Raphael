// Shared member actions for the column and the team board.
import * as api from "./api";
import { describeError } from "./format";
import { t } from "./i18n";
import { confirmDelete, groupChoices, groupFromValue, groupValue, openEditMemberDialog, openDialog } from "./member-dialogs";
import { RAPHAEL_ID, memberOf, orderedMembers, primaryIdOf } from "./members";
import { openPrimaryPicker } from "./primary-picker";
import type { AvatarPreset, Member, MemberPatch, MembersList, WireAttachment } from "./types";
import { btn, esc, openMenu, toaster } from "./ui";

interface MemberActionHost {
  list(): MembersList | null;
  presets(): AvatarPreset[];
  current(): number;
  select(id: number): void;
  load(): Promise<void>;
  update(id: number, patch: MemberPatch): Promise<boolean>;
  setPicture(id: number, file: WireAttachment): Promise<void>;
  cleared(id: number): void;
  deleted(id: number): void;
}

export function createMemberActions(host: MemberActionHost) {
  function editMember(m: Member): void {
    openEditMemberDialog({groups: host.list()?.groups ?? [], presets: host.presets(), taken: (host.list()?.members ?? []).filter(other => other.id !== m.id).map(other => other.name), update: host.update, setPicture: host.setPicture, saved: () => void host.load()}, m);
  }

  async function memberAction(id: number, action: string): Promise<void> {
    const m = memberOf(host.list(), id);
    if (!m || id === RAPHAEL_ID) return;
    if (action === "edit") return editMember(m);
    if (action === "primary") return choosePrimary(id);
    try {
      if (action === "pin") await host.update(id, {pinned: !m.pinned});
      else if (action === "hide") {
        await api.membersUpdate(id, {hidden: true});
        if (host.current() === id) host.select(RAPHAEL_ID);
        await host.load();
      } else if (action === "unread") {
        await api.core("members.unread", {id});
        // Marking the open conversation unread moves away so viewing does not immediately clear it.
        if (host.current() === id) host.select(RAPHAEL_ID);
        await host.load();
      } else if (action === "read-all") {
        await api.core("members.mark_all_read", {}); await host.load();
      } else if (action === "duplicate") {
        const result = await api.core<{member: Member}>("members.duplicate", {id});
        await host.load(); host.select(result.member.id);
      } else if (action === "clear") {
        openDialog({title: t("members.sideClearConfirm", {p0: m.name}), body: `<p>${esc(t("members.sideClearHint"))}</p>`, cls: "mb-confirm",
          actions: {secondary: btn(esc(t("common.cancel")), "", ' data-dlg="cancel"'), destructive: {label: esc(t("members.sideClear")), attrs: ' data-dlg="clear"'}},
          onAct(pick, _button, dialog) {
            if (pick !== "clear") return dialog.close();
            void api.core("agent.history.clear", {member_id: id}).then(() => { dialog.close(); if (host.current() === id) host.cleared(id); void host.load(); }).catch(err => toaster.info(describeError(err)));
          },
        });
      }
    } catch (err) { toaster.info(describeError(err)); }
  }

  /** Choose the primary contact without changing the chief or existing conversations. */
  async function choosePrimary(from: number): Promise<void> {
    const list = host.list();
    if (!list) return;
    const now = primaryIdOf(list);
    const candidates = orderedMembers(list).filter((m) => m.id !== now);
    const chosen = await openPrimaryPicker({ members: candidates, chosen: from });
    if (chosen === null || chosen === now) return;
    try {
      await api.membersSetPrimary(chosen);
    } catch (err) {
      toaster.info(describeError(err));
    }
    await host.load();
  }

  function moveMember(id: number, anchor: HTMLElement): void {
    const list = host.list();
    const m = list?.members.find((other) => other.id === id);
    if (!m || !list) return;
    openMenu(anchor, groupChoices([...list.groups].sort((a, b) => a.position - b.position)), groupValue(m.group_id), (value) => {
      const groupId = groupFromValue(value);
      if (groupId !== m.group_id) void host.update(id, { group_id: groupId });
    });
  }

  async function deleteMember(id: number): Promise<void> {
    const list = host.list();
    const m = list?.members.find((other) => other.id === id);
    if (!m || !(await confirmDelete(m.name))) return;
    try {
      await api.membersDelete(id);
    } catch (err) {
      toaster.info(describeError(err));
      return;
    }
    host.deleted(id);
    if (host.current() === id) host.select(RAPHAEL_ID);
    toaster.info(t("members.deleted", { p0: m.name }));
    await host.load();
  }

  return { editMember, memberAction, moveMember, deleteMember, update: host.update };
}
