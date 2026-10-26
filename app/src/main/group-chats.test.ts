// @vitest-environment happy-dom
import { beforeEach, expect, test, vi } from "vitest";
import type { AppContext, Member } from "./types";
import * as chatComposer from "./chat-composer";
const api=vi.hoisted(()=>({membersList:vi.fn(),roomsCreate:vi.fn(),roomsList:vi.fn(),roomMessages:vi.fn(),roomSend:vi.fn(),roomsDelete:vi.fn(),roomsUpdate:vi.fn(),roomStop:vi.fn(),core:vi.fn()}));
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
 await openNewGroupChat();document.querySelector<HTMLInputElement>('#room-name')!.value="Launch";document.querySelector("#room-name")!.dispatchEvent(new Event("input"));document.querySelector<HTMLElement>('[data-dlg="create"]')!.click();await flush();expect(api.roomsCreate).not.toHaveBeenCalled();expect(document.querySelector('input[type="checkbox"]')).toBeNull();expect(document.querySelector<HTMLInputElement>('#room-name')!.placeholder).toBeTruthy();document.querySelector<HTMLElement>('[data-member="1"]')!.click();expect(document.querySelector('[data-member="1"]')!.getAttribute('aria-pressed')).toBe('true');document.querySelector<HTMLElement>('[data-dlg="member"][data-member="0"]')!.click();document.querySelector<HTMLElement>('[data-dlg="create"]')!.click();await flush();expect(api.roomsCreate).toHaveBeenCalledWith({name:"Launch",member_ids:[0,1],group_id:null,default_responder:"auto",request_id:expect.any(String)});expect(app.go).toHaveBeenCalledWith('agent');expect(document.querySelector('.au-room-dialog')).toBeNull();
});
test("failed send retains the draft and reuses its idempotency key on retry",async()=>{
 api.roomSend.mockRejectedValueOnce(new Error("offline"));await pane();const input=draft("@Alice hello");document.querySelector<HTMLElement>('.cmp-send')!.click();await flush();const id=api.roomSend.mock.calls[0][2];expect(input.value).toBe("@Alice hello");document.querySelector<HTMLElement>('.cmp-send')!.click();await flush();expect(api.roomSend.mock.calls[1][2]).toBe(id);expect(input.value).toBe("");
});

test("generated room names preserve owner edits and creation retries reuse the request",async()=>{
 api.roomsCreate.mockRejectedValueOnce(new Error("offline"));
 await openNewGroupChat();
 expect(document.querySelector<HTMLButtonElement>('[data-dlg="member"][data-member="0"]')!.disabled).toBe(true);
 document.querySelector<HTMLElement>('[data-dlg="member"][data-member="1"]')!.click();
 const input=document.querySelector<HTMLInputElement>('#room-name')!;
 expect(input.value).toBe('Raphael · Alice');input.value='Owner name';input.dispatchEvent(new Event('input'));
 document.querySelector<HTMLElement>('[data-dlg="create"]')!.click();await flush();
 document.querySelector<HTMLElement>('[data-dlg="create"]')!.click();await flush();
 expect(api.roomsCreate.mock.calls[0][0]).toEqual(api.roomsCreate.mock.calls[1][0]);
 expect(api.roomsCreate.mock.calls[1][0].name).toBe('Owner name');
});

test("reply labels distinguish owner sources from unavailable sources",async()=>{
 api.roomMessages.mockResolvedValue({messages:[
  {id:2,member_id:1,text:'Answer',created:1,status:'done',reply_reference:{message_id:1,author_kind:'owner',author_member_id:null}},
  {id:3,member_id:1,text:'Kept reply',created:1,status:'done',reply_reference:{message_id:9,unavailable:true}},
 ]});await pane();
 expect([...document.querySelectorAll('.au-room-reply')].map(el=>el.textContent)).toEqual(['Reply to You','Source unavailable']);
});

