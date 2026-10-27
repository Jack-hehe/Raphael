// The member column keeps Raphael pinned first, followed by member groups and ungrouped members.
// Raphael keeps his original large emblem; the other rows retain their message previews.
//
// A right click on a row, or its borderless 「more」 that shows under the pointer, gives 「設為主要」 (the
// primary contact, marked by a star on its face), 「移到分組 ▸」 and 「刪除」; a group header's gives 「重新命名」, 「看的地方」 and 「刪除」. What they do is the host's
// (members-shell.ts): the column only draws and says what was asked.
//
// `columnHTML` draws the list from a snapshot (pure, so its rules can be tested without a browser);
// `createMemberColumn` keeps the search and the folded groups and hands the clicks over.

import { createRoomsSection, openNewGroupChat } from "./group-chats";
import { t } from "./i18n";
import { formatClock } from "./format";
import { createMessageSearch } from "./message-search";
import { type MemberSection, faceHTML, memberMatches, memberSections, previewOf, primaryIdOf, primaryStarHTML, raphaelOf } from "./members";
import type { Member, MembersList } from "./types";
import { closeMenu, esc, ico, iconButton, openPopover } from "./ui";

/** What the column is drawn from. */
export interface ColumnView {
  list: MembersList | null;
  /** The member whose thread is open. */
  current: number;
  /** The last message of each member's thread, as far as it is known. */
  previews: ReadonlyMap<number, string>;
  query: string;
  /** The groups folded away, by id. */
  folded: ReadonlySet<number>;
  /** The members that were not there at the last drawing: their rows fade in. */
  fresh: ReadonlySet<number>;
  collapsed?: boolean;
}

/** A member's row: its picture (with the star of the primary contact), name, role, the line under them and the dot; then its 「more」. */
export function memberRowHTML(member: Member, v: Pick<ColumnView, "current" | "previews" | "fresh"> & { list?: ColumnView["list"] }): string {
  const chief = member.id === 0;
  const lastAt = new Date(member.last_at ?? NaN);
  const preview = previewOf(member, v.previews.get(member.id));
  const dot = member.waiting
    ? `<span class="mb-dot is-wait" role="img" aria-label="${esc(t("members.waitingShort"))}"></span>`
    : member.unread
      ? `<span class="mb-dot is-unread" role="img" aria-label="${esc(t("members.unread"))}"></span>`
      : "";
  return (
    `<div class="mb-item${v.fresh.has(member.id) ? " is-new" : ""}" data-member-item="${member.id}">` +
    `<button type="button" class="nav mb-row${chief ? " mb-chief" : ""}" data-member="${member.id}" aria-current="${member.id === v.current}" data-k="member:${member.id}">` +
    faceHTML(member, 40) +
    (v.list && primaryIdOf(v.list) === member.id ? primaryStarHTML() : "") +
    `<span class="mb-row-main"><span class="mb-row-t"><b>${esc(member.name)}</b>${chief || member.pinned ? `<span class="mb-pin" aria-label="${esc(t("members.sidePinned"))}">${ico("pin")}</span>` : ""}${member.role ? `<i>${esc(member.role)}</i>` : ""}` +
    (Number.isFinite(lastAt.getTime()) ? `<time class="mb-row-time" datetime="${lastAt.toISOString()}">${formatClock(lastAt.getTime())}</time>` : "") + `</span>` +
    (preview.text ? `<span class="mb-row-p${preview.waiting ? " is-wait" : ""}">${esc(preview.text)}</span>` : "") +
    `</span>${dot}</button>` +
    (chief ? "" : iconButton("more", t("members.more", { p0: member.name }), ` data-member-more="${member.id}" aria-haspopup="menu" aria-expanded="false" data-k="member-more:${member.id}"`, "mb-more")) +
    `</div>`
  );
}

