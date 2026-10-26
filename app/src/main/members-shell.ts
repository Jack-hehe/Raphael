// The members of the 拉斐爾 page, put together: the list the core keeps (`members.list`), the column
// on the left (member-column.ts), the details panel on the right (member-details.ts), the dialogs
// (member-dialogs.ts) and the member identity used by the stacked conversation title.
// Below 860 px the column gives way to a dropdown at the left of that row.
//
// A new member of a group with places to look comes with the routine the core offers
// (「每天早上的進度」): the owner is asked, and only 「加入」 makes it.
//
// The page (pages/agent.ts) holds the conversation; this holds who it is with. When the owner picks
// another member, `onSelect` tells the page, which loads that member's thread. A thread the owner
// reads is told to the core (`members.read`), so that its row loses its dot.
//
// What the core cannot answer (a core from before the members, or one that is starting again)
// leaves Raphael alone in the column: the page works as it did before.

import * as api from "./api";
import { describeError } from "./format";
import { currentRoomId, openNewGroupChat, openRoomsMenu } from "./group-chats";
import { getLocale, onLocaleChange, t } from "./i18n";
import { createMemberColumn } from "./member-column";
import { createDetails } from "./member-details";
import {
  askGroupName,
  confirmDelete,
  editSources,
  groupChoices,
  groupFromValue,
  groupValue,
  offerRoutine,
  openNewMember,
  openEditMemberDialog,
  openDialog,
} from "./member-dialogs";
import { RAPHAEL_ID, faceHTML, memberOf, memberSections, raphaelOf } from "./members";
import type { AppContext, AvatarPreset, MainEvent, Member, MemberGroup, MemberPatch, MembersList, WireAttachment } from "./types";
import { type Choice, btn, closeMenu, esc, ico, leave, openMenu, toaster } from "./ui";

/** Below this window width the details panel lies over the chat instead of taking a column of its own. */
export const DETAILS_COLUMN_MIN_PX = 1120;
/** Events that come together (a task's step and its turn) read the list once. */
const REFRESH_DELAY_MS = 300;

export interface MembersHost {
  onOpenConversation?(member: number, conversation: number, turn?: number): void;
  app: AppContext;
  /** The owner chose another member's thread. */
  onSelect(id: number): void;
  /** The list of members changed (a name or picture was updated). */
  onUpdate?(): void;
  /** Whether the page is on screen now (a thread read only then). */
  visible(): boolean;
  focusHeader?(): void;
  onDetailsChange?(open: boolean): void;
}

export interface MembersShell {
  /** The member column. */
  readonly column: HTMLElement;
  /** The details panel. */
  readonly details: HTMLElement;
  /** The narrow windows' dropdown of members, for the left of the chat's top row. */
  readonly picker: HTMLElement;
  /** The pill with the open member, for the middle of the chat's top row. */
  readonly pill: HTMLElement;
  /** The id of the open member: 0 is Raphael. */
  current(): number;
  /** The name of member `id`, as the list has it (Raphael's for 0). */
  nameOf(id: number): string;
  /** The face of member `id` at `size` px (Raphael's bare emblem for 0); none for a member the list does not have. */
  faceOf(id: number, size: number): string;
  /** The open member, as the list has it. */
  member(): Member;
  /** Existing groups sorted by position. */
  groups(): readonly MemberGroup[];
  /** Loaded avatar presets. */
  presets(): readonly AvatarPreset[];
  /** Opens the thread of member `id`. */
  select(id: number): void;
  /** Reads the list (and what the column and the panel need with it) again. */
  load(): Promise<void>;
  /** A turn was written or a task moved: the list is read again a moment later. */
  changed(): void;
  follow(event: MainEvent): void;
  refreshRoutines(memberId: number): void;
  /** Whether the details panel is open. */
  detailsOpen(): boolean;
  setDetails(open: boolean): void;
  /** The page went off screen. */
  hide(): void;
}

