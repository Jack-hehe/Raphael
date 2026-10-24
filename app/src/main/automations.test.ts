// @vitest-environment happy-dom
import { beforeEach, expect, test, vi } from "vitest";
import type { AppContext, Member, Routine } from "./types";
const api = vi.hoisted(() => ({ membersList:vi.fn(), routinesList:vi.fn(), routineRuns:vi.fn(), routinesCreate:vi.fn(), routinesUpdate:vi.fn(), routinesDelete:vi.fn(), keepAwakeGet:vi.fn(), keepAwakeSet:vi.fn(), roomsList:vi.fn(), settingsGet:vi.fn(), groupsCreate:vi.fn(), groupsUpdate:vi.fn(), groupsDelete:vi.fn(), membersUpdate:vi.fn(), roomsCreate:vi.fn(), roomSend:vi.fn(), roomMessages:vi.fn() }));
vi.mock("./api",()=>api);
import { openRoutineDialog } from "./routine-dialog";
import { occurrences } from "./automation-calendar";
import { createAutomationsPage } from "./pages/automations";
import { createTeamPage } from "./pages/team";
import { dateKey, localDay, shiftDay } from "./schedule-picker";
import { setLocale } from "./i18n";
import { closeMenu } from "./ui";
const person = (id:number,name:string):Member => ({id,name,role:"Research",instructions:"",avatar:"",group_id:id?1:null,model:"",tools:null,position:id,created:0,unread:false,waiting:false,preview:"",last_at:null});
const members = [person(0,"Raphael"),person(1,"Alice"),person(2,"Bob")];
const plan:Routine = {id:1,member_id:1,title:"Review",prompt:"Check progress",schedule:{kind:"daily",time:"09:00"},enabled:true,last_run:null,next_run:new Date("2026-10-24T09:00").getTime()};
const flush = async ():Promise<void>=>{for(let i=0;i<16;i++)await Promise.resolve();};
const click=(selector:string):void=>{document.querySelector<HTMLElement>(selector)!.click();};
const field=(selector:string,value:string):void=>{document.querySelector<HTMLInputElement>(selector)!.value=value;};
let events: ((e:any)=>void)[];
const app=():AppContext=>({onEvent:(fn:any)=>{events.push(fn);return ()=>{};},go:vi.fn()} as unknown as AppContext);
beforeEach(()=>{
  closeMenu(); document.body.innerHTML="";vi.clearAllMocks();setLocale("en");events=[];
  api.membersList.mockResolvedValue({members,groups:[{id:1,name:"Work",position:0,sources:[]}]});
  api.routinesList.mockResolvedValue({routines:[plan]});api.routineRuns.mockResolvedValue({runs:[]});
  api.routinesCreate.mockResolvedValue({routine:plan});api.routinesUpdate.mockResolvedValue({routine:plan});api.routinesDelete.mockResolvedValue({deleted:true});
  api.keepAwakeGet.mockResolvedValue(false);api.keepAwakeSet.mockResolvedValue(true);api.roomsList.mockResolvedValue({rooms:[]});api.settingsGet.mockResolvedValue({settings:{agent_model:"openai/shared-model"}});
});

test("routine editor saves the chosen weekdays, owner, date and end count",async()=>{
  openRoutineDialog({members,memberId:2,date:"2030-10-24",time:"09:00"});
  field("#au-title","Morning report");field("#au-prompt","Read progress");
  click('[data-kind="weekly"]');click('[data-day="1"]');click('[data-day="3"]');click('[data-dlg="more"]');click('[data-dlg="end"]');click('#app-popover [data-value="after"]');field("#au-count","5");
  expect(document.querySelector<HTMLInputElement>("#au-title")!.value).toBe("Morning report");
  click('[data-dlg="save"]');await flush();
  expect(api.routinesCreate).toHaveBeenCalledWith(expect.objectContaining({member_id:2,title:"Morning report",prompt:"Read progress",schedule:expect.objectContaining({kind:"weekly",max_runs:5,start_date:"2030-10-24",time:"09:00"})}));
  expect(document.querySelector('[role="dialog"]')).toBeNull();
});