/** A group's header and its members; 未分組 has no menu. */
function sectionHTML(section: MemberSection, v: ColumnView): string {
  const group = section.group;
  const id = group ? String(group.id) : "none";
  const name = group ? group.name : t("members.ungrouped");
  const open = !(group && v.folded.has(group.id)) || v.query.trim() !== "";
  return (
    `<section class="mb-sec" data-group="${id}" data-open="${open}">` +
    `<div class="mb-ghead"><span class="mb-gname">${esc(name)}</span>` +
    (group
      ? iconButton("more", t("members.more", { p0: name }), ` data-group-more="${id}" aria-haspopup="menu" aria-expanded="false" data-k="group-more:${id}"`, "mb-more")
      : "") +
    iconButton("down", t("members.toggleGroup", { p0: name }), ` data-group-fold="${id}" aria-expanded="${open}" data-k="group-fold:${id}"`, "mb-fold") +
    `</div>` +
    `<div class="mb-gbody"${open ? "" : " inert"}><div class="mb-gclip">${section.members.map((member) => memberRowHTML(member, v)).join("")}</div></div>` +
    `</section>`
  );
}

/** The list under the search: Raphael, the groups, 未分組; or a line that nothing matches. */
export function columnHTML(v: ColumnView): string {
  const raphael = raphaelOf(v.list);
  const all = v.list ?? { members: [], groups: [] };
  const pinned = all.members.filter(m => m.id !== 0 && m.pinned && !m.hidden && memberMatches(m, v.query));
  const sections = memberSections({ ...all, members: all.members.filter(m => !m.pinned) }, v.query);
  const searching = v.query.trim() !== "";
  // The star marks the primary contact, Raphael until the owner chooses another; the strip and the column show it alike.
  const primary = v.list ? primaryIdOf(v.list) : -1;
  // Where a face stands alone (the strip) or a row is named by its label, the star is said in words too.
  const named = (name: string, marked: boolean): string => esc(marked ? `${name} · ${t("members.primary")}` : name);
  const chief = `<button type="button" class="nav mb-row mb-chief" data-member="0" aria-current="${v.current === 0}" data-k="member:0" aria-label="${named(raphael.name, primary === 0)}"${v.collapsed ? ` data-tooltip="${named(raphael.name, primary === 0)}"` : ""}>` +
    faceHTML(raphael, 64) + (primary === 0 ? primaryStarHTML() : "") + `<b>${esc(raphael.name)}</b>` +
    (raphael.waiting || raphael.unread ? `<span class="mb-dot ${raphael.waiting ? "is-wait" : "is-unread"}" role="img" aria-label="${esc(t(raphael.waiting ? "members.waitingShort" : "members.unread"))}"></span>` : "") + `</button>`;
  if (v.collapsed) {
    const rows = [...pinned, ...sections.flatMap(section => section.members)];
    return chief + rows.map(m => `<button type="button" class="nav mb-row mb-face" data-member="${m.id}" data-k="member:${m.id}" aria-current="${m.id === v.current}" aria-label="${named(m.name, m.id === primary)}" data-tooltip="${named(m.name, m.id === primary)}">` +
      faceHTML(m, 40) + (m.id === primary ? primaryStarHTML() : "") + (m.waiting || m.unread ? `<span class="mb-dot ${m.waiting ? "is-wait" : "is-unread"}" role="img" aria-label="${esc(t(m.waiting ? "members.waitingShort" : "members.unread"))}"></span>` : "") + `</button>`).join("");
  }
  const showChief = !searching || memberMatches(raphael, v.query);
  return (showChief ? chief : "") + (pinned.length ? `<div class="mb-pinned">${pinned.map(m => memberRowHTML(m, v)).join("")}</div>` : "") + sections.map((section) => sectionHTML(section, v)).join("") + (searching && !showChief && sections.length === 0 && pinned.length === 0 ? `<p class="mb-none">${esc(t("members.noMatch"))}</p>` : "");
}

