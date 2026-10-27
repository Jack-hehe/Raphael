// A team board that shares the member store, group dialogs and group-thread dispatcher.
import { createChatComposer } from "../chat-composer";
import * as api from "../api";
import { describeError } from "../format";
import { getLocale, onLocaleChange, t } from "../i18n";
import { askGroupName, confirmDelete } from "../member-dialogs";
import { createMemberActions } from "../member-actions";
import { createTeamCanvas } from "../team-canvas";
import { memberMenu, openActions } from "../member-column";
import { openNewGroupChat, openGroupChat } from "../group-chats";
import { faceHTML, memberDisplayList, memberRole } from "../members";
import { messageTooltip } from "../message-tooltip";
import { modelMark } from "../provider-marks";
import { modelPickerSelection } from "../model-picker";
import { openMemberThread } from "../team-navigation";
import { teamMentions } from "../team-mentions";
import type { AppContext, Member, MemberGroup, MembersList, Page, MemberPatch, WireAttachment, ModelCatalog, CliBrain } from "../types";
import { btn, esc, ico, iconButton, toaster } from "../ui";
import "../members.css";
import "../automations.css";

export function createTeamPage(app: AppContext): Page {
  const el = document.createElement("section"); el.className = "au-page team-page page-agent";
  el.innerHTML = `<header class="page-head"></header><div class="team-board-scroll"><div class="team-board"></div></div><div class="team-bottom"><div class="team-composer"></div><div class="team-zoom"></div></div>`;
  const composer = createChatComposer({ placeholder: () => t("team.composer"), send, stopTask });
  el.querySelector(".team-composer")!.append(composer.el);
  const hideTooltip = messageTooltip(el);
  const board = el.querySelector<HTMLElement>(".team-board")!, input = composer.input;
  let list: MembersList = { members: [], groups: [] }, model = "", zoom = 1, visible = false, loads = 0, sending = false;
  let catalog: ModelCatalog | null = null, clis: CliBrain[] = [];
  let requestId: string | undefined, retryText = "", activeRoom: number | undefined, stopPending = false;
  async function stopTask():Promise<void>{stopPending=true;if(activeRoom){await api.roomStop(activeRoom);await syncTask();}}
  async function syncTask():Promise<void>{const id=activeRoom;if(!id)return;const snapshot=await api.roomMessages(id);if(id!==activeRoom)return;composer.setTaskState(snapshot.exchange && ["queued","running"].includes(snapshot.exchange.state) || snapshot.handoffs?.some(task=>["queued","running","waiting"].includes(task.state)) ? "running" : sending ? "ack" : "idle");}
  const actions = createMemberActions({
    list: () => list, presets: () => presets, current: () => -1,
    select: id => openMemberThread(app, id), load,
    async update(id: number, patch: MemberPatch) {
      try { await api.membersUpdate(id, patch); await load(); return true; }
      catch (error) { toaster.info(describeError(error)); return false; }
    },
    async setPicture(id, file) { await api.membersSetAvatar(id, file); await load(); },
    cleared: () => {}, deleted: () => {},
  });
  let presets: import("../types").AvatarPreset[] = [];
  const canvas = createTeamCanvas(el.querySelector(".team-board-scroll")!, board, value => { zoom = value; drawZoom(); });
  function card(m: Member): string {
    const usedModel = m.model || model;
    const selected = modelPickerSelection({ role: "answer", current: usedModel, catalog, clis, label: "" });
    const modelLabel = [selected.name, selected.source].filter(Boolean).join(" · ");
    const dot = m.waiting ? `<span class="mb-dot is-wait" role="img" aria-label="${esc(t("members.waitingShort"))}"></span>` : m.unread ? `<span class="mb-dot is-unread" role="img" aria-label="${esc(t("members.unread"))}"></span>` : "";
    return `<article class="team-member" data-team-card="${m.id}"${m.id ? ` draggable="true" data-team-member="${m.id}"` : ""}><div class="team-member-head">${faceHTML(m, 40)}<div><b>${esc(m.name)}</b><span>${esc(memberRole(m))}</span></div>${dot}${m.id ? iconButton("more", t("members.more", { p0: m.name }), ` data-team="member-menu" data-member="${m.id}" aria-haspopup="menu" aria-expanded="false"`) : ""}</div><footer>${btn(ico("chat") + esc(t("team.chat")), "sm", ` data-team="chat" data-member="${m.id}"`)}<span class="team-model" title="${esc(modelLabel)}">${modelMark(selected.model ?? { id: usedModel })}<span>${esc(modelLabel)}</span></span></footer></article>`;
  }
  function group(group: MemberGroup | null, display: MembersList): string {
    const people = display.members.filter(m => m.id !== display.members[0].id && m.group_id === (group?.id ?? null));
    if (!group && !people.length) return `<section class="team-group team-drop-empty" data-team-group="none" hidden><h2>${esc(t("members.ungrouped"))}</h2></section>`;
    return `<section class="team-group" data-team-group="${group?.id ?? "none"}"><header><h2>${esc(group?.name ?? t("members.ungrouped"))}</h2><span>${people.length}</span>${group ? iconButton("more", t("members.more", { p0: group.name }), ` data-team="group-menu" data-group="${group.id}"`) : ""}</header><div class="team-group-members">${people.map(card).join("")}</div></section>`;
  }
  function layoutGroups(): void {
    for (const group of board.querySelectorAll<HTMLElement>("[data-team-group]")) {
      const count = group.querySelectorAll(".team-group-members > .team-member").length;
      group.style.setProperty("--team-columns", String(Math.max(1, Math.min(3, count))));
    }
    canvas.layout();
  }
  function draw(): void {
    el.querySelector(".page-head")!.innerHTML = `<h1>${esc(t("team.title"))}</h1>${btn(ico("plus") + esc(t("auto.new")), "pri", ' data-team="new"')}`;
    const display = memberDisplayList(list);
    board.innerHTML = `<div class="team-chief">${list.members.length ? card(display.members[0]) : ""}</div><div class="team-groups">${list.groups.map(g => group(g, display)).join("")}${group(null, display)}</div>`;
    layoutGroups();
    input.placeholder = t("team.composer"); input.setAttribute("aria-label", t("team.composer"));
    composer.draw();
  }
  function drawZoom(): void {
    el.querySelector(".team-zoom")!.innerHTML = iconButton("minus", t("team.zoomOut"), ` data-team="zoom-out"${zoom <= 0.5 ? " disabled" : ""}`) + `<span>${Math.round(zoom * 100)}%</span>` + iconButton("plus", t("team.zoomIn"), ` data-team="zoom-in"${zoom >= 1.5 ? " disabled" : ""}`) + iconButton("monitor", t("team.fit"), ' data-team="fit"');
  }
  async function load(opening = false): Promise<void> {
    const mine = ++loads;
    try {
      const [people, settings, models, brains] = await Promise.all([api.membersList(getLocale()), api.settingsGet(), api.modelsCatalog().catch(() => null), api.brainsCli().catch(() => ({ clis: [] }))]);
      if (!visible || mine !== loads) return;
      list = people; model = settings.settings.agent_model ?? ""; catalog = models; clis = brains.clis; draw();
      if (opening) canvas.revealChief();
    }
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
    retryText = JSON.stringify([text,files]); requestId ??= crypto.randomUUID(); sending = true; activeRoom=undefined;stopPending=false;draw();
    try { const { room } = await api.roomsCreate({ name: t("rooms.team"), member_ids: list.members.map(m => m.id), is_team: true }); activeRoom=room.id;await api.roomSend(room.id, text, requestId, getLocale(), files);if(stopPending){await api.roomStop(room.id);stopPending=false;}await syncTask(); requestId = undefined; void openGroupChat(room.id); }
    finally { sending = false; draw();void syncTask().catch(error=>toaster.info(describeError(error))); }
  }
  el.addEventListener("click", e => {
    const target = (e.target as Element).closest<HTMLElement>("[data-team]"); if (!target) return;
    const act = target.dataset.team;
    if (act === "chat") openMemberThread(app, Number(target.dataset.member));
    else if (act === "new") openActions(target, [
      { id: "member", label: t("members.newMember"), icon: "plus" },
      { id: "room", label: t("members.sideNewGroupChat"), icon: "chat" },
      { id: "group", label: t("team.newGroup"), icon: "folder" },
      { id: "reset-layout", label: t("team.resetLayout"), icon: "refresh" },
    ], pick => {
      if (pick === "reset-layout") canvas.reset();
      else if (pick === "room") void openNewGroupChat();
      else if (pick === "member") { app.go("agent"); window.dispatchEvent(new CustomEvent("raphael:new-member")); }
      else void newGroup();
    });
    else if (act === "zoom-in" || act === "zoom-out") canvas.zoomBy(act === "zoom-in" ? 0.1 : -0.1);
    else if (act === "fit") canvas.fit();
    else if (act === "member-menu") memberMenu(Number(target.dataset.member), target, {
      view: () => ({ list, current: -1, previews: new Map(), fresh: new Set() }),
      moveMember: (id, _group, anchor) => actions.moveMember(id, anchor),
      deleteMember: id => void actions.deleteMember(id), memberAction: (id, action) => {
        if (action === "edit") void api.membersPresets().then(result => { presets = result.presets; return actions.memberAction(id, action); }).catch(error => toaster.info(describeError(error)));
        else void actions.memberAction(id, action);
      },
    });
    else if (act === "group-menu") { const g = list.groups.find(g => g.id === Number(target.dataset.group)); if (g) openActions(target, [{ id: "rename", label: t("members.rename"), icon: "pencil" }, { id: "delete", label: t("members.delete"), icon: "trash" }], pick => { if (pick === "rename") void rename(g); else void remove(g); }); }
  });
  input.addEventListener("input", () => teamMentions(input, list));
  board.addEventListener("dblclick", e => {
    if ((e.target as Element).closest("button")) return;
    const card = (e.target as Element).closest<HTMLElement>("[data-team-card]");
    if (card) openMemberThread(app, Number(card.dataset.teamCard));
  });
  let dragging: HTMLElement | null = null;
  function clearDrop(): void {
    board.querySelectorAll(".is-drop-target").forEach(node => node.classList.remove("is-drop-target"));
    board.querySelector(".team-drop-preview")?.remove();
    layoutGroups();
  }
  function endDrag(): void {
    clearDrop(); dragging?.classList.remove("is-dragging"); dragging = null;
    const target = board.querySelector<HTMLElement>(".team-drop-empty"); if (target) target.hidden = true;
    layoutGroups();
  }
  board.addEventListener("dragstart", e => {
    const card = (e.target as Element).closest<HTMLElement>("[data-team-member]"); if (!card) return;
    dragging = card; card.classList.add("is-dragging");
    e.dataTransfer?.setData("application/x-raphael-member", card.dataset.teamMember!);
    if (e.dataTransfer) e.dataTransfer.effectAllowed = "move";
    const target = board.querySelector<HTMLElement>(".team-drop-empty"); if (target) target.hidden = false;
    layoutGroups();
  });
  board.addEventListener("dragend", endDrag);
  board.addEventListener("dragover", e => {
    const group = (e.target as Element).closest<HTMLElement>("[data-team-group]");
    if (!group || (!dragging && !e.dataTransfer?.types.includes("application/x-raphael-member"))) return;
    e.preventDefault(); if (e.dataTransfer) e.dataTransfer.dropEffect = "move";
    if (dragging?.closest("[data-team-group]") === group) { clearDrop(); return; }
    if (group.classList.contains("is-drop-target")) return;
    clearDrop(); group.classList.add("is-drop-target");
    if (dragging) {
      const preview = dragging.cloneNode(true) as HTMLElement;
      preview.removeAttribute("data-team-member"); preview.removeAttribute("data-team-card"); preview.removeAttribute("draggable");
      preview.className = "team-member team-drop-preview"; preview.inert = true; preview.setAttribute("aria-hidden", "true");
      preview.querySelectorAll("button").forEach(button => button.remove());
      (group.querySelector(".team-group-members") ?? group).append(preview);
      layoutGroups();
    }
  });
  board.addEventListener("dragleave", e => {
    const group = (e.target as Element).closest("[data-team-group]");
    if (group && (!(e.relatedTarget instanceof Node) || !group.contains(e.relatedTarget))) clearDrop();
  });
  board.addEventListener("drop", e => {
    const group = (e.target as Element).closest<HTMLElement>("[data-team-group]"); if (!group) return;
    e.preventDefault(); const id = Number(e.dataTransfer?.getData("application/x-raphael-member")); endDrag();
    if (!id || !list.members.some(member => member.id === id)) return;
    void actions.update(id, { group_id: group.dataset.teamGroup === "none" ? null : Number(group.dataset.teamGroup) });
  });
  app.onEvent(event => { if(event.type==="RoomsChanged")void syncTask().catch(error=>toaster.info(describeError(error))); if (visible && ["MembersChanged", "ConnectionsChanged", "CoreReady", "ProvidersChanged", "BrainsChanged"].includes(event.type)) void load(); });
  onLocaleChange(() => { if (visible) { draw(); void load(); } });
  return { id: "team", el, show() { visible = true; draw(); canvas.revealChief(); void load(true); }, hide() { visible = false; loads++; canvas.cancel(); endDrag(); hideTooltip(); } };
}