test("routine editing preserves an until date and never changes the member",async()=>{
  openRoutineDialog({members,routine:{...plan,schedule:{kind:"weekdays",time:"08:30",start_date:"2030-01-01",until:"2030-12-31"}}});
  expect(document.querySelector<HTMLButtonElement>('[data-dlg="member"]')!.disabled).toBe(true);
  click('[data-dlg="save"]');await flush();
  expect(api.routinesUpdate).toHaveBeenCalledWith(1,expect.objectContaining({schedule:{kind:"weekdays",time:"08:30",start_date:"2030-01-01",until:"2030-12-31"}}));
});

test.each(["25:00","09:61","oops"])("routine editor refuses invalid time %s",async time=>{
  openRoutineDialog({members,date:"2030-10-24",time});field("#au-title","Review");field("#au-prompt","Check");click('[data-dlg="save"]');await flush();expect(api.routinesCreate).not.toHaveBeenCalled();
});

test("date picker has real day buttons and leaves the form draft intact",()=>{
  openRoutineDialog({members,date:"2030-10-24",time:"09:00"});field("#au-title","Draft");click('[data-dlg="date"]');click('[data-date="2030-10-25"]');expect(document.querySelector<HTMLInputElement>("#au-title")!.value).toBe("Draft");expect(document.querySelector('[data-dlg="date"]')!.textContent).toBe("2030-10-25");
});

test("Escape closes a menu first and the editor second",()=>{
  openRoutineDialog({members,date:"2030-10-24"});click('[data-dlg="more"]');click('[data-dlg="end"]');document.dispatchEvent(new KeyboardEvent("keydown",{key:"Escape",bubbles:true}));expect(document.querySelector('.au-routine-dialog')).not.toBeNull();document.dispatchEvent(new KeyboardEvent("keydown",{key:"Escape",bubbles:true}));expect(document.querySelector('.au-routine-dialog')).toBeNull();
});

test("list explains routines, links results and persists switches without a run log",async()=>{
  const page=createAutomationsPage(app());document.body.append(page.el);page.show();await flush();
  expect(page.el.textContent).toContain("Review");click('[data-au="toggle"]');await flush();expect(api.routinesUpdate).toHaveBeenCalledWith(1,{enabled:false});
  click('[data-au="awake"]');await flush();expect(api.keepAwakeSet).toHaveBeenCalledWith(true);expect(page.el.querySelector('[data-au="awake"]')!.getAttribute("aria-checked")).toBe("true");
  expect(page.el.querySelector('[data-au="runs"]')).toBeNull();
  expect(page.el.textContent).toContain("automatically");
  expect(page.el.querySelector('.au-sentence')!.textContent).toContain("Alice");
  expect(page.el.querySelector('.au-sentence')!.textContent).toContain("Review");
  click('[data-au="destination"]');expect(document.querySelector('.au-runs-dialog')).toBeNull();page.hide();
});

test("live routine notifications refresh the list, and hide invalidates pending responses",async()=>{
  const page=createAutomationsPage(app());document.body.append(page.el);page.show();await flush();api.routinesList.mockResolvedValue({routines:[{...plan,title:"Changed"}]});events.forEach(fn=>fn({type:"RoutinesChanged",member_id:1}));await flush();expect(page.el.textContent).toContain("Changed");page.hide();
});

test("calendar switches between day and week and mini dates navigate",async()=>{
  const page=createAutomationsPage(app());document.body.append(page.el);page.show();await flush();click('[data-view="calendar"]');expect(page.el.querySelectorAll('.au-day-col')).toHaveLength(7);click('[data-period="day"]');expect(page.el.querySelectorAll('.au-day-col')).toHaveLength(1);click('[data-mini-date]');expect(page.el.querySelectorAll('.au-day-col')).toHaveLength(1);page.hide();
});

test("calendar chips keep complete compact times and full task details when edited",async()=>{
  const page=createAutomationsPage(app());document.body.append(page.el);page.show();await flush();click('[data-view="calendar"]');
  const chip=page.el.querySelector<HTMLButtonElement>('.au-occurrence')!;
  expect(chip.querySelector('time')?.textContent).toBe("09:00");
  expect(chip.title).toContain("Review");expect(chip.title).toContain("09:00");
  expect(chip.getAttribute("aria-label")).toContain("Alice");
  chip.click();expect(document.querySelector<HTMLInputElement>("#au-title")!.value).toBe("Review");
  expect(document.querySelector<HTMLInputElement>("#au-time")!.value).toBe("09:00");page.hide();
});