export function createMembers(host: MembersHost): MembersShell {
  let list: MembersList | null = null;
  let current = RAPHAEL_ID;
  const previews = new Map<number, string>();
  let known: Set<number> | null = null;
  let fresh = new Set<number>();
  let presets: AvatarPreset[] = [];
  let presetsRead = false;
  let loads = 0;
  let refreshTimer: ReturnType<typeof setTimeout> | null = null;
  let open = typeof innerWidth === "number" ? innerWidth >= DETAILS_COLUMN_MIN_PX : true;
  let stopLeaving: (() => void) | null = null;
  let panelRead = false;
  let refreshOnShow = false;

  const member = (): Member => memberOf(list, current) ?? raphaelOf(list);

  // ---------- the parts ----------

  const column = createMemberColumn({
    openMessage: (member, conversation, turn) => host.onOpenConversation?.(member, conversation, turn),
    view: () => ({ list, current: currentRoomId() === null ? current : -1, previews, fresh }),
    select: (id) => select(id),
    newMember: (groupId) => newMember(groupId),
    newGroup: () => void newGroup(),
    newConversation: () => newConversation(),
    newGroupChat: () => void openNewGroupChat(),
    memberAction: (id, action) => void memberAction(id, action),
    moveMember: (id, _group, anchor) => moveMember(id, anchor),
    deleteMember: (id) => void deleteMember(id),
    renameGroup: (id) => void renameGroup(id),
    editSources: (id) => void sources(id),
    deleteGroup: (id) => void deleteGroup(id),
  });

  const details = createDetails({
    app: host.app,
    member,
    list: () => list,
    presets: () => presets,
    update,
    setPicture,
    close: () => setDetails(false),
    edit: m => editMember(m),
    hide: m => void memberAction(m.id, "hide"),
    openConversation: host.onOpenConversation ? (member, conversation) => host.onOpenConversation!(member, conversation) : undefined,
  });

  const pill = document.createElement("span");
  pill.className = "mb-pill";
  pill.dataset.shell = "pill";
  const picker = document.createElement("button");
  picker.type = "button";
  picker.className = "sel mb-pick";
  picker.setAttribute("aria-haspopup", "menu");
  picker.setAttribute("aria-expanded", "false");

  /** The pill and the narrow dropdown, for the open member. */
  function drawHead(): void {
    const m = member();
    pill.innerHTML = faceHTML(m, 22, { cls: "mb-pill-face" }) + `<span class="mb-pill-t">${esc(m.name)}</span>`;
    const waiting = list?.members.some((other) => other.id !== current && (other.waiting || other.unread)) ?? false;
    picker.innerHTML = `<span>${esc(m.name)}</span>${waiting ? `<i class="mb-dot is-wait" aria-hidden="true"></i>` : ""}${ico("down")}`;
    picker.setAttribute("aria-label", `${t("members.pick")}: ${m.name}`);
  }

  function drawAll(): void {
    column.draw();
    details.draw();
    drawHead();
  }

  // ---------- reading ----------

  async function load(): Promise<void> {
    const mine = ++loads;
    // the panel reads Raphael's routines the first time the page is on screen, not when the app starts
    if (!panelRead) {
      panelRead = true;
      details.showMember();
    } else if (refreshOnShow) {
      details.refreshRoutines();
      details.refreshMemory(current);
    }
    refreshOnShow = false;
    let read: MembersList;
    try {
      read = await Promise.resolve().then(() => api.membersList(getLocale()));
      if (!read || !Array.isArray(read.members) || !Array.isArray(read.groups)) throw new Error("no members");
    } catch {
      if (mine !== loads) return;
      // A core without the members: Raphael alone, as before them.
      if (list === null) drawAll();
      return;
    }
    if (mine !== loads) return;
    const ids = new Set(read.members.map((m) => m.id));
    fresh = known === null ? new Set() : new Set([...ids].filter((id) => !known!.has(id)));
    known = ids;
    list = read;
    details.refreshMemory(current);
    if (current !== RAPHAEL_ID && !ids.has(current)) {
      current = RAPHAEL_ID;
      host.onSelect(current);
      details.showMember();
    }
    drawAll();
    host.onUpdate?.();
    void readThread();
    if (host.visible()) tellViewing(current);
    previews.clear();
    for (const m of read.members) previews.set(m.id, m.preview ?? "");
    column.draw();
    if (!presetsRead) {
      void Promise.resolve()
        .then(() => api.membersPresets())
        .then(
          (answer) => {
            if (Array.isArray(answer?.presets)) {
              presets = answer.presets;
              presetsRead = true;
              host.onUpdate?.();
            }
          },
          () => {},
        );
    }
  }

  /** The open thread is read: the core forgets that it was unread. */
  async function readThread(): Promise<void> {
    const m = member();
    if (!host.visible() || !m.unread || m.id === RAPHAEL_ID) return;
    if (list) list = { ...list, members: list.members.map((other) => (other.id === m.id ? { ...other, unread: false } : other)) };
    column.draw();
    try {
      await api.membersRead(m.id);
    } catch {
      // it stays marked; the next list says so
    }
  }

  /** Tells the core which thread is on screen (null: none), so that what arrives there is no news. */
  function tellViewing(id: number | null): void {
    void Promise.resolve()
      .then(() => api.membersViewing(id))
      .catch(() => {});
  }

  function changed(): void {
    if (refreshTimer !== null) return;
    refreshTimer = setTimeout(() => {
      refreshTimer = null;
      void load();
    }, REFRESH_DELAY_MS);
  }

  // ---------- choosing ----------

  function select(id: number): void {
    if (id === current) {
      if (currentRoomId() !== null) { host.onSelect(id); drawAll(); }
      // Selecting the current member reopens its details without leaving the thread.
      setDetails(true);
      return;
    }
    current = id;
    fresh = new Set();
    host.onSelect(id);
    details.showMember();
    drawAll();
    if (host.visible()) tellViewing(id);
    void readThread();
  }

  function setDetails(next: boolean): void {
    if (next === open) return;
    open = next;
    host.onDetailsChange?.(open);
    const page = details.el.parentElement;
    stopLeaving?.();
    stopLeaving = null;
    if (next) {
      details.el.hidden = false;
      page?.setAttribute("data-det", "open");
      details.el.classList.remove("is-in");
      void details.el.offsetWidth;
      details.el.classList.add("is-in");
      details.draw();
      details.refreshRoutines();
      details.refreshMemory(current);
      details.el.querySelector<HTMLElement>('[data-det="close"]')?.focus({ preventScroll: true });
    } else {
      details.el.classList.remove("is-in");
      stopLeaving = leave(details.el, 160, () => {
        details.el.hidden = true;
        page?.removeAttribute("data-det");
        stopLeaving = null;
      });
      if (host.focusHeader) host.focusHeader();
      else picker.focus({ preventScroll: true });
    }
    drawHead();
  }

  picker.addEventListener("click", () => {
    const people: Choice[] = [raphaelOf(list), ...memberSections(list ?? { members: [], groups: [] }).flatMap((s) => s.members)].map((m) => ({
      value: String(m.id),
      label: m.name,
      ...(m.role ? { detail: m.role } : {}),
      ...(m.waiting ? { tag: t("members.waitingShort") } : m.unread ? { tag: t("members.unread") } : {}),
    }));
    openMenu(picker, people, String(current), (value) => select(Number(value)), undefined, [
      { label: t("chat.searchAll"), icon: "search", run: () => column.openSearch(picker) },
      { label: t("rooms.title"), icon: "chat", run: () => { closeMenu(); void openRoomsMenu(picker); } },
      { label: t("members.sideNewConversation"), icon: "chat", run: () => { closeMenu(); newConversation(); } },
      {
        label: t("members.newMember"),
        icon: "plus",
        run: () => {
          closeMenu();
          picker.focus({ preventScroll: true });
          newMember(null);
        },
      },
    ]);
  });

  // ---------- changing ----------

  async function update(id: number, patch: MemberPatch): Promise<boolean> {
    try {
      await api.membersUpdate(id, patch);
    } catch (err) {
      toaster.info(describeError(err));
      await load();
      return false;
    }
    await load();
    return true;
  }

  async function setPicture(id: number, file: WireAttachment): Promise<void> {
    try {
      await api.membersSetAvatar(id, file);
    } catch (err) {
      toaster.info(describeError(err));
    }
    await load();
  }

  function newMember(groupId: number | null): void {
    openNewMember({
      groups: [...(list?.groups ?? [])].sort((a, b) => a.position - b.position),
      groupId,
      presets,
      taken: (list?.members ?? []).map((m) => m.name),
      async create({ member: made, picture }) {
        let answer: Awaited<ReturnType<typeof api.membersCreate>>;
        try {
          answer = await api.membersCreate({ ...made, ui_language: getLocale() });
        } catch (err) {
          toaster.info(describeError(err));
          return false;
        }
        const id = answer.member.id;
        if (picture) {
          try {
            await api.membersSetAvatar(id, picture);
          } catch (err) {
            toaster.info(describeError(err));
          }
        }
        toaster.info(t("members.created", { p0: made.name }));
        await load();
        select(id);
        // the routine the core offers goes in only with the owner's 加入, once the dialog is gone
        const offer = answer.routine_offer;
        if (offer) {
          setTimeout(async () => {
            if (!(await offerRoutine(offer))) return;
            try {
              await api.routinesCreate({ member_id: id, ...offer });
            } catch (err) {
              toaster.info(describeError(err));
            }
            if (current === id) details.showMember();
          }, 0);
        }
        return answer.member;
      },
    });
  }

  function editMember(m: Member): void {
    openEditMemberDialog({groups: list?.groups ?? [], presets, taken: (list?.members ?? []).filter(other => other.id !== m.id).map(other => other.name), update, setPicture, saved: () => void load()}, m);
  }

  async function memberAction(id: number, action: string): Promise<void> {
    const m = memberOf(list, id);
    if (!m || id === RAPHAEL_ID) return;
    if (action === "edit") return editMember(m);
    try {
      if (action === "pin") await update(id, {pinned: !m.pinned});
      else if (action === "hide") {
        await api.membersUpdate(id, {hidden: true});
        if (current === id) select(RAPHAEL_ID);
        await load();
      } else if (action === "unread") {
        await api.core("members.unread", {id});
        // Marking the open conversation unread moves away so viewing does not immediately clear it.
        if (current === id) select(RAPHAEL_ID);
        await load();
      } else if (action === "read-all") {
        await api.core("members.mark_all_read", {}); await load();
      } else if (action === "duplicate") {
        const result = await api.core<{member: Member}>("members.duplicate", {id});
        await load(); select(result.member.id);
      } else if (action === "clear") {
        openDialog({title: t("members.sideClearConfirm", {p0: m.name}), body: `<p>${esc(t("members.sideClearHint"))}</p>`, cls: "mb-confirm",
          actions: {secondary: btn(esc(t("common.cancel")), "", ' data-dlg="cancel"'), destructive: {label: esc(t("members.sideClear")), attrs: ' data-dlg="clear"'}},
          onAct(pick, _button, dialog) {
            if (pick !== "clear") return dialog.close();
            void api.core("agent.history.clear", {member_id: id}).then(() => { dialog.close(); if (current === id) host.onSelect(id); void load(); }).catch(err => toaster.info(describeError(err)));
          },
        });
      }
    } catch (err) { toaster.info(describeError(err)); }
  }

  function newConversation(): void {
    const dialog = openDialog({title: t("members.sideNewConversation"), cls: "mb-chat-picker",
      body: `<input class="mb-fin" type="search" data-chat-search placeholder="${esc(t("members.sidePickerHint"))}" aria-label="${esc(t("members.sidePickerHint"))}"><div class="mb-chat-choices"></div>`,
      actions: {secondary: btn(esc(t("common.cancel")), "", ' data-dlg="cancel"')},
      onAct(action, target, d) {
        if (action === "describe") {
          const description = input.value.trim();
          if (!description) return;
          d.close();
          select(RAPHAEL_ID);
          setDetails(false);
          host.app.go("agent", `draft:${t("firstBot.newProposal", { description })}`);
        }
        else if (action === "member") { d.close(); newMember(null); }
        else if (action === "conversation") { d.close(); select(Number(target.dataset.id)); }
        else d.close();
      },
    });
    const input = dialog.el.querySelector<HTMLInputElement>("[data-chat-search]")!;
    const choices = dialog.el.querySelector<HTMLElement>(".mb-chat-choices")!;
    const draw = () => {
      const q = input.value.trim().toLocaleLowerCase();
      choices.innerHTML = (q ? btn(`${ico("plus")}<span>${esc(t("firstBot.describe"))}</span>`, "mb-chat-choice", ' data-dlg="describe"') : "") +
        btn(`${ico("plus")}<span>${esc(t("members.newMember"))}</span>`, "mb-chat-choice", ' data-dlg="member"') +
        [raphaelOf(list), ...memberSections(list ?? {members: [], groups: []}).flatMap(section => section.members)]
          .filter(m => `${m.name} ${m.role} ${m.blurb ?? ""}`.toLocaleLowerCase().includes(q))
          .map(m => btn(`${faceHTML(m, 34)}<span><b>${esc(m.name)}</b><small>${esc(m.blurb || m.role)}</small></span>`, "mb-chat-choice", ` data-dlg="conversation" data-id="${m.id}"`)).join("");
    };
    input.addEventListener("input", draw); draw();
  }
  window.addEventListener("keydown", event => {
    if (event.ctrlKey && !event.altKey && !event.metaKey && event.key.toLowerCase() === "n" && host.visible() && !document.querySelector(".mb-scrim")) {
      event.preventDefault(); newMember(null);
    }
  });

  function moveMember(id: number, anchor: HTMLElement): void {
    const m = list?.members.find((other) => other.id === id);
    if (!m || !list) return;
    openMenu(anchor, groupChoices([...list.groups].sort((a, b) => a.position - b.position)), groupValue(m.group_id), (value) => {
      const groupId = groupFromValue(value);
      if (groupId !== m.group_id) void update(id, { group_id: groupId });
    });
  }

  async function deleteMember(id: number): Promise<void> {
    const m = list?.members.find((other) => other.id === id);
    if (!m || !(await confirmDelete(m.name))) return;
    try {
      await api.membersDelete(id);
    } catch (err) {
      toaster.info(describeError(err));
      return;
    }
    previews.delete(id);
    if (current === id) select(RAPHAEL_ID);
    toaster.info(t("members.deleted", { p0: m.name }));
    await load();
  }

  /** Runs a change of the groups, says why it failed if it did, and reads the list again. */
  async function groupChange(work: () => Promise<unknown>): Promise<void> {
    try {
      await work();
    } catch (err) {
      toaster.info(describeError(err));
    }
    await load();
  }

  async function newGroup(): Promise<void> {
    const name = await askGroupName(t("members.newGroup"));
    if (name !== null) await groupChange(() => api.groupsCreate(name));
  }

  async function renameGroup(id: number): Promise<void> {
    const group = list?.groups.find((g) => g.id === id);
    if (!group) return;
    const name = await askGroupName(t("members.rename"), group.name);
    if (name !== null && name !== group.name) await groupChange(() => api.groupsUpdate(id, { name }));
  }

  async function sources(id: number): Promise<void> {
    const group = list?.groups.find((g) => g.id === id);
    if (!group) return;
    const next = await editSources(group);
    if (next !== null) await groupChange(() => api.groupsUpdate(id, { sources: next }));
  }

  async function deleteGroup(id: number): Promise<void> {
    const group = list?.groups.find((g) => g.id === id);
    if (!group || !(await confirmDelete(group.name))) return;
    await groupChange(() => api.groupsDelete(id));
  }

  onLocaleChange(() => { drawAll(); details.refreshMemory(current); });
  details.el.hidden = !open;
  drawAll();

  return {
    column: column.el,
    details: details.el,
    picker,
    pill,
    current: () => current,
    nameOf: (id) => memberOf(list, id)?.name ?? "",
    groups: () => [...(list?.groups ?? [])].sort((a, b) => a.position - b.position),
    presets: () => presets,
    faceOf(id, size) {
      const found = memberOf(list, id);
      return found ? faceHTML(found, size, { cls: "sys-face" }) : "";
    },
    member,
    select,
    load,
    changed,
    detailsOpen: () => open,
    setDetails,
    refreshRoutines(memberId) {
      if (host.visible() && memberId === current) details.refreshRoutines();
    },
    follow(event) {
      if (!host.visible()) return;
      if (event.type === "MembersChanged") {
        if (refreshTimer !== null) clearTimeout(refreshTimer);
        refreshTimer = null;
        void load();
      } else if (event.type === "RoutinesChanged" && event.member_id === current) {
        details.refreshRoutines();
      } else if (event.type === "MemoryChanged") {
        details.refreshMemory(event.member_id, event.space);
      }
    },
    hide() {
      refreshOnShow = true;
      if (refreshTimer !== null) clearTimeout(refreshTimer);
      refreshTimer = null;
      loads++;
      tellViewing(null);
    },
  };
}
