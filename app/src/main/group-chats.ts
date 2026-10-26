// Group chat entry points are independent of the member editor so both navigation columns can use them.
import * as api from "./api";
import { describeError } from "./format";
import { getLocale, onLocaleChange, t } from "./i18n";
import { askGroupName, confirmDelete, openDialog } from "./member-dialogs";
import { openActions } from "./member-column";
import { createChatComposer } from "./chat-composer";
import { messageHTML } from "./message-content";
import { teamMentions } from "./team-mentions";
import { roomWrapupHTML } from "./room-wrapup";
import { filesHTML, messageActionsHTML } from "./timeline";
import { faceHTML } from "./members";
import type { AppContext, MembersList, Room, RoomMessage, RoomExchange } from "./types";
import { btn, closeMenu, copyText, esc, ico, iconButton, openMenu, sel, toaster } from "./ui";
import "./automations.css";

let context: AppContext | undefined;
const listeners = new Set<() => void>();
export function configureRooms(app: AppContext): void {
  context = app;
  document.addEventListener("click", event => {
    const link = (event.target as Element).closest<HTMLElement>("[data-meeting-room]");
    if (link) void openGroupChat(Number(link.dataset.meetingRoom));
  });
  app.onEvent(event => { if (["RoomsChanged", "MembersChanged", "ConnectionsChanged", "WorkerChanged", "CoreReady"].includes(event.type)) for (const listener of listeners) listener(); });
}
let selectedRoom: number | null = null;
export const currentRoomId = (): number | null => selectedRoom;
export function clearRoomSelection(): void { selectedRoom = null; changed(); }
const changed = (): void => { for (const listener of listeners) listener(); };
const responderChoices = (list: MembersList, selected: Set<number>) => [
  { value: "auto", label: t("rooms.autoResponder") },
  ...list.members.filter(m => selected.has(m.id)).map(m => ({ value: `member:${m.id}`, label: m.name })),
  { value: "everyone", label: t("team.everyone") },
];
const responderField = (list: MembersList, selected: Set<number>, value: string) => `<div class="au-field"><span>${esc(t("rooms.responder"))}</span><span data-responder>${sel(responderChoices(list,selected),value,t("rooms.responder"),' data-dlg="responder"')}</span></div>`;

export async function openRoomsMenu(anchor: HTMLElement): Promise<void> {
  try {
    const { rooms } = await api.roomsList();
    openMenu(anchor, rooms.map(room => ({ value: String(room.id), label: room.name })), "", value => void openGroupChat(Number(value)), undefined, [
      { label: t("rooms.new"), icon: "plus", run: () => { closeMenu(); void openNewGroupChat(); } },
    ]);
  } catch (error) { toaster.info(describeError(error)); }
}