test("team cards share the global model and a group menu can rename",async()=>{
  const page=createTeamPage(app());document.body.append(page.el);page.show();await flush();expect(page.el.querySelectorAll('.team-member')).toHaveLength(3);expect(page.el.querySelectorAll('.team-model')).toHaveLength(3);expect(page.el.textContent).toContain("shared-model");click('[data-team="group-menu"]');expect(document.querySelector('#app-popover')!.textContent).toContain("Rename");page.hide();
});

test.each([
  [{kind:"once",time:"09:00",start_date:"2026-10-24"},1],
  [{kind:"weekdays",time:"09:00"},5],
  [{kind:"weekly",time:"09:00",days:[1,3,5]},3],
  [{kind:"daily",time:"09:00",until:"2026-10-26"},3],
])("calendar respects local dates and recurrence %j",(schedule,expected)=>{
 const routine={...plan,schedule: schedule as Routine["schedule"]};expect(occurrences(routine,localDay("2026-10-24"),localDay("2026-10-31"))).toHaveLength(expected);
});
test("interval phases and remaining run limits survive calendar boundaries",()=>{
 const routine={...plan,schedule:{kind:"every" as const,hours:3}};const from=localDay("2026-10-24");expect(occurrences(routine,from,shiftDay(from,1),2).map(o=>new Date(o.at).getHours())).toEqual([9,12]);expect(occurrences({...routine,next_run:null},from,shiftDay(from,1))).toEqual([]);expect(dateKey(shiftDay(localDay("2026-12-31"),1))).toBe("2027-01-01");
});

test("calendar accepts member drops with date and hour but ignores unrelated drags",async()=>{
 const page=createAutomationsPage(app());document.body.append(page.el);page.show();await flush();click('[data-view="calendar"]');
 const slot=page.el.querySelector<HTMLElement>('[data-slot][data-hour="9"]')!;
 const drop=(value:string):void=>{const event=new Event("drop",{bubbles:true,cancelable:true});Object.defineProperty(event,"dataTransfer",{value:{getData:()=>value}});slot.dispatchEvent(event);};
 drop("");expect(document.querySelector('.au-routine-dialog')).toBeNull();drop("2");
 expect(document.querySelector('[data-dlg="member"][aria-pressed="true"]')!.textContent).toContain("Bob");expect(document.querySelector('[data-dlg="date"]')!.textContent).toBe(slot.dataset.slot);expect(document.querySelector<HTMLInputElement>('#au-time')!.value).toBe("09:00");page.hide();
});

test("future calendar windows account for occurrences before the visible range",()=>{
 const from=localDay("2026-10-26"),to=localDay("2026-11-02");
 expect(occurrences(plan,from,to,2)).toEqual([]);
 expect(occurrences({...plan,schedule:{kind:"every",hours:3}},from,to,4)).toEqual([]);
 expect(occurrences({...plan,schedule:{kind:"weekdays",time:"09:00"}},from,to,2)).toHaveLength(2);
});

test("team mention autocomplete keeps typing focus and inserts the chosen member",async()=>{
 const page=createTeamPage(app());document.body.append(page.el);page.show();await flush();
 const input=page.el.querySelector<HTMLTextAreaElement>("textarea")!;input.focus();input.value="Review @Al";input.dispatchEvent(new Event("input"));
 expect(document.activeElement).toBe(input);expect(document.querySelector('#app-popover')!.textContent).toContain("@Alice");
 click('#app-popover [data-value="Alice"]');expect(input.value).toBe("Review @Alice ");expect(document.activeElement).toBe(input);page.hide();
});