test("the room voice target seeds correction learning while preserving reply delivery",async()=>{
 const create=vi.spyOn(chatComposer,"createChatComposer");
 try {
  api.core.mockResolvedValue({learned:["OpenMausBot"]});
  api.roomMessages.mockResolvedValue({messages:[{id:7,member_id:1,text:"Ready",created:1,status:"done"}]});
  await pane();document.querySelector<HTMLElement>('[data-room-act="reply"]')!.click();
  create.mock.calls[create.mock.calls.length-1][0].transcriptTarget!()("Please use Open Mouse Bot");
  draft("Please use OpenMausBot");document.querySelector<HTMLElement>('.cmp-send')!.click();await flush();
  expect(api.roomSend).toHaveBeenCalledWith(1,"Please use OpenMausBot",expect.any(String),"en",[],7);
  expect(api.core).toHaveBeenCalledExactlyOnceWith("dictionary.learn",{corrections:[{word:"OpenMausBot",misheard:"Open Mouse Bot"}]});
  expect(document.querySelector('.cmp-quote')!.textContent).toBe("");
 } finally { create.mockRestore(); }
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


test("room thread uses owner bubbles and keeps room turns out of private navigation",async()=>{
 api.roomMessages.mockResolvedValue({messages:[{id:1,member_id:null,text:"Hello",created:1,status:"done"},{id:2,member_id:1,text:"Done",created:2,status:"done",turn_id:23}]});
 await pane();expect(document.querySelector('.me .bubble')!.textContent).toBe("Hello");expect(document.querySelector('.ra .au-room-author')!.textContent).toBe("Alice");
 expect(document.querySelector('[data-room-act="turn"]')).toBeNull();expect(document.querySelector('.cmp-attach')).not.toBeNull();expect(document.querySelector('.cmp-voice')).not.toBeNull();expect(document.querySelector('[role="dialog"]')).toBeNull();
});

test("room header members menu saves selected identities",async()=>{
 await pane();document.querySelector<HTMLElement>('[data-room-menu]')!.click();document.querySelector<HTMLElement>('#app-popover [data-pick="members"]')!.click();
 await flush();
 document.querySelector<HTMLElement>('[data-dlg="member"][data-member="0"]')!.click();document.querySelector<HTMLElement>('[data-dlg="save"]')!.click();await flush();expect(api.roomsUpdate).toHaveBeenCalledWith(1,{member_ids:[0,1],default_responder:"auto"});
});

test("room rows expose rename, membership and confirmed deletion through more",async()=>{
 const section=createRoomsSection();document.body.append(section);await flush();
 expect(section.querySelector('[data-room-delete]')).toBeNull();
 const more=section.querySelector<HTMLButtonElement>('[data-room-more="1"]')!;
 expect(more.getAttribute('aria-haspopup')).toBe('menu');more.click();
 expect([...document.querySelectorAll('#app-popover [data-pick]')].map(el=>(el as HTMLElement).dataset.pick)).toEqual(['rename','members','delete']);
 document.querySelector<HTMLElement>('[data-pick="delete"]')!.click();await flush();
 expect(api.roomsDelete).not.toHaveBeenCalled();
 document.querySelector<HTMLElement>('[data-dlg="cancel"]')!.click();await flush();
 expect(api.roomsDelete).not.toHaveBeenCalled();
 more.click();document.querySelector<HTMLElement>('[data-pick="delete"]')!.click();await flush();
 document.querySelector<HTMLElement>('[data-dlg="delete"]')!.click();await flush();
 expect(api.roomsDelete).toHaveBeenCalledWith(1);
});

test("reply chips identify their recipient including Raphael's zero id and escape names",async()=>{
 api.membersList.mockResolvedValue({members:[{id:0,name:'Raphael',avatar:''},{id:1,name:'<img src=x onerror=bad()>',avatar:''}],groups:[]});
 api.roomMessages.mockResolvedValue({messages:[{id:1,member_id:0,text:'Agreed',reply_reference:{message_id:8,author_kind:'member',author_member_id:1},reply_to_member:1,kind:'message',created:1,status:'done'},{id:2,member_id:1,text:'Thanks',reply_reference:{message_id:9,author_kind:'member',author_member_id:0},reply_to_member:0,created:2,status:'done'}]});
 await pane();const chips=[...document.querySelectorAll('.au-room-reply')];
 expect(chips.map(el=>el.textContent)).toEqual(['Reply to <img src=x onerror=bad()>','Reply to Raphael']);
 expect(document.querySelector('.au-room-reply img')).toBeNull();
});

test("multiple queued replies show one speaking row which disappears when completed",async()=>{
 const replies=[{id:1,member_id:1,text:'',created:1,status:'running'},{id:2,member_id:0,text:'',created:2,status:'running'}];
 api.roomMessages.mockResolvedValue({messages:replies});await pane();
 expect(document.querySelectorAll('.au-room-thinking')).toHaveLength(1);
 expect(document.querySelector('.au-room-thinking')!.textContent).toBe('Alice is speaking…');
 expect(document.querySelector('.au-room-thinking .av')).not.toBeNull();
 expect(document.querySelectorAll('.au-room-message')).toHaveLength(0);
 api.roomMessages.mockResolvedValue({messages:replies.map(m=>({...m,status:'done',text:'Finished'}))});
 event({type:'RoomsChanged',room_id:1});await flush();
 expect(document.querySelector('.au-room-thinking')).toBeNull();
 expect(document.querySelectorAll('.au-room-message')).toHaveLength(2);
});

test("new room saves a selected default responder and resets it when that member leaves",async()=>{
 await openNewGroupChat();document.querySelector<HTMLInputElement>('#room-name')!.value='Plans';document.querySelector('#room-name')!.dispatchEvent(new Event('input'));
 for(const id of [0,1])document.querySelector<HTMLElement>(`[data-dlg="member"][data-member="${id}"]`)!.click();
 document.querySelector<HTMLElement>('[data-dlg="responder"]')!.click();
 document.querySelector<HTMLElement>('#app-popover [data-value="member:1"]')!.click();
 document.querySelector<HTMLElement>('[data-dlg="create"]')!.click();await flush();
 expect(api.roomsCreate).toHaveBeenCalledWith({name:'Plans',member_ids:[0,1],group_id:null,default_responder:'member:1',request_id:expect.any(String)});
 await openNewGroupChat();
 for(const id of [0,1])document.querySelector<HTMLElement>(`[data-dlg="member"][data-member="${id}"]`)!.click();
 document.querySelector<HTMLElement>('[data-dlg="responder"]')!.click();document.querySelector<HTMLElement>('#app-popover [data-value="member:1"]')!.click();
 document.querySelector<HTMLElement>('[data-dlg="member"][data-member="1"]')!.click();
 expect(document.querySelector('[data-dlg="responder"]')!.textContent).toContain('Automatic');
});

test("reply controls target a member message and can be cancelled",async()=>{
 api.roomMessages.mockResolvedValue({messages:[{id:12,member_id:1,text:'Ready',created:1,status:'done'}]});await pane();
 document.querySelector<HTMLElement>('[data-room-act="reply"]')!.click();
 expect(document.querySelector('.cmp-quote')!.textContent).toContain('Reply to Alice');
 draft('More details');document.querySelector<HTMLElement>('.cmp-send')!.click();await flush();
 expect(api.roomSend.mock.calls[0][5]).toBe(12);
 document.querySelector<HTMLElement>('[data-room-act="reply"]')!.click();document.querySelector<HTMLElement>('[data-room-act="cancel-reply"]')!.click();
 expect((document.querySelector('.cmp-quote') as HTMLElement).hidden).toBe(true);
});

test("active exchanges expose stop and Raphael's distinct wrap-up card",async()=>{
 api.roomStop.mockResolvedValue({stopped:true});
 api.roomMessages.mockResolvedValue({messages:[{id:12,member_id:0,text:'### Conclusion\nAlice owns testing',kind:'wrapup',created:1,status:'done'}],exchange:{state:'running',stop_requested:0}});
 await pane();expect(document.querySelector('.au-room-wrapup')!.textContent).toContain('Wrap-up');
 expect(document.querySelector('.au-room-wrapup h3')!.textContent).toBe('Conclusion');
 expect(document.querySelector('.au-room-crown')!.getAttribute('aria-label')).toBe('Chief of staff');
 document.querySelector<HTMLElement>('[data-room-act="stop"]')!.click();await flush();expect(api.roomStop).toHaveBeenCalledWith(1);
 api.roomMessages.mockResolvedValue({messages:[],exchange:{state:'stopped',stop_requested:1}});event({type:'RoomsChanged',room_id:1});await flush();
 expect(document.querySelector('[data-room-act="stop"]')).toBeNull();
});

test("wrap-up renders three labelled sections and escaped owners without Markdown markers",async()=>{
 setLocale('zh-TW');
 api.membersList.mockResolvedValue({members:[{id:0,name:'拉斐爾',avatar:''},{id:1,name:'小晴',avatar:''}],groups:[]});
 api.roomMessages.mockResolvedValue({messages:[{id:12,member_id:0,kind:'wrapup',created:1,status:'done',text:'### 結論\r\n先測試。\r\n\r\n### 待決定\r\n今天或明天？\r\n\r\n### 下一步\r\n- 小晴：完成測試。\r\n- **拉斐爾**：確認安排。\r\n- <script>bad()</script>'}]});
 await pane();
 const card=document.querySelector('.au-room-wrapup')!;
 expect([...card.querySelectorAll('h3')].map(node=>node.textContent)).toEqual(['結論','待你決定','下一步']);
 expect([...card.querySelectorAll('.au-room-wrapup-owner')].map(node=>node.textContent)).toEqual(['小晴','拉斐爾']);
 expect(card.textContent).not.toMatch(/###|\*\*/);
 expect(card.textContent).toContain('<script>bad()</script>');
 expect(card.querySelector('script')).toBeNull();
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