export async function openNewGroupChat(opts: { onCreated?(room: Room): void } = {}): Promise<void> {
  try {
    const list = await api.membersList(getLocale());
    let group = "none", saving = false, responder = "auto";
    const creationRequestId = crypto.randomUUID();
    const selected = new Set<number>([0]);
    const choices = [{ value: "none", label: t("members.ungrouped") }, ...list.groups.map(g => ({ value: String(g.id), label: g.name }))];
    const dialog = openDialog({ title: t("rooms.new"), cls: "au-group-dialog", body:
      `<label class="au-field"><span>${esc(t("rooms.name"))}</span><input id="room-name" class="mb-fin" maxlength="24" autocomplete="off" value="${esc(list.members.find(m=>m.id===0)?.name??"Raphael")}" placeholder="${esc(t("rooms.namePlaceholder"))}" aria-label="${esc(t("rooms.name"))}"></label>` +
      `<div class="au-field"><span>${esc(t("rooms.group"))}</span>${sel(choices, group, t("rooms.group"), ' data-dlg="group"')}</div>` +
      `<section class="au-field"><span>${esc(t("rooms.members"))}</span><div class="au-members au-room-members">${list.members.map(m => btn(faceHTML(m,30)+`<span>${esc(m.name)}</span><span data-check>${m.id===0?ico("check"):""}</span>`, "", ` data-dlg="member" data-member="${m.id}" aria-pressed="${m.id===0}"${m.id===0?" disabled":""}`)).join("")}</div></section>` + `<div class="room-picked" data-picked>${list.members.filter(m=>selected.has(m.id)).map(m=>`<span>${esc(m.name)}</span>`).join("")}</div>` + responderField(list,selected,responder),
      actions: { secondary: btn(esc(t("common.cancel")), "", ' data-dlg="cancel"'), primary: { label: esc(t("rooms.create")), attrs: ' data-dlg="create"' } }, canClose: () => !saving,
      onEnter: () => void save(), onAct(act, target) {
        if (act === "cancel") dialog.close();
        else if (act === "member") { const id=Number(target.dataset.member);if(id===0)return;if(selected.has(id))selected.delete(id);else {if(selected.size>=6){toaster.info(t("rooms.memberCount"));return;}selected.add(id);}target.setAttribute("aria-pressed",String(selected.has(id)));target.querySelector("[data-check]")!.innerHTML=selected.has(id)?ico("check"):"";if(responder===`member:${id}`&&!selected.has(id))responder="auto";dialog.el.querySelector('[data-responder]')!.innerHTML=sel(responderChoices(list,selected),responder,t("rooms.responder"),' data-dlg="responder"');dialog.el.querySelector('[data-picked]')!.innerHTML=list.members.filter(m=>selected.has(m.id)).map(m=>`<span>${esc(m.name)}</span>`).join("");if(!nameEdited)nameInput.value=list.members.filter(m=>selected.has(m.id)).map(m=>m.name).join(" · ").slice(0,24); }
        else if (act === "create") void save();
        else if (act === "group") openMenu(target, choices, group, value => { group = value; target.querySelector("span")!.textContent = choices.find(c => c.value === value)!.label; });
        else if (act === "responder") { const choices=responderChoices(list,selected);openMenu(target,choices,responder,value=>{responder=value;target.querySelector('span')!.textContent=choices.find(c=>c.value===value)!.label;}); }
      },
    });
    const nameInput=dialog.el.querySelector<HTMLInputElement>("#room-name")!;
    let nameEdited=false;
    nameInput.addEventListener("input",()=>{nameEdited=true;});
    async function save(): Promise<void> {
      if (saving) return;
      const name = dialog.el.querySelector<HTMLInputElement>("#room-name")!.value.trim();
      const member_ids = [...selected];
      if (!name || member_ids.length<2 || member_ids.length>6) { toaster.info(t("rooms.memberCount")); return; }
      saving = true; dialog.el.querySelector<HTMLButtonElement>('[data-dlg="create"]')!.disabled = true;
      try { const { room } = await api.roomsCreate({ name, member_ids, group_id: group === "none" ? null : Number(group), default_responder:responder, request_id:creationRequestId }); dialog.close(); changed(); opts.onCreated?.(room); void openGroupChat(room.id); }
      catch (error) { toaster.info(describeError(error)); }
      finally { saving = false; const b = dialog.el.querySelector<HTMLButtonElement>('[data-dlg="create"]'); if (b) b.disabled = false; }
    }
  } catch (error) { toaster.info(describeError(error)); }
}

export async function openGroupChat(roomId: number): Promise<void> {
  selectedRoom = roomId; changed();
  context?.go("agent");
  window.dispatchEvent(new CustomEvent("raphael:open-room", { detail: { roomId } }));
}

