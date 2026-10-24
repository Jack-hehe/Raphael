// A team board that shares the member store, group dialogs and group-thread dispatcher.
import { createChatComposer } from "../chat-composer";
import * as api from "../api";
import { describeError } from "../format";
import { getLocale, onLocaleChange, t } from "../i18n";
import { askGroupName, confirmDelete } from "../member-dialogs";
import { openActions } from "../member-column";
import { openGroupChat } from "../group-chats";
import { faceHTML } from "../members";
import { modelMark } from "../provider-marks";
import { openMemberThread } from "../team-navigation";
import type { AppContext, Member, MemberGroup, MembersList, Page, WireAttachment } from "../types";
import { btn, closeMenu, esc, ico, iconButton, openMenu, toaster } from "../ui";
import "../members.css";
import "../automations.css";

export function createTeamPage(app: AppContext): Page {
  const el = document.createElement("section"); el.className = "au-page team-page page-agent";
  el.innerHTML = `<header class="au-header"></header><div class="team-board-scroll"><div class="team-board"></div></div><div class="team-bottom"><div class="team-composer"></div><div class="team-zoom"></div></div>`;
  const composer = createChatComposer({ placeholder: () => t("team.composer"), send });
  el.querySelector(".team-composer")!.append(composer.el);
  const board = el.querySelector<HTMLElement>(".team-board")!, input = composer.input;
  let list: MembersList = { members: [], groups: [] }, model = "", zoom = 1, visible = false, loads = 0, sending = false;
  let requestId: string | undefined, retryText = "";
  function card(m: Member): string {
    return `<article class="team-member"${m.id ? ` draggable="true" data-team-member="${m.id}"` : ""}><div class="team-member-head">${faceHTML(m, 40)}<div><b>${esc(m.name)}</b><span>${esc(m.id === 0 ? t("team.chief") : m.role)}</span></div></div><footer>${btn(ico("chat") + esc(t("team.chat")), "sm", ` data-team="chat" data-member="${m.id}"`)}<span class="team-model" title="${esc(model)}">${modelMark({ id: model })}<span>${esc(model.split("/").pop() ?? model)}</span></span></footer></article>`;
  }
  function group(group: MemberGroup | null): string {
    const people = list.members.filter(m => m.id !== 0 && m.group_id === (group?.id ?? null));
    if (!group && !people.length) return `<section class="team-group team-drop-empty" data-team-group="none" hidden><h2>${esc(t("members.ungrouped"))}</h2></section>`;
    return `<section class="team-group" data-team-group="${group?.id ?? "none"}"><header><h2>${esc(group?.name ?? t("members.ungrouped"))}</h2><span>${people.length}</span>${group ? iconButton("more", t("members.more", { p0: group.name }), ` data-team="group-menu" data-group="${group.id}"`) : ""}</header><div class="team-group-members">${people.map(card).join("")}</div></section>`;
  }
  function draw(): void {
    el.querySelector(".au-header")!.innerHTML = `<h1>${esc(t("team.title"))}<small>${list.members.length}</small></h1>${btn(ico("plus") + esc(t("auto.new")), "pri", ' data-team="new"')}`;
    board.innerHTML = `<div class="team-chief">${list.members[0] ? card(list.members[0]) : ""}</div><div class="team-groups">${list.groups.map(group).join("")}${group(null)}</div>`;
    board.style.zoom = String(zoom);
    input.placeholder = t("team.composer"); input.setAttribute("aria-label", t("team.composer"));
    composer.draw();
    el.querySelector(".team-zoom")!.innerHTML = iconButton("minus", t("team.zoomOut"), ` data-team="zoom-out"${zoom <= 0.5 ? " disabled" : ""}`) + `<span>${Math.round(zoom * 100)}%</span>` + iconButton("plus", t("team.zoomIn"), ` data-team="zoom-in"${zoom >= 1.5 ? " disabled" : ""}`);
  }
  async function load(): Promise<void> {
    const mine = ++loads;
    try { const [people, settings] = await Promise.all([api.membersList(getLocale()), api.settingsGet()]); if (!visible || mine !== loads) return; list = people; model = settings.settings.agent_model ?? ""; draw(); }
    catch (error) { if (visible && mine === loads) toaster.info(describeError(error)); }
  }
  async function newGroup(): Promise<void> {
    const name = await askGroupName(t("team.newGroup")); if (!name) return;
    try { await api.groupsCreate(name); await load(); } catch (error) { toaster.info(describeError(error)); }
  }
  async function rename(g: MemberGroup): Promise<void> {
    const name = await askGroupName(t("members.rename"), g.name); if (!name) return;
    try { await api.groupsUpdate(g.id, { name }); await load(); } catch (error) { toaster.info(describeError(error)); }
  }
  async function remove(g: MemberGroup): Promise<void> {
    if (!await confirmDelete(g.name)) return;
    try { await api.groupsDelete(g.id); await load(); } catch (error) { toaster.info(describeError(error)); }
  }
  async function send(text: string, files: WireAttachment[]): Promise<void> {
    if ((!text && !files.length) || sending) return;
    if (JSON.stringify([text,files]) !== retryText) requestId = undefined;
    retryText = JSON.stringify([text,files]); requestId ??= crypto.randomUUID(); sending = true; draw();
    try { const { room } = await api.roomsCreate({ name: t("rooms.team"), member_ids: list.members.map(m => m.id), is_team: true }); await api.roomSend(room.id, text, requestId, getLocale(), files); requestId = undefined; void openGroupChat(room.id); }
    finally { sending = false; draw(); }
  }
  el.addEventListener("click", e => {
    const target = (e.target as Element).closest<HTMLElement>("[data-team]"); if (!target) return;
    const act = target.dataset.team;
    if (act === "chat") openMemberThread(app, Number(target.dataset.member));
    else if (act === "new") openActions(target, [{ id: "group", label: t("team.newGroup"), icon: "folder" }], () => void newGroup());
    else if (act === "zoom-in" || act === "zoom-out") { zoom = Math.max(0.5, Math.min(1.5, Math.round((zoom + (act === "zoom-in" ? 0.1 : -0.1)) * 10) / 10)); draw(); }
    else if (act === "group-menu") { const g = list.groups.find(g => g.id === Number(target.dataset.group)); if (g) openActions(target, [{ id: "rename", label: t("members.rename"), icon: "pencil" }, { id: "delete", label: t("members.delete"), icon: "trash" }], pick => { if (pick === "rename") void rename(g); else void remove(g); }); }
  });
  input.addEventListener("input", () => {
    const before = input.value.slice(0, input.selectionStart), match = /@([^@\n]*)$/.exec(before);
    if (!match || match[1].length > 30) { closeMenu(); return; }
    const choices = [{ value: t("team.everyone"), label: "@" + t("team.everyone") }, ...list.members.map(m => ({ value: m.name, label: "@" + m.name })), ...list.groups.map(g => ({ value: g.name, label: "@" + g.name }))].filter(c => c.value.toLocaleLowerCase().includes(match[1].toLocaleLowerCase()));
    if (choices.length) {
      const caret = input.selectionStart;
      openMenu(input, choices, "", value => { const tail = input.value.slice(caret); const prefix = before.slice(0, match.index); input.value = prefix + "@" + value + " " + tail; input.focus(); input.setSelectionRange(prefix.length + value.length + 2, prefix.length + value.length + 2); });
      input.focus();
    } else closeMenu();
  });
  board.addEventListener("dragstart", e => { const m = (e.target as Element).closest<HTMLElement>("[data-team-member]"); if (m) { e.dataTransfer?.setData("application/x-raphael-member", m.dataset.teamMember!); const target=board.querySelector<HTMLElement>(".team-drop-empty"); if(target)target.hidden=false; } });
  board.addEventListener("dragend", () => { const target=board.querySelector<HTMLElement>(".team-drop-empty");if(target)target.hidden=true; });
  board.addEventListener("dragover", e => { if ((e.target as Element).closest("[data-team-group]") && e.dataTransfer?.types.includes("application/x-raphael-member")) e.preventDefault(); });
  board.addEventListener("drop", e => { const g = (e.target as Element).closest<HTMLElement>("[data-team-group]"); if (!g) return; e.preventDefault(); const id = Number(e.dataTransfer?.getData("application/x-raphael-member")); if (!id || !list.members.some(m => m.id === id)) return; void api.membersUpdate(id, { group_id: g.dataset.teamGroup === "none" ? null : Number(g.dataset.teamGroup) }).then(() => load()).catch(error => toaster.info(describeError(error))); });
  app.onEvent(event => { if (visible && ["MembersChanged", "CoreReady", "ProvidersChanged"].includes(event.type)) void load(); });
  onLocaleChange(() => { if (visible) { draw(); void load(); } });
  return { id: "team", el, show() { visible = true; draw(); void load(); }, hide() { visible = false; loads++; } };
}
