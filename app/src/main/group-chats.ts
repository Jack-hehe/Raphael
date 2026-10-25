// Group chat entry points are independent of the member editor so both navigation columns can use them.
import * as api from "./api";
import { describeError } from "./format";
import { getLocale, onLocaleChange, t } from "./i18n";
import { askGroupName, confirmDelete, openDialog } from "./member-dialogs";
import { openActions } from "./member-column";
import { createChatComposer } from "./chat-composer";
import { messageHTML } from "./message-content";
import { filesHTML, messageActionsHTML } from "./timeline";
import { faceHTML } from "./members";
import { openMemberThread } from "./team-navigation";
import type { AppContext, MembersList, Room, RoomMessage } from "./types";
import { btn, closeMenu, copyText, esc, ico, iconButton, openMenu, sel, toaster } from "./ui";
import "./automations.css";

let context: AppContext | undefined;
const listeners = new Set<() => void>();
export function configureRooms(app: AppContext): void {
  context = app;
  app.onEvent(event => { if (["RoomsChanged", "MembersChanged", "WorkerChanged", "CoreReady"].includes(event.type)) for (const listener of listeners) listener(); });
}
let selectedRoom: number | null = null;
export const currentRoomId = (): number | null => selectedRoom;
export function clearRoomSelection(): void { selectedRoom = null; changed(); }
const changed = (): void => { for (const listener of listeners) listener(); };

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
    let group = "none", saving = false;
    const selected = new Set<number>();
    const choices = [{ value: "none", label: t("members.ungrouped") }, ...list.groups.map(g => ({ value: String(g.id), label: g.name }))];
    const dialog = openDialog({ title: t("rooms.new"), cls: "au-group-dialog", body:
      `<label class="au-field"><span>${esc(t("rooms.name"))}</span><input id="room-name" class="mb-fin" maxlength="24" autocomplete="off" placeholder="${esc(t("rooms.namePlaceholder"))}" aria-label="${esc(t("rooms.name"))}"></label>` +
      `<div class="au-field"><span>${esc(t("rooms.group"))}</span>${sel(choices, group, t("rooms.group"), ' data-dlg="group"')}</div>` +
      `<section class="au-field"><span>${esc(t("rooms.members"))}</span><div class="au-members au-room-members">${list.members.map(m => btn(faceHTML(m,30)+`<span>${esc(m.name)}</span><span data-check></span>`, "", ` data-dlg="member" data-member="${m.id}" aria-pressed="false"`)).join("")}</div></section>`,
      actions: { secondary: btn(esc(t("common.cancel")), "", ' data-dlg="cancel"'), primary: { label: esc(t("rooms.create")), attrs: ' data-dlg="create"' } }, canClose: () => !saving,
      onEnter: () => void save(), onAct(act, target) {
        if (act === "cancel") dialog.close();
        else if (act === "member") { const id=Number(target.dataset.member);if(selected.has(id))selected.delete(id);else selected.add(id);target.setAttribute("aria-pressed",String(selected.has(id)));target.querySelector("[data-check]")!.innerHTML=selected.has(id)?ico("check"):""; }
        else if (act === "create") void save();
        else if (act === "group") openMenu(target, choices, group, value => { group = value; target.querySelector("span")!.textContent = choices.find(c => c.value === value)!.label; });
      },
    });
    async function save(): Promise<void> {
      if (saving) return;
      const name = dialog.el.querySelector<HTMLInputElement>("#room-name")!.value.trim();
      const member_ids = [...selected];
      if (!name || !member_ids.length) { toaster.info(t("rooms.needs")); return; }
      saving = true; dialog.el.querySelector<HTMLButtonElement>('[data-dlg="create"]')!.disabled = true;
      try { const { room } = await api.roomsCreate({ name, member_ids, group_id: group === "none" ? null : Number(group) }); dialog.close(); changed(); opts.onCreated?.(room); void openGroupChat(room.id); }
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

/** A room occupies the member conversation pane; creation and management alone use dialogs. */
export function createRoomPane(app: AppContext, onClose: () => void, onRender: () => void = () => {}) {
  const el=document.createElement("section");el.className="mb-chat au-room-pane";el.hidden=true;
  el.innerHTML=`<header class="page-head mb-head"><span class="room-picker"></span><div class="au-room-heading"></div>${iconButton("more",t("members.more",{p0:t("rooms.title")}), ' data-room-menu')}</header><div class="ra-stage"><div class="ra-scroll"><div class="ra-col"><div class="ra-older"></div><ol class="thread au-room-thread"></ol></div></div><div class="ra-dock"></div></div>`;
  const thread=el.querySelector<HTMLElement>(".thread")!,scroll=el.querySelector<HTMLElement>(".ra-scroll")!,older=el.querySelector<HTMLElement>(".ra-older")!;
  let room:Room|undefined,list:MembersList={members:[],groups:[]},messages:RoomMessage[]=[],generation=0,opened=false,canOlder=false,loadingOlder=false;
  let requestId:string|undefined,retry="";
  const drafts=new Map<number,string>();
  const composer=createChatComposer({placeholder:()=>t("rooms.message"),transcriptTarget(){const id=room?.id;return text=>{if(room?.id===id){composer.input.value+=(composer.input.value?" ":"")+text;composer.seedDictation(text);}else if(id){const draft=drafts.get(id)??"";drafts.set(id,draft+(draft?" ":"")+text);}};},async send(text,files){if(!room)return;const id=room.id,key=JSON.stringify([text,files]);if(key!==retry)requestId=undefined;retry=key;requestId??=crypto.randomUUID();await api.roomSend(id,text,requestId,getLocale(),files);requestId=undefined;if(room?.id===id)await refresh();changed();}});
  composer.input.id="room-message";el.querySelector('.ra-dock')!.append(composer.el);
  const person=(id:number|null)=>list.members.find(m=>m.id===id);
  function draw():void {
    if(!room)return;
    el.querySelector('.au-room-heading')!.innerHTML=`<b>${esc(room.name)}</b><div class="au-room-faces">${room.member_ids.map(id=>{const m=person(id);return m?faceHTML(m,24):"";}).join("")}</div>`;
    older.innerHTML=canOlder?btn(esc(t("rooms.older")),"sm",` data-room-act="older"${loadingOlder?" disabled":""}`):"";
    const follow=scroll.scrollHeight-scroll.scrollTop-scroll.clientHeight<80,top=scroll.scrollTop;
    thread.innerHTML=messages.length?messages.map(m=>{
      const member=person(m.member_id),owner=m.member_id===null,words=m.text||(owner?"":t(m.status==="error"?"auto.error":"auto.running"));
      const actions=messageActionsHTML(m.created, false, undefined, false).replace('</div>',(m.member_id!==null&&m.turn_id?iconButton("external",t("rooms.viewIn",{p0:member?.name??t("members.title")}),` data-room-act="turn" data-member="${m.member_id}" data-turn="${m.turn_id}"`,"room-turn").replace(" title="," data-tooltip="):"")+"</div>");
      return `<li class="tn tn-plain au-room-message" data-room-message="${m.id}">${owner?`<div class="me"><div class="chat-message owner-message">${filesHTML(m.attachments)}${words?`<div class="bubble">${messageHTML(words)}</div>`:""}${actions}</div></div>`:`<div class="ra"><span class="ra-face">${member?faceHTML(member,30):""}</span><div class="ra-body"><b class="au-room-author">${esc(member?.name??t("members.title"))}</b><div class="chat-message"><div class="ans"><div class="message-text">${messageHTML(words,"",true)}</div></div>${actions}</div></div></div>`}</li>`;
    }).join(""):`<li class="au-empty">${esc(t("rooms.empty"))}</li>`;
    scroll.scrollTop=follow?scroll.scrollHeight:top;composer.draw();onRender();
  }
  async function refresh():Promise<void>{const mine=++generation,id=room?.id;if(!opened||!id)return;try{const [people,rooms,history]=await Promise.all([api.membersList(getLocale()),api.roomsList(),api.roomMessages(id)]);if(mine!==generation||!opened)return;const found=rooms.rooms.find(r=>r.id===id);if(!found){close();toaster.info(t("rooms.deleted"));return;}list=people;room=found;const first=history.messages[0]?.id??Infinity;messages=[...messages.filter(m=>m.id<first),...history.messages];draw();}catch(error){if(opened&&mine===generation)toaster.info(describeError(error));}}
  function close():void{if(room)drafts.set(room.id,composer.input.value);opened=false;generation++;el.hidden=true;clearRoomSelection();onClose();}
  async function editMembers():Promise<void>{if(!room||room.is_team)return;const id=room.id,selected=new Set(room.member_ids);let saving=false;
    const dialog=openDialog({title:t("rooms.members"),cls:"au-group-dialog",body:`<div class="au-members">${list.members.map(m=>btn(faceHTML(m,30)+`<span>${esc(m.name)}</span><span data-check>${selected.has(m.id)?ico("check"):""}</span>`,"",` data-dlg="member" data-member="${m.id}" aria-pressed="${selected.has(m.id)}"`)).join("")}</div>`,actions:{secondary:btn(esc(t("common.cancel")),"",' data-dlg="cancel"'),primary:{label:esc(t("members.save")),attrs:' data-dlg="save"'}},canClose:()=>!saving,onAct(act,target){if(act==="cancel")dialog.close();else if(act==="member"){const member=Number(target.dataset.member);if(selected.has(member))selected.delete(member);else selected.add(member);target.setAttribute("aria-pressed",String(selected.has(member)));target.querySelector('[data-check]')!.innerHTML=selected.has(member)?ico("check"):"";}else if(act==="save"&&!saving){if(!selected.size){toaster.info(t("rooms.needs"));return;}saving=true;void api.roomsUpdate(id,{member_ids:[...selected]}).then(()=>{dialog.close();changed();return refresh();}).catch(error=>toaster.info(describeError(error))).finally(()=>{saving=false;});}}});
  }
  el.querySelector('[data-room-menu]')!.addEventListener('click',e=>{if(!room)return;const id=room.id,name=room.name;openActions(e.currentTarget as HTMLElement,[{id:"rename",label:t("members.rename"),icon:"pencil"},...(!room.is_team?[{id:"members",label:t("rooms.members"),icon:"grid" as const}]:[]),{id:"delete",label:t("members.delete"),icon:"trash"}],act=>{if(act==="members")void editMembers();else if(act==="rename")void askGroupName(t("members.rename"),name,t("members.name")).then(async name=>{if(name){await api.roomsUpdate(id,{name});changed();await refresh();}}).catch(error=>toaster.info(describeError(error)));else void confirmDelete(name).then(async yes=>{if(yes){await api.roomsDelete(id);changed();close();}}).catch(error=>toaster.info(describeError(error)));});});
  el.addEventListener('click',e=>{const target=(e.target as Element).closest<HTMLElement>('[data-room-act],[data-act]');if(!target)return;const act=target.dataset.roomAct??target.dataset.act;if(["turn","copy-message","copy-code","attachment-open","older"].includes(act??""))e.stopPropagation();
    if(act==="turn"){const id=Number(target.dataset.member),turn=Number(target.dataset.turn);close();openMemberThread(app,id,turn);}
    else if(act==="copy-message"){const m=messages.find(m=>m.id===Number(target.closest<HTMLElement>('[data-room-message]')?.dataset.roomMessage));if(m)void copyText(m.text).catch(error=>toaster.info(describeError(error)));}
    else if(act==="copy-code")void copyText(target.closest('.message-code')?.querySelector('code')?.textContent??"").catch(error=>toaster.info(describeError(error)));
    else if(act==="attachment-open")void api.openAttachment(target.dataset.id??"").catch(()=>toaster.info(t("agent.fileUnavailable")));
    else if(act==="older"&&!loadingOlder&&room&&messages.length){loadingOlder=true;draw();const id=room.id,mine=generation;void api.roomMessages(id,messages[0].id).then(result=>{if(mine!==generation||!opened)return;canOlder=result.messages.length===100;messages=[...new Map([...result.messages,...messages].map(m=>[m.id,m])).values()].sort((a,b)=>a.id-b.id);}).catch(error=>toaster.info(describeError(error))).finally(()=>{loadingOlder=false;if(opened)draw();});}
  });
  const listener=():void=>{if(opened&&el.isConnected)void refresh();};listeners.add(listener);onLocaleChange(()=>{if(opened)draw();});
  return {el,close,async open(id:number){if(room)drafts.set(room.id,composer.input.value);opened=true;generation++;room={id} as Room;messages=[];canOlder=false;requestId=undefined;retry="";composer.setDraft(drafts.get(id)??"");el.hidden=false;await refresh();if(opened&&room?.id===id){canOlder=messages.length===100;draw();scroll.scrollTop=scroll.scrollHeight;composer.input.focus();}},get active(){return opened;}};
}

export function createRoomsSection(): HTMLElement {
  const el = document.createElement("section"); el.className = "au-rooms-section";
  let rooms: Room[] = [], generation = 0;
  const draw = (): void => { el.innerHTML = `<div class="mb-ghead"><span class="mb-gname">${esc(t("rooms.title"))}</span>${iconButton("plus", t("rooms.new"), ' data-room-new')}</div>` + rooms.map(r => `<div class="au-room-row">${btn(ico("chat") + `<span>${esc(r.name)}</span>`, "", ` data-room="${r.id}" aria-current="${selectedRoom === r.id}"`)}${iconButton("trash", t("members.delete"), ` data-room-delete="${r.id}"`, "danger")}</div>`).join(""); };
  const load = async (): Promise<void> => { const mine = ++generation; try { const result = await api.roomsList(); if (mine !== generation) return; rooms = result.rooms; draw(); } catch { /* A core starting up can temporarily have no rooms. */ } };
  listeners.add(() => void load()); onLocaleChange(draw);
  el.addEventListener("click", e => {
    const target = (e.target as Element).closest<HTMLElement>("[data-room], [data-room-new], [data-room-delete]");
    if (!target) return;
    if (target.hasAttribute("data-room-new")) void openNewGroupChat();
    else if (target.dataset.room) void openGroupChat(Number(target.dataset.room));
    else { const room = rooms.find(r => r.id === Number(target.dataset.roomDelete)); if (room) void confirmDelete(room.name).then(async yes => { if (yes) { try { await api.roomsDelete(room.id); changed(); } catch (error) { toaster.info(describeError(error)); } } }); }
  });
  draw(); void load(); return el;
}