async function editRoomMembers(room: Room, saved: () => void): Promise<void> {
  try {
    const list = await api.membersList(getLocale()), selected = new Set([0,...room.member_ids]);
    let saving = false, responder = room.default_responder ?? "auto";
    const dialog = openDialog({ title: t("rooms.members"), cls: "au-group-dialog",
      body: `<div class="au-members">${list.members.map(m => btn(faceHTML(m,30) + `<span>${esc(m.name)}</span><span data-check>${selected.has(m.id) ? ico("check") : ""}</span>`, "", ` data-dlg="member" data-member="${m.id}" aria-pressed="${selected.has(m.id)}"${m.id===0?" disabled":""}`)).join("")}</div>` + responderField(list,selected,responder),
      actions: { secondary: btn(esc(t("common.cancel")), "", ' data-dlg="cancel"'), primary: { label: esc(t("members.save")), attrs: ' data-dlg="save"' } },
      canClose: () => !saving,
      onAct(act, target) {
        if (act === "cancel") dialog.close();
        else if (act === "member") {
          const id = Number(target.dataset.member);
          if(id===0)return;
          if (selected.has(id)) selected.delete(id); else { if(selected.size>=6){toaster.info(t("rooms.memberCount"));return;}selected.add(id); }
          target.setAttribute("aria-pressed", String(selected.has(id)));
          target.querySelector('[data-check]')!.innerHTML = selected.has(id) ? ico("check") : "";
          if(responder===`member:${id}`&&!selected.has(id))responder="auto";
          dialog.el.querySelector('[data-responder]')!.innerHTML=sel(responderChoices(list,selected),responder,t("rooms.responder"),' data-dlg="responder"');
        } else if (act === "responder") {
          const choices=responderChoices(list,selected);openMenu(target,choices,responder,value=>{responder=value;target.querySelector('span')!.textContent=choices.find(c=>c.value===value)!.label;});
        } else if (act === "save" && !saving) {
          if (selected.size<2||selected.size>6) { toaster.info(t("rooms.memberCount")); return; }
          saving = true;
          void api.roomsUpdate(room.id, { member_ids: [...selected], default_responder:responder }).then(() => {
            dialog.close(); changed(); saved();
          }).catch(error => toaster.info(describeError(error))).finally(() => { saving = false; });
        }
      },
    });
  } catch (error) { toaster.info(describeError(error)); }
}

async function editRoomRoles(room: Room, saved: () => void): Promise<void> {
  try {
    const list = await api.membersList(getLocale()), roles = { ...room.roles };
    const choices = [{ value: "participant", label: t("rooms.participant") }, { value: "moderator", label: t("rooms.moderator") },
      { value: "pro", label: t("rooms.pro") }, { value: "con", label: t("rooms.con") }];
    let saving = false;
    const dialog = openDialog({ title: t("rooms.roles"), cls: "au-group-dialog",
      body: room.member_ids.map(id => `<div class="au-field"><span>${esc(list.members.find(m => m.id === id)?.name ?? "")}</span>${sel(choices, roles[id] ?? "participant", t("rooms.roles"), ` data-dlg="role" data-member="${id}"`)}</div>`).join(""),
      actions: { secondary: btn(esc(t("common.cancel")), "", ' data-dlg="cancel"'), primary: { label: esc(t("members.save")), attrs: ' data-dlg="save"' } },
      canClose: () => !saving,
      onAct(action, target) {
        if (action === "cancel") dialog.close();
        else if (action === "role") openMenu(target, choices, roles[target.dataset.member!] ?? "participant", value => {
          if (value === "moderator") {
            for (const id of Object.keys(roles)) if (roles[id] === "moderator") {
              roles[id] = "participant";
              const field = dialog.el.querySelector(`[data-member="${id}"] span`);
              if (field) field.textContent = t("rooms.participant");
            }
          }
          roles[target.dataset.member!] = value;
          target.querySelector("span")!.textContent = choices.find(c => c.value === value)!.label;
        });
        else if (action === "save" && !saving) {
          saving = true;
          void api.roomsUpdate(room.id, { roles }).then(() => { dialog.close(); changed(); saved(); })
            .catch(error => toaster.info(describeError(error))).finally(() => { saving = false; });
        }
      },
    });
  } catch (error) { toaster.info(describeError(error)); }
}

function roomActions(anchor: HTMLElement, room: Room, saved: () => void = () => {}, deleted: () => void = () => {}): void {
  openActions(anchor, [
    { id: "rename", label: t("members.rename"), icon: "pencil" },
    ...(!room.is_team ? [{ id: "members", label: t("rooms.members"), icon: "grid" as const }] : []),
    { id: "roles", label: t("rooms.roles"), icon: "grid" },
    { id: "delete", label: t("members.delete"), icon: "trash" },
  ], action => {
    if (action === "roles") void editRoomRoles(room, saved);
    else if (action === "members") void editRoomMembers(room, saved);
    else if (action === "rename") void askGroupName(t("members.rename"), room.name, t("members.name")).then(async name => {
      if (name) { await api.roomsUpdate(room.id, { name }); changed(); saved(); }
    }).catch(error => toaster.info(describeError(error)));
    else void confirmDelete(room.name).then(async yes => {
      if (yes) { await api.roomsDelete(room.id); changed(); deleted(); }
    }).catch(error => toaster.info(describeError(error)));
  });
}