export interface ColumnHost {
  openMessage?(member: number, conversation: number, turn: number): void;
  view(): Omit<ColumnView, "query" | "folded">;
  /** Opens the thread of member `id`. */
  select(id: number): void;
  newMember(groupId: number | null): void;
  newGroup(): void;
  newConversation?(): void;
  newGroupChat?(): void;
  memberAction?(id: number, action: string): void;
  /** 移到分組 chose a group for member `id` (null: 未分組). */
  moveMember(id: number, groupId: number | null, anchor: HTMLElement): void;
  deleteMember(id: number): void;
  renameGroup(id: number): void;
  editSources(id: number): void;
  deleteGroup(id: number): void;
}

export interface MemberColumn {
  readonly el: HTMLElement;
  draw(): void;
  /** The same cross-conversation search, available when the column is hidden on narrow windows. */
  openSearch(anchor: HTMLElement): void;
  /** The search's words now. */
  query(): string;
  menu(id: number, anchor: HTMLElement): void;
}

/** A menu of plain items under `anchor`; `onPick` gets the chosen item's `id`. */
export function openActions(anchor: HTMLElement, items: readonly { id: string; label: string; icon?: Parameters<typeof ico>[0]; sub?: boolean }[], onPick: (id: string) => void): void {
  const el = openPopover(
    anchor,
    items
      .map(
        (item) =>
          `<button type="button" role="menuitem"${item.icon === "trash" ? ' class="danger"' : ""} tabindex="-1" data-pick="${esc(item.id)}"${item.sub ? ' aria-haspopup="menu"' : ""}>` +
          `<span class="ck">${item.icon ? ico(item.icon) : ""}</span><span class="mt">${esc(item.label)}</span>${item.sub ? ico("right") : ""}</button>`,
      )
      .join(""),
    { className: "menu mb-menu", role: "menu", label: anchor.getAttribute("aria-label") ?? undefined },
  );
  el.addEventListener("click", (e) => {
    const pick = (e.target as Element).closest<HTMLElement>("[data-pick]");
    if (!pick) return;
    const id = pick.dataset.pick ?? "";
    // a submenu opens in the place of this one, from the same button
    if (!pick.hasAttribute("aria-haspopup")) {
      closeMenu();
      anchor.focus({ preventScroll: true });
    }
    onPick(id);
  });
  el.querySelector<HTMLElement>("button")?.focus();
}

