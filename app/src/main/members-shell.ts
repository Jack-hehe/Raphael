// The members of the 拉斐爾 page, put together: the list the core keeps (`members.list`), the column
// on the left (member-column.ts), the details panel on the right (member-details.ts), the dialogs
// (member-dialogs.ts) and, over the chat, the centred pill with the open member's picture and name,
// which opens the details. Below 860 px the column gives way to a dropdown at the left of that row.
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
} from "./member-dialogs";
import { RAPHAEL_ID, type ToolChoice, faceHTML, memberOf, memberSections, raphaelOf, toolChoices } from "./members";
import type { AppContext, AvatarPreset, Member, MemberPatch, MembersList, ModelCatalog, WireAttachment } from "./types";
import { type Choice, closeMenu, esc, ico, leave, openMenu, toaster } from "./ui";

/** Below this window width the details panel lies over the chat instead of taking a column of its own. */
export const DETAILS_COLUMN_MIN_PX = 1120;
/** Events that come together (a task's step and its turn) read the list once. */
const REFRESH_DELAY_MS = 300;

export interface MembersHost {
  app: AppContext;
  /** The owner chose another member's thread. */
  onSelect(id: number): void;
  /** Whether the page is on screen now (a thread read only then). */
  visible(): boolean;
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
  /** Opens the thread of member `id`. */
  select(id: number): void;
  /** Reads the list (and what the column and the panel need with it) again. */
  load(): Promise<void>;
  /** A turn was written or a task moved: the list is read again a moment later. */
  changed(): void;
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
  let connections: ToolChoice[] = toolChoices([]);
  let toolsRead = false;
  let catalog: Promise<ModelCatalog | null> | null = null;
  let loads = 0;
  let refreshTimer: ReturnType<typeof setTimeout> | null = null;
  let open = typeof innerWidth === "number" ? innerWidth >= DETAILS_COLUMN_MIN_PX : true;
  let stopLeaving: (() => void) | null = null;
  let panelRead = false;

  const member = (): Member => memberOf(list, current) ?? raphaelOf(list);

  // ---------- the parts ----------

  const column = createMemberColumn({
    view: () => ({ list, current, previews, fresh }),
    select: (id) => select(id),
    newMember: (groupId) => newMember(groupId),
    newGroup: () => void newGroup(),
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
    tools: () => connections,
    catalog: () => (catalog ??= api.modelsCatalog().then((read) => read, () => null)),
    update,
    setPicture,
    close: () => setDetails(false),
  });

  const pill = document.createElement("button");
  pill.type = "button";
  pill.className = "btn mb-pill";
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
    pill.setAttribute("aria-label", t("members.open", { p0: m.name }));
    pill.setAttribute("aria-expanded", String(open));
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
    }
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
    if (current !== RAPHAEL_ID && !ids.has(current)) {
      current = RAPHAEL_ID;
      host.onSelect(current);
      details.showMember();
    }
    drawAll();
    void readThread();
    if (host.visible()) tellViewing(current);
    previews.clear();
    for (const m of read.members) previews.set(m.id, m.preview ?? "");
    column.draw();
    if (!presetsRead) {
      presetsRead = true;
      void Promise.resolve()
        .then(() => api.membersPresets())
        .then((answer) => {
          presets = Array.isArray(answer?.presets) ? answer.presets : [];
        }, () => {});
    }
    if (!toolsRead) {
      toolsRead = true;
      // the core's own list of what a member may be given, or, from a core without it, the connections
      void Promise.resolve()
        .then(() => api.membersToolChoices())
        .then(
          (answer) => toolChoices(answer.connections ?? [], answer.tools),
          () => Promise.resolve().then(() => api.toolsetsList()).then((answer) => toolChoices(answer?.groups ?? [])),
        )
        .then((choices) => {
          connections = choices;
          details.draw();
        }, () => {});
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
      // the member already open: on a narrow window its panel comes back
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
      details.el.querySelector<HTMLElement>('[data-det="close"]')?.focus({ preventScroll: true });
    } else {
      details.el.classList.remove("is-in");
      stopLeaving = leave(details.el, 160, () => {
        details.el.hidden = true;
        page?.removeAttribute("data-det");
        stopLeaving = null;
      });
      pill.focus({ preventScroll: true });
    }
    drawHead();
  }

  pill.addEventListener("click", () => setDetails(!open));
  picker.addEventListener("click", () => {
    const people: Choice[] = [raphaelOf(list), ...memberSections(list ?? { members: [], groups: [] }).flatMap((s) => s.members)].map((m) => ({
      value: String(m.id),
      label: m.name,
      ...(m.role ? { detail: m.role } : {}),
      ...(m.waiting ? { tag: t("members.waitingShort") } : m.unread ? { tag: t("members.unread") } : {}),
    }));
    openMenu(picker, people, String(current), (value) => select(Number(value)), undefined, [
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
      tools: connections,
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
        if (picture) await api.membersSetAvatar(id, picture).catch((err: unknown) => toaster.info(describeError(err)));
        toaster.info(t("members.created", { p0: made.name }));
        await load();
        select(id);
        // the routine the core offers goes in only with the owner's 加入, once the dialog is gone
        const offer = answer.routine_offer;
        if (offer) {
          setTimeout(async () => {
            if (!(await offerRoutine(offer))) return;
            await api.routinesCreate({ member_id: id, ...offer }).catch((err: unknown) => toaster.info(describeError(err)));
            if (current === id) details.showMember();
          }, 0);
        }
        return true;
      },
    });
  }

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

  onLocaleChange(drawAll);
  details.el.hidden = !open;
  drawAll();

  return {
    column: column.el,
    details: details.el,
    picker,
    pill,
    current: () => current,
    nameOf: (id) => memberOf(list, id)?.name ?? "",
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
    hide() {
      if (refreshTimer !== null) clearTimeout(refreshTimer);
      refreshTimer = null;
      loads++;
      tellViewing(null);
    },
  };
}