/** A room occupies the member conversation pane; creation and management alone use dialogs. */
export function createRoomPane(_app: AppContext, onClose: () => void, onRender: () => void = () => {}) {
  const el=document.createElement("section");el.className="mb-chat au-room-pane";el.hidden=true;
  el.innerHTML=`<header class="page-head mb-head"><span class="room-picker"></span><div class="au-room-heading"></div>${iconButton("more",t("members.more",{p0:t("rooms.title")}), ' data-room-menu')}</header><div class="ra-stage"><div class="ra-scroll"><div class="ra-col"><div class="ra-older"></div><ol class="thread au-room-thread"></ol></div></div><div class="ra-dock"></div></div>`;
  const thread=el.querySelector<HTMLElement>(".thread")!,scroll=el.querySelector<HTMLElement>(".ra-scroll")!,older=el.querySelector<HTMLElement>(".ra-older")!;
  let room:Room|undefined,list:MembersList={members:[],groups:[]},messages:RoomMessage[]=[],generation=0,opened=false,canOlder=false,loadingOlder=false;
  let requestId:string|undefined,retry="";
  let exchange:RoomExchange|null|undefined,replyTo:RoomMessage|undefined,stopping=false;
  const drafts=new Map<number,string>();
  const composer=createChatComposer({placeholder:()=>t("rooms.message"),transcriptTarget(){const id=room?.id;return text=>{if(room?.id===id){composer.input.value+=(composer.input.value?" ":"")+text;composer.seedDictation(text);}else if(id){const draft=drafts.get(id)??"";drafts.set(id,draft+(draft?" ":"")+text);}};},async send(text,files){if(!room)return;const id=room.id,key=JSON.stringify([text,files,replyTo?.id]);if(key!==retry)requestId=undefined;retry=key;requestId??=crypto.randomUUID();await api.roomSend(id,text,requestId,getLocale(),files,replyTo?.id);requestId=undefined;if(room?.id===id){replyTo=undefined;await refresh();}changed();}});
  composer.input.addEventListener("input", () => teamMentions(composer.input, list, room?.member_ids));
  composer.input.id="room-message";el.querySelector('.ra-dock')!.append(composer.el);
  composer.el.querySelector('.cmp-quote')!.addEventListener('click',e=>{if((e.target as Element).closest('[data-room-act="cancel-reply"]')){replyTo=undefined;draw();composer.input.focus();}});
  const status=document.createElement('div');status.className="au-room-controls";el.querySelector('.ra-dock')!.prepend(status);
  const person=(id:number|null)=>list.members.find(m=>m.id===id);
  function draw():void {
    if(!room)return;
    el.querySelector('.au-room-heading')!.innerHTML=`<b>${esc(room.name)}</b><div class="au-room-faces">${room.member_ids.map(id=>{const m=person(id);return m?`<span class="au-room-participant">${faceHTML(m,24)}${id===0?`<span class="au-room-crown" role="img" aria-label="${esc(t("rooms.chief"))}">${ico("crown")}</span>`:""}</span>`:"";}).join("")}</div>`;
    const active=exchange&&["queued","running"].includes(exchange.state);
    status.innerHTML=room.membership_state==="needs_adjustment"?btn(esc(t("rooms.adjustMembers")),"sm",' data-room-act="members"'):active?btn(esc(t(exchange?.stop_requested?"rooms.stopping":"rooms.stop")),"sm",` data-room-act="stop"${stopping||exchange?.stop_requested?" disabled":""}`):"";
    const quote=composer.el.querySelector<HTMLElement>('.cmp-quote')!;quote.hidden=!replyTo;
    quote.innerHTML=replyTo?`<span>${esc(t("rooms.replyTo",{p0:person(replyTo.member_id)?.name??t("members.title")}))}</span>${iconButton("x",t("common.cancel"),' data-room-act="cancel-reply"')}`:"";
    const notices = (exchange?.reply_intents ?? []).filter(intent => intent.state === "blocked");
    const reasons = { owner: "rooms.blockedOwner", revoked: "rooms.blockedRevoked", cost: "rooms.blockedCost", restart: "rooms.blockedRestart", worker: "rooms.blockedWorker", source_unavailable: "rooms.blockedSource" } as const;
    status.innerHTML += notices.map(intent => `<span class="room-intent-blocked" role="status">${esc(t("rooms.intentBlocked", { p0: person(intent.recipient_id)?.name ?? intent.recipient_name, p1: t(reasons[intent.reason as keyof typeof reasons] ?? "rooms.blockedWorker") }))}</span>`).join("");
    if (exchange?.routing?.fallback) status.innerHTML += `<span role="status">${esc(t("rooms.routingFallback"))}</span>`;
    older.innerHTML=canOlder?btn(esc(t("rooms.older")),"sm",` data-room-act="older"${loadingOlder?" disabled":""}`):"";
    const follow=scroll.scrollHeight-scroll.scrollTop-scroll.clientHeight<80,top=scroll.scrollTop;
    const thinking=messages.find(m=>m.member_id!==null&&m.status==="running");
    thread.innerHTML=messages.length?messages.map(m=>{
      if(m.member_id!==null&&m.status==="running") {
        if(m!==thinking)return "";
        const member=person(m.member_id);
        return `<li class="tn tn-plain au-room-thinking" data-room-message="${m.id}" role="status"><span class="ra-face">${member?faceHTML(member,30):""}</span><span>${esc(t("rooms.speaking",{p0:member?.name??t("members.title")}))}</span></li>`;
      }
      const member=person(m.member_id),owner=m.member_id===null,words=m.text||(owner?"":t(m.status==="error"?"auto.error":"auto.running"));
      const source=m.reply_reference;
      const reply=source?`<span class="au-room-reply">${esc(source.unavailable?t("rooms.sourceMissing"):t("rooms.replyTo",{p0:source.author_kind==="owner"?t("rooms.owner"):person(source.author_member_id??null)?.name??t("rooms.sourceMissing")}))}</span>`:"";
      const actions=messageActionsHTML(m.created, false, undefined, false).replace('</div>',(!owner?iconButton("reply",t("rooms.reply"),` data-room-act="reply" data-message="${m.id}"`).replace(" title="," data-tooltip="):"")+"</div>");
      return `<li class="tn tn-plain au-room-message${m.kind==="wrapup"?" au-room-wrapup":""}" data-room-message="${m.id}">${owner?`<div class="me"><div class="chat-message owner-message">${reply}${filesHTML(m.attachments)}${words?`<div class="bubble">${messageHTML(words)}</div>`:""}${actions}</div></div>`:`<div class="ra"><span class="ra-face">${member?faceHTML(member,30):""}</span><div class="ra-body"><div class="au-room-meta"><b class="au-room-author">${esc(member?.name??t("members.title"))}</b>${member?.role?`<span class="au-room-role">${esc(member.role)}</span>`:""}${reply}</div><div class="chat-message"><div class="ans">${m.kind==="wrapup"?`<b class="au-room-wrapup-title">${esc(t("rooms.wrapup"))}</b>`:""}<div class="message-text">${m.kind==="wrapup"?roomWrapupHTML(words,list.members.map(member=>member.name)):messageHTML(words,"",true)}</div></div>${actions}</div></div></div>`}</li>`;
    }).join(""):`<li class="au-empty">${esc(t("rooms.empty"))}</li>`;
    scroll.scrollTop=follow?scroll.scrollHeight:top;composer.draw();onRender();
  }
  async function refresh():Promise<void>{const mine=++generation,id=room?.id;if(!opened||!id)return;try{const [people,rooms,history]=await Promise.all([api.membersList(getLocale()),api.roomsList(),api.roomMessages(id)]);if(mine!==generation||!opened)return;const found=rooms.rooms.find(r=>r.id===id);if(!found){close();toaster.info(t("rooms.deleted"));return;}list=people;room=found;exchange=history.exchange;const first=history.messages[0]?.id??Infinity;messages=[...messages.filter(m=>m.id<first),...history.messages];draw();}catch(error){if(opened&&mine===generation)toaster.info(describeError(error));}}
  function close():void{if(room)drafts.set(room.id,composer.input.value);opened=false;generation++;el.hidden=true;clearRoomSelection();onClose();}
  el.querySelector('[data-room-menu]')!.addEventListener('click',e=>{if(room)roomActions(e.currentTarget as HTMLElement,room,()=>void refresh(),close);});
  el.addEventListener('click',e=>{const target=(e.target as Element).closest<HTMLElement>('[data-room-act],[data-act]');if(!target)return;const act=target.dataset.roomAct??target.dataset.act;if(["turn","copy-message","copy-code","attachment-open","older"].includes(act??""))e.stopPropagation();
    if(act==="members"&&room)void editRoomMembers(room,()=>void refresh());
    else if(act==="stop"&&room&&!stopping){stopping=true;const id=room.id;draw();void api.roomStop(id).then(()=>refresh()).catch(error=>toaster.info(describeError(error))).finally(()=>{stopping=false;if(opened)draw();});}
    else if(act==="reply"){replyTo=messages.find(m=>m.id===Number(target.dataset.message));draw();composer.input.focus();}
    else if(act==="cancel-reply"){replyTo=undefined;draw();composer.input.focus();}
    else if(act==="copy-message"){const m=messages.find(m=>m.id===Number(target.closest<HTMLElement>('[data-room-message]')?.dataset.roomMessage));if(m)void copyText(m.text).catch(error=>toaster.info(describeError(error)));}
    else if(act==="copy-code")void copyText(target.closest('.message-code')?.querySelector('code')?.textContent??"").catch(error=>toaster.info(describeError(error)));
    else if(act==="attachment-open")void api.openAttachment(target.dataset.id??"").catch(()=>toaster.info(t("agent.fileUnavailable")));
    else if(act==="older"&&!loadingOlder&&room&&messages.length){loadingOlder=true;draw();const id=room.id,mine=generation;void api.roomMessages(id,messages[0].id).then(result=>{if(mine!==generation||!opened)return;canOlder=result.messages.length===100;messages=[...new Map([...result.messages,...messages].map(m=>[m.id,m])).values()].sort((a,b)=>a.id-b.id);}).catch(error=>toaster.info(describeError(error))).finally(()=>{loadingOlder=false;if(opened)draw();});}
  });
  const listener=():void=>{if(opened&&el.isConnected)void refresh();};listeners.add(listener);onLocaleChange(()=>{if(opened)draw();});
  return {el,close,async open(id:number){if(room)drafts.set(room.id,composer.input.value);opened=true;generation++;room={id} as Room;messages=[];exchange=undefined;replyTo=undefined;stopping=false;canOlder=false;requestId=undefined;retry="";composer.setDraft(drafts.get(id)??"");el.hidden=false;await refresh();if(opened&&room?.id===id){canOlder=messages.length===100;draw();scroll.scrollTop=scroll.scrollHeight;composer.input.focus();}},get active(){return opened;}};
}

