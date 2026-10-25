// @vitest-environment happy-dom
import { beforeEach, expect, test, vi } from "vitest";
const api=vi.hoisted(()=>({roomTranscribe:vi.fn(),core:vi.fn()}));
vi.mock("./api",()=>api);
import { createChatComposer, wavBytes } from "./chat-composer";
import { setLocale } from "./i18n";
const flush=async()=>{for(let i=0;i<30;i++)await Promise.resolve();};
beforeEach(()=>{document.body.innerHTML="";vi.clearAllMocks();setLocale("en");api.core.mockImplementation(async method => method === "settings.get" ? { settings: { action_mode: "ask" } } : {});});
function mount(send=vi.fn().mockResolvedValue(undefined)) {const view=createChatComposer({placeholder:()=>"Room message",send});document.body.append(view.el);return {view,send};}
test("failed delivery retains a draft and Enter retries; IME Enter never submits",async()=>{
 const {view,send}=mount(vi.fn().mockRejectedValueOnce(new Error("offline")).mockResolvedValue(undefined));view.setDraft("Hello");
 view.input.dispatchEvent(new KeyboardEvent("keydown",{key:"Enter",isComposing:true,bubbles:true}));expect(send).not.toHaveBeenCalled();
 view.input.dispatchEvent(new KeyboardEvent("keydown",{key:"Enter",bubbles:true}));await flush();expect(view.input.value).toBe("Hello");
 view.input.dispatchEvent(new KeyboardEvent("keydown",{key:"Enter",bubbles:true}));await flush();expect(send).toHaveBeenCalledTimes(2);expect(view.input.value).toBe("");
});
test("switching rooms while a send completes preserves the new room draft",async()=>{
 let complete!:()=>void;const {view}=mount(vi.fn(()=>new Promise<void>(resolve=>{complete=resolve;})));view.setDraft("Old room");view.el.requestSubmit();view.setDraft("New room");complete();await flush();expect(view.input.value).toBe("New room");
});
test("failed delivery preserves voice correction evidence for a retry, and learns only after delivery",async()=>{
 const {view}=mount(vi.fn().mockRejectedValueOnce(new Error("offline")).mockResolvedValue(undefined));
 view.setDraft("Please use Open Mouse Bot");view.seedDictation("Please use Open Mouse Bot");
 view.input.value="Please use OpenMausBot";view.input.dispatchEvent(new Event("input"));
 view.el.requestSubmit();await flush();expect(api.core).not.toHaveBeenCalledWith("dictionary.learn",expect.anything());
 api.core.mockResolvedValue({learned:[]});view.el.requestSubmit();await flush();
 expect(api.core).toHaveBeenCalledWith("dictionary.learn",{corrections:[{word:"OpenMausBot",misheard:"Open Mouse Bot"}]});
});
test("composer buttons do not also trigger an enclosing member composer",()=>{
 const outer=document.createElement("section"),handler=vi.fn();outer.addEventListener("click",handler);const {view}=mount();outer.append(view.el);document.body.append(outer);view.el.querySelector<HTMLButtonElement>(".cmp-attach")!.click();expect(handler).not.toHaveBeenCalled();
});

test("a pending learning response does not keep a delivered composer busy",async()=>{
 api.core.mockImplementation(()=>new Promise(()=>{}));const {view,send}=mount();
 view.setDraft("Please use Open Mouse Bot");view.seedDictation("Please use Open Mouse Bot");
 view.input.value="Please use OpenMausBot";view.input.dispatchEvent(new Event("input"));view.el.requestSubmit();await flush();
 expect(send).toHaveBeenCalledOnce();expect(view.input.value).toBe("");
 view.input.value="Next message";view.input.dispatchEvent(new Event("input"));
 expect(view.el.querySelector<HTMLButtonElement>(".cmp-send")!.disabled).toBe(false);
});
test("attachment-only delivery uses the shared composer and keeps the file on failure",async()=>{
 const {view,send}=mount(vi.fn().mockRejectedValueOnce(new Error("offline")).mockResolvedValue(undefined));const picker=view.el.querySelector<HTMLInputElement>(".cmp-picker")!;
 Object.defineProperty(picker,"files",{value:[{name:"notes.txt",type:"text/plain",size:5,arrayBuffer:async()=>new TextEncoder().encode("notes").buffer}]});picker.dispatchEvent(new Event("change"));await flush();
 expect(view.el.querySelector<HTMLButtonElement>(".cmp-send")!.disabled).toBe(false);view.el.requestSubmit();await flush();expect(view.el.querySelector(".cmp-file")!.textContent).toContain("notes.txt");
 view.el.requestSubmit();await flush();expect(send).toHaveBeenLastCalledWith("",[{name:"notes.txt",mime:"text/plain",data:btoa("notes")}]);expect(view.el.querySelector<HTMLElement>(".cmp-file")!.hidden).toBe(true);
});

