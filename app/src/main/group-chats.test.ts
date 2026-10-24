// @vitest-environment happy-dom
import { beforeEach, expect, test, vi } from "vitest";
import type { AppContext, Member } from "./types";
const api=vi.hoisted(()=>({membersList:vi.fn(),roomsCreate:vi.fn(),roomsList:vi.fn(),roomMessages:vi.fn(),roomSend:vi.fn(),roomsDelete:vi.fn(),roomsUpdate:vi.fn()}));
vi.mock("./api",()=>api);
import { configureRooms, openNewGroupChat, openRoomsMenu, createRoomsSection, createRoomPane, clearRoomSelection } from "./group-chats";
import { setLocale } from "./i18n";
const flush=async():Promise<void>=>{for(let i=0;i<24;i++)await Promise.resolve();};
const room={id:1,name:"Launch",member_ids:[0,1],group_id:null,created:1,is_team:0};
let event:(e:any)=>void;
let app:AppContext;
const pane=async()=>{const view=createRoomPane(app,vi.fn());document.body.append(view.el);await view.open(1);return view;};
const draft=(text:string)=>{const input=document.querySelector<HTMLTextAreaElement>("#room-message")!;input.value=text;input.dispatchEvent(new Event("input"));return input;};
beforeEach(()=>{document.body.innerHTML="";vi.clearAllMocks();setLocale("en");const members=[0,1].map(id=>({id,name:id?"Alice":"Raphael",avatar:""} as Member));api.membersList.mockResolvedValue({members,groups:[]});api.roomsCreate.mockResolvedValue({room});api.roomsList.mockResolvedValue({rooms:[room]});api.roomMessages.mockResolvedValue({messages:[]});api.roomSend.mockResolvedValue({message_id:1,duplicate:false});app={go:vi.fn(),onEvent:(fn:any)=>{event=fn;return()=>{};}} as unknown as AppContext;clearRoomSelection();configureRooms(app);api.roomsUpdate.mockResolvedValue({room});});
test("new group requires members and saves checked identities",async()=>{
 await openNewGroupChat();document.querySelector<HTMLInputElement>('#room-name')!.value="Launch";document.querySelector<HTMLElement>('[data-dlg="create"]')!.click();await flush();expect(api.roomsCreate).not.toHaveBeenCalled();expect(document.querySelector('input[type="checkbox"]')).toBeNull();expect(document.querySelector<HTMLInputElement>('#room-name')!.placeholder).toBeTruthy();document.querySelector<HTMLElement>('[data-member="1"]')!.click();expect(document.querySelector('[data-member="1"]')!.getAttribute('aria-pressed')).toBe('true');document.querySelector<HTMLElement>('[data-dlg="create"]')!.click();await flush();expect(api.roomsCreate).toHaveBeenCalledWith({name:"Launch",member_ids:[1],group_id:null});expect(app.go).toHaveBeenCalledWith('agent');expect(document.querySelector('.au-room-dialog')).toBeNull();
});
test("failed send retains the draft and reuses its idempotency key on retry",async()=>{
 api.roomSend.mockRejectedValueOnce(new Error("offline"));await pane();const input=draft("@Alice hello");document.querySelector<HTMLElement>('.cmp-send')!.click();await flush();const id=api.roomSend.mock.calls[0][2];expect(input.value).toBe("@Alice hello");document.querySelector<HTMLElement>('.cmp-send')!.click();await flush();expect(api.roomSend.mock.calls[1][2]).toBe(id);expect(input.value).toBe("");
});
test("live replies keep the composer draft, speaker identity and escaped text",async()=>{
 await pane();const input=draft("Unsaved draft");api.roomMessages.mockResolvedValue({messages:[{id:1,member_id:1,text:"<script>untrusted()</script>",created:1,status:"done"}]});event({type:"RoomsChanged",room_id:1});await flush();expect(input.value).toBe("Unsaved draft");expect(document.querySelector('.au-room-thread')!.textContent).toContain("Alice");expect(document.querySelector('.au-room-thread script')).toBeNull();
});
test("sidebar section loads rooms and opens the selected thread",async()=>{
 const section=createRoomsSection();document.body.append(section);await flush();expect(section.textContent).toContain("Launch");section.querySelector<HTMLElement>('[data-room="1"]')!.click();await flush();expect(app.go).toHaveBeenCalledWith('agent');expect(document.querySelector('.au-room-dialog')).toBeNull();
});