export function createMemberColumn(host: ColumnHost): MemberColumn {
  const el = document.createElement("aside");
  el.className = "mb-col";
  let collapsed = window.matchMedia?.("(max-width: 859px)").matches ?? false;
  try { const saved = localStorage.getItem("raphael.members.collapsed"); if (saved !== null) collapsed = saved === "true"; } catch { /* Storage can be unavailable. */ }
  el.innerHTML =
    `<div class="mb-top">` +
    iconButton("left", t("members.collapseColumn"), ' data-col="collapse" aria-expanded="true"', "mb-tool mb-collapse") +
    `<label class="mb-find" hidden>${ico("search")}<input type="search" class="mb-find-input" autocomplete="off" spellcheck="false" maxlength="40"></label>` +
    `<span class="mb-top-fill"></span>` +
    iconButton("search", t("members.search"), ' data-col="search" aria-expanded="false"', "mb-tool") +
    iconButton("plus", t("members.add"), ' data-col="add" aria-haspopup="menu" aria-expanded="false"', "mb-tool") +
    `</div>` +
    `<nav class="mb-scroll"><div class="mb-list"></div></nav>`;
  el.querySelector(".mb-scroll")!.append(createRoomsSection());
  const collapseButton = el.querySelector<HTMLButtonElement>('[data-col="collapse"]')!;
  const find = el.querySelector<HTMLElement>(".mb-find")!;
  const findInput = el.querySelector<HTMLInputElement>(".mb-find-input")!;
  const searchButton = el.querySelector<HTMLElement>('[data-col="search"]')!;
  const addButton = el.querySelector<HTMLElement>('[data-col="add"]')!;
  const list = el.querySelector<HTMLElement>(".mb-list")!;
  const nav = el.querySelector<HTMLElement>(".mb-scroll")!;
  let query = "";
  const folded = new Set<number>();
  const search = createMessageSearch(list, {
    name: id => (id === 0 ? raphaelOf(host.view().list) : host.view().list?.members.find(member => member.id === id))?.name ?? "",
    face: id => {
      const members = host.view().list;
      const member = id === 0 ? raphaelOf(members) : members?.members.find(member => member.id === id);
      return member ? faceHTML(member, 34) : "";
    },
    open: (member, conversation, turn) => host.openMessage?.(member, conversation, turn),
  });

  function draw(): void {
    el.classList.toggle("is-collapsed", collapsed);
    const label = t(collapsed ? "members.expandColumn" : "members.collapseColumn");
    collapseButton.setAttribute("aria-label", label); collapseButton.title = label;
    collapseButton.setAttribute("aria-expanded", String(!collapsed));
    collapseButton.innerHTML = ico(collapsed ? "right" : "left");
    nav.setAttribute("aria-label", t("members.title"));
    for (const [button, label] of [
      [searchButton, find.hidden ? t("members.search") : t("members.searchClose")],
      [addButton, t("members.add")],
    ] as const) {
      button.setAttribute("aria-label", label);
      button.title = label;
    }
    findInput.placeholder = t("chat.searchAll");
    findInput.setAttribute("aria-label", t("chat.searchAll"));
    const focusKey = (document.activeElement as HTMLElement | null)?.closest<HTMLElement>("[data-k]");
    const key = focusKey && list.contains(focusKey) ? focusKey.dataset.k : undefined;
    search.draw(collapsed ? "" : query, columnHTML({ ...host.view(), query: collapsed ? "" : query, folded, collapsed }));
    if (key) list.querySelector<HTMLElement>(`[data-k="${CSS.escape(key)}"]`)?.focus({ preventScroll: true });
  }

  function toggleSearch(): void {
    find.hidden = !find.hidden;
    searchButton.setAttribute("aria-expanded", String(!find.hidden));
    if (find.hidden) {
      query = "";
      findInput.value = "";
      draw();
      searchButton.focus();
    } else {
      draw();
      findInput.focus();
    }
  }

  function memberMenu(id: number, anchor: HTMLElement): void {
    const member = host.view().list?.members.find(m => m.id === id);
    openActions(anchor, [
      { id: "pin", label: t(member?.pinned ? "members.sideUnpin" : "members.sidePin"), icon: "pin" },
      { id: "move", label: t("members.moveTo"), icon: "folder", sub: true },
      { id: "primary", label: t("members.sidePrimary"), icon: "star" },
      { id: "unread", label: t("members.sideUnread"), icon: "chat" },
      { id: "read-all", label: t("members.sideReadAll"), icon: "check" },
      { id: "edit", label: t("members.sideEdit"), icon: "pencil" },
      { id: "duplicate", label: t("members.sideDuplicate"), icon: "copy" },
      { id: "clear", label: t("members.sideClear"), icon: "trash" },
      { id: "hide", label: t("members.sideHide"), icon: "eyeOff" },
    ], pick => {
      if (pick === "move") host.moveMember(id, null, anchor);
      else host.memberAction?.(id, pick);
    });
  }

  function groupMenu(id: number, anchor: HTMLElement): void {
    openActions(
      anchor,
      [
        { id: "rename", label: t("members.rename"), icon: "pencil" },
        { id: "sources", label: t("members.sources"), icon: "link" },
        { id: "delete", label: t("members.delete"), icon: "trash" },
      ],
      (pick) => {
        if (pick === "rename") host.renameGroup(id);
        else if (pick === "sources") host.editSources(id);
        else host.deleteGroup(id);
      },
    );
  }

  el.addEventListener("click", (e) => {
    const target = e.target as Element;
    const col = target.closest<HTMLElement>("[data-col]")?.dataset.col;
    if (col === "collapse") {
      closeMenu();
      collapsed = !collapsed;
      try { localStorage.setItem("raphael.members.collapsed", String(collapsed)); } catch { /* The in-memory choice remains usable. */ }
      draw(); collapseButton.focus({ preventScroll: true });
      return;
    }
    if (col === "search") return toggleSearch();
    if (col === "add") {
      return openActions(
        addButton,
        [
          { id: "conversation", label: t("members.sideNewConversation"), icon: "chat" },
          { id: "member", label: t("members.newMember") + "  Ctrl+N", icon: "plus" },
          { id: "group", label: t("members.newGroup"), icon: "folder" },
          { id: "group-chat", label: t("members.sideNewGroupChat"), icon: "chat" },
        ],
        pick => {
          if (pick === "member") host.newMember(null);
          else if (pick === "conversation") host.newConversation?.();
          else if (pick === "group") host.newGroup();
          else if (host.newGroupChat) host.newGroupChat();
          else void openNewGroupChat();
        },
      );
    }
    const more = target.closest<HTMLElement>("[data-member-more]");
    if (more) return memberMenu(Number(more.dataset.memberMore), more);
    const groupMore = target.closest<HTMLElement>("[data-group-more]");
    if (groupMore) return groupMenu(Number(groupMore.dataset.groupMore), groupMore);
    const fold = target.closest<HTMLElement>("[data-group-fold]");
    if (fold) {
      const value = fold.dataset.groupFold!;
      if (value === "none" || query.trim() !== "") return;
      const id = Number(value);
      if (folded.has(id)) folded.delete(id);
      else folded.add(id);
      // the group folds where it is: its body slides, nothing else is drawn again
      const section = fold.closest<HTMLElement>(".mb-sec")!;
      const open = !folded.has(id);
      section.dataset.open = String(open);
      fold.setAttribute("aria-expanded", String(open));
      section.querySelector(".mb-gbody")?.toggleAttribute("inert", !open);
      return;
    }
    const row = target.closest<HTMLElement>("[data-member]");
    if (row) host.select(Number(row.dataset.member));
  });

  el.addEventListener("contextmenu", (e) => {
    const item = (e.target as Element).closest<HTMLElement>("[data-member-item]");
    const head = (e.target as Element).closest<HTMLElement>(".mb-ghead");
    const button = item?.querySelector<HTMLElement>("[data-member-more]") ?? head?.querySelector<HTMLElement>("[data-group-more]");
    if (!button) return;
    e.preventDefault();
    if (button.dataset.memberMore !== undefined) memberMenu(Number(button.dataset.memberMore), button);
    else groupMenu(Number(button.dataset.groupMore), button);
  });

  findInput.addEventListener("input", () => {
    query = findInput.value;
    draw();
  });
  findInput.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      toggleSearch();
    }
  });

  function openSearch(anchor: HTMLElement): void {
    const panel = openPopover(anchor, '<div class="conversation-tools"><input type="search"></div><div class="conversation-rows page-agent"></div>', { className: "menu conversation-search", role: "dialog", label: t("chat.searchAll") });
    const input = panel.querySelector<HTMLInputElement>("input")!;
    input.placeholder = t("chat.searchAll"); input.setAttribute("aria-label", t("chat.searchAll"));
    const results = panel.querySelector<HTMLElement>(".conversation-rows")!;
    const lookup = createMessageSearch(results, {
      name: id => (id === 0 ? raphaelOf(host.view().list) : host.view().list?.members.find(member => member.id === id))?.name ?? "",
      face: id => {
        const member = id === 0 ? raphaelOf(host.view().list) : host.view().list?.members.find(member => member.id === id);
        return member ? faceHTML(member, 34) : "";
      },
      open: (member, conversation, turn) => { closeMenu(); host.openMessage?.(member, conversation, turn); },
    });
    const redraw = (): void => lookup.draw(input.value, columnHTML({ ...host.view(), query: input.value, folded }));
    input.addEventListener("input", redraw);
    results.addEventListener("click", event => {
      const member = (event.target as Element).closest<HTMLElement>("[data-member]")?.dataset.member;
      if (member !== undefined) { closeMenu(); host.select(Number(member)); }
    });
    redraw(); input.focus();
  }
  return { el, draw, openSearch, query: () => query, menu: memberMenu };
}