test("shared composer plus menu preserves attachment picking and the global action mode submenu", async () => {
 const {view}=mount();
 const picker=view.el.querySelector<HTMLInputElement>(".cmp-picker")!,pick=vi.spyOn(picker,"click");
 view.el.querySelector<HTMLButtonElement>(".cmp-attach")!.click();await flush();
 expect(document.querySelector("#app-popover .menu-sep")).toBeNull();
 document.querySelector<HTMLButtonElement>('#app-popover [data-action="1"]')!.click();
 expect(document.querySelector("#app-popover")).not.toBeNull();
 document.querySelector<HTMLButtonElement>('#app-submenu [data-value="direct"]')!.click();await flush();
 expect(api.core).toHaveBeenCalledWith("settings.set",{key:"action_mode",value:"direct"});
 view.el.querySelector<HTMLButtonElement>(".cmp-attach")!.click();await flush();
 document.querySelector<HTMLButtonElement>('#app-popover [data-action="0"]')!.click();
 expect(pick).toHaveBeenCalledOnce();
});
test("voice records until manual Escape and learns an edited transcript only on send",async()=>{
 const stopTrack=vi.fn(),stop=vi.fn();let recorder:any;Object.defineProperty(navigator,"mediaDevices",{configurable:true,value:{getUserMedia:vi.fn().mockResolvedValue({getTracks:()=>[{stop:stopTrack}]})}});
 vi.stubGlobal("MediaRecorder",class {state="inactive";onstop?:()=>void;ondataavailable?:(e:any)=>void;constructor(){recorder=this;}start(){this.state="recording";}stop(){stop();this.state="inactive";this.onstop?.();}});
 vi.stubGlobal("AudioContext",class {decodeAudioData=async()=>({numberOfChannels:1,sampleRate:16000,getChannelData:()=>new Float32Array([0,0.5])});close=async()=>{};});
 api.roomTranscribe.mockResolvedValue({text:"Please use Open Mouse Bot"});const {view,send}=mount();view.el.querySelector<HTMLButtonElement>(".cmp-voice")!.click();await flush();expect(recorder.state).toBe("recording");expect(stop).not.toHaveBeenCalled();
 expect(view.el.querySelector<HTMLButtonElement>(".cmp-send")!.disabled).toBe(true);document.dispatchEvent(new KeyboardEvent("keydown",{key:"Escape",bubbles:true}));await flush();expect(stop).toHaveBeenCalledOnce();expect(stopTrack).toHaveBeenCalledOnce();expect(view.input.value).toBe("Please use Open Mouse Bot");expect(send).not.toHaveBeenCalled();expect(api.roomTranscribe).toHaveBeenCalledOnce();
 api.core.mockResolvedValue({learned:["OpenMausBot"]});view.input.value="Please use OpenMausBot";view.input.dispatchEvent(new Event("input"));view.el.requestSubmit();await flush();expect(api.core).toHaveBeenCalledWith("dictionary.learn",{corrections:[{word:"OpenMausBot",misheard:"Open Mouse Bot"}]});vi.unstubAllGlobals();
});
test("WAV encoding produces mono PCM with correct sample rate and clipping",()=>{
 const bytes=wavBytes([new Float32Array([-2,0,2])],16000),view=new DataView(bytes.buffer);expect(new TextDecoder().decode(bytes.slice(0,4))).toBe("RIFF");expect(view.getUint32(24,true)).toBe(16000);expect(view.getUint16(22,true)).toBe(1);expect(view.getInt16(44,true)).toBe(-32768);expect(view.getInt16(48,true)).toBe(32767);
});