test("team group creation, rename and deletion submit the confirmed values",async()=>{
 const page=createTeamPage(app());document.body.append(page.el);page.show();await flush();
 click('[data-team="new"]');click('#app-popover [data-pick="group"]');field('#mb-group-name','Research');click('[data-dlg="save"]');await flush();expect(api.groupsCreate).toHaveBeenCalledWith('Research');
 click('[data-team="group-menu"]');click('#app-popover [data-pick="rename"]');field('#mb-group-name','Planning');click('[data-dlg="save"]');await flush();expect(api.groupsUpdate).toHaveBeenCalledWith(1,{name:'Planning'});
 click('[data-team="group-menu"]');click('#app-popover [data-pick="delete"]');click('[data-dlg="delete"]');await flush();expect(api.groupsDelete).toHaveBeenCalledWith(1);page.hide();
});

test("team drops move members, retain the chief and navigate from chat",async()=>{
 const context=app(),page=createTeamPage(context);document.body.append(page.el);page.show();await flush();
 const drop=(id:string):void=>{const event=new Event('drop',{bubbles:true,cancelable:true});Object.defineProperty(event,'dataTransfer',{value:{getData:()=>id}});page.el.querySelector('[data-team-group="none"]')!.dispatchEvent(event);};
 drop('0');expect(api.membersUpdate).not.toHaveBeenCalled();api.membersUpdate.mockResolvedValue({});drop('2');await flush();expect(api.membersUpdate).toHaveBeenCalledWith(2,{group_id:null});
 click('[data-team="chat"][data-member="2"]');expect(context.go).toHaveBeenCalledWith('agent');page.hide();
});


test("status summaries have one separator and history appears in past calendar days",async()=>{
 const at=new Date();at.setHours(9,0,0,0);at.setDate(at.getDate()-2);
 api.routineRuns.mockResolvedValue({runs:[{id:5,routine_id:1,scheduled:at.getTime(),started:at.getTime()+120000,finished:at.getTime()+180000,status:"done",summary:"Completed。．.",cost:0,turn_id:12}]});
 const page=createAutomationsPage(app());document.body.append(page.el);page.show();await flush();
 expect(page.el.querySelector('.au-card p')!.textContent).not.toMatch(/[。．.]\s*·/);
 click('[data-view="calendar"]');click('[data-mini-date="'+dateKey(at)+'"]');click('[data-period="day"]');
 expect(page.el.querySelector('[data-run="5"]')).not.toBeNull();page.hide();
});

test("routine destination uses an app dropdown",()=>{
 openRoutineDialog({members});click('[data-dlg="more"]');click('[data-dlg="destination"]');
 expect(document.querySelector('#app-popover')!.textContent).toContain("Member's main thread");
});

test("empty ungrouped column is available only while dragging",async()=>{
 const page=createTeamPage(app());document.body.append(page.el);page.show();await flush();const target=page.el.querySelector<HTMLElement>('.team-drop-empty')!;expect(target.hidden).toBe(true);
 const card=page.el.querySelector<HTMLElement>('[data-team-member="1"]')!;card.dispatchEvent(new Event('dragstart',{bubbles:true}));expect(target.hidden).toBe(false);card.dispatchEvent(new Event('dragend',{bubbles:true}));expect(target.hidden).toBe(true);page.hide();
});

test("each empty-state example opens a prefilled routine that can be saved",async()=>{
 api.routinesList.mockResolvedValue({routines:[]});
 const page=createAutomationsPage(app());document.body.append(page.el);page.show();await flush();
 expect(page.el.querySelectorAll('[data-au="example"]')).toHaveLength(3);
 for (let i=0;i<3;i++) {
   click(`[data-au="example"][data-example="${i}"]`);
   expect(document.querySelector<HTMLInputElement>('#au-title')!.value.length).toBeGreaterThan(5);
   expect(document.querySelector<HTMLTextAreaElement>('#au-prompt')!.value.length).toBeGreaterThan(5);
   expect(document.querySelector<HTMLInputElement>('#au-time')!.value).toBe(i===2?"21:00":"08:00");
   expect(document.querySelector('[data-kind="'+(i===1?'weekly':'daily')+'"][aria-pressed="true"]')).not.toBeNull();
   click('[data-dlg="save"]');await flush();
 }
 expect(api.routinesCreate).toHaveBeenCalledTimes(3);
 expect(api.routinesCreate.mock.calls[1][0].schedule.days).toEqual([1]);page.hide();
});