export function createRoomsSection(): HTMLElement {
  const el = document.createElement("section"); el.className = "au-rooms-section";
  let rooms: Room[] = [], generation = 0;
  const draw = (): void => { el.innerHTML = `<div class="mb-ghead"><span class="mb-gname">${esc(t("rooms.title"))}</span>${iconButton("plus", t("rooms.new"), ' data-room-new')}</div>` + rooms.map(r => `<div class="au-room-row">${btn(ico("chat") + `<span>${esc(r.name)}</span>`, "", ` data-room="${r.id}" aria-current="${selectedRoom === r.id}"`)}${iconButton("more", t("members.more",{p0:r.name}), ` data-room-more="${r.id}" aria-haspopup="menu" aria-expanded="false"`, "au-room-more")}</div>`).join(""); };
  const load = async (): Promise<void> => { const mine = ++generation; try { const result = await api.roomsList(); if (mine !== generation) return; rooms = result.rooms; draw(); } catch { /* A core starting up can temporarily have no rooms. */ } };
  listeners.add(() => void load()); onLocaleChange(draw);
  el.addEventListener("click", e => {
    const target = (e.target as Element).closest<HTMLElement>("[data-room], [data-room-new], [data-room-more]");
    if (!target) return;
    if (target.hasAttribute("data-room-new")) void openNewGroupChat();
    else if (target.dataset.room) void openGroupChat(Number(target.dataset.room));
    else { const room = rooms.find(r => r.id === Number(target.dataset.roomMore)); if (room) roomActions(target,room); }
  });
  draw(); void load(); return el;
}