test("loading older messages survives live refresh and stops offering exhausted history",async()=>{
 const latest=Array.from({length:100},(_,i)=>({id:i+101,room_id:1,member_id:null,text:`Message ${i+101}`,created:1,status:"done"}));
 api.roomMessages.mockResolvedValueOnce({messages:latest});await pane();
 api.roomMessages.mockResolvedValueOnce({messages:[{...latest[0],id:1,text:"Oldest message"}]});
 document.querySelector<HTMLElement>('[data-room-act="older"]')!.click();await flush();
 expect(document.querySelector('[data-room-act="older"]')).toBeNull();
 api.roomMessages.mockResolvedValue({messages:latest});event({type:"RoomsChanged",room_id:1});await flush();
 expect(document.querySelector('.au-room-thread')!.textContent).toContain("Oldest message");
});

test("an open group closes when its room is deleted live",async()=>{
 const view=await pane();api.roomsList.mockResolvedValue({rooms:[]});event({type:"RoomsChanged",room_id:1});await flush();expect(view.el.hidden).toBe(true);
});

test("compact rooms menu opens an existing room and offers creation",async()=>{
 const anchor=document.createElement("button");document.body.append(anchor);await openRoomsMenu(anchor);
 document.querySelector<HTMLElement>('#app-popover [data-value="1"]')!.click();await flush();expect(app.go).toHaveBeenCalledWith('agent');expect(document.querySelector('.au-room-dialog')).toBeNull();
 await openRoomsMenu(anchor);
 document.querySelector<HTMLElement>('#app-popover [data-action]')!.click();await flush();expect(document.querySelector('#room-name')).not.toBeNull();
});


test("room thread uses owner bubbles, named replies and a hover action into the member conversation",async()=>{
 api.roomMessages.mockResolvedValue({messages:[{id:1,member_id:null,text:"Hello",created:1,status:"done"},{id:2,member_id:1,text:"Done",created:2,status:"done",turn_id:23}]});
 await pane();expect(document.querySelector('.me .bubble')!.textContent).toBe("Hello");expect(document.querySelector('.ra .au-room-author')!.textContent).toBe("Alice");
 const action=document.querySelector<HTMLElement>('[data-room-act="turn"]')!;expect(action.closest('.message-actions')).not.toBeNull();expect(action.dataset.tooltip).toContain("Alice");expect(action.textContent).not.toContain("Open conversation");expect(document.querySelector('.cmp-attach')).not.toBeNull();expect(document.querySelector('.cmp-voice')).not.toBeNull();expect(document.querySelector('[role="dialog"]')).toBeNull();
});

test("room header members menu saves selected identities",async()=>{
 await pane();document.querySelector<HTMLElement>('[data-room-menu]')!.click();document.querySelector<HTMLElement>('#app-popover [data-pick="members"]')!.click();
 document.querySelector<HTMLElement>('[data-dlg="member"][data-member="0"]')!.click();document.querySelector<HTMLElement>('[data-dlg="save"]')!.click();await flush();expect(api.roomsUpdate).toHaveBeenCalledWith(1,{member_ids:[1]});
});

test("room rename and delete require their management dialogs",async()=>{
 const view=await pane();document.querySelector<HTMLElement>('[data-room-menu]')!.click();document.querySelector<HTMLElement>('#app-popover [data-pick="rename"]')!.click();
 document.querySelector<HTMLInputElement>('#mb-group-name')!.value="Planning";document.querySelector<HTMLElement>('[data-dlg="save"]')!.click();await flush();expect(api.roomsUpdate).toHaveBeenCalledWith(1,{name:"Planning"});
 document.querySelector<HTMLElement>('[data-room-menu]')!.click();document.querySelector<HTMLElement>('#app-popover [data-pick="delete"]')!.click();expect(api.roomsDelete).not.toHaveBeenCalled();document.querySelector<HTMLElement>('[data-dlg="delete"]')!.click();await flush();expect(api.roomsDelete).toHaveBeenCalledWith(1);expect(view.el.hidden).toBe(true);
});

test("attachment-only owner messages hydrate through the shared media renderer without a running bubble",async()=>{
 api.roomMessages.mockResolvedValue({messages:[{id:1,member_id:null,text:"",created:1,status:"done",attachments:[{id:"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.png",name:"chart.png",mime:"image/png",size:10}]}]});
 const rendered=vi.fn(),view=createRoomPane(app,vi.fn(),rendered);document.body.append(view.el);await view.open(1);expect(rendered).toHaveBeenCalled();expect(view.el.querySelector('img[data-attachment="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.png"]')).not.toBeNull();expect(view.el.querySelector('.me .bubble')).toBeNull();
});
