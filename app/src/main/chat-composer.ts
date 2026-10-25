// The same composer shell in member conversations, rooms and the team board. Member conversations
// own their existing hotkey/attachment lifecycle; room composers keep their draft until delivery.
import * as api from "./api";
import { bindComposerLearning, learnComposerCorrections } from "./composer-learning";
import { avatarHTML } from "./avatar";
import { describeError } from "./format";
import { getLocale, onLocaleChange, t } from "./i18n";
import type { WireAttachment } from "./types";
import { closeMenu, esc, ico, iconButton, openMenu, sendGlyph, toaster } from "./ui";
import "./agent.css";

export function composerHTML(): string {
  return `<div class="cmp-quote" hidden></div><div class="cmp-file" hidden></div><div class="cmp-row">` +
    `<button type="button" class="btn cmp-attach" data-act="attach" aria-haspopup="menu" aria-expanded="false">${ico("plus")}</button>` +
    `<textarea class="cmp-input" rows="1" autocomplete="off" enterkeyhint="send"></textarea>` +
    `<span class="cmp-pair">` +
    iconButton("send", t("chat.send"), " disabled", "cmp-send").replace('type="button"', 'type="submit"').replace(" title=", " data-tooltip=").replace(ico("send"), sendGlyph()) +
    `<button type="button" class="btn cmp-voice" data-act="voice"></button></span></div><input class="cmp-picker" type="file" multiple hidden>`;
}

/** The same attachment and global action policy menu for member, room and team composers. */
export async function openComposerMenu(anchor: HTMLElement, attach: () => void): Promise<void> {
  let mode: "ask" | "direct" = "direct";
  try {
    const result = await api.core<{ settings: { action_mode?: "ask" | "direct" } }>("settings.get");
    mode = result?.settings?.action_mode ?? "direct";
  } catch { /* A reconnecting core keeps the menu available; saving still requires the core. */ }
  if (!anchor.isConnected) return;
  openMenu(anchor, [], "", () => {}, undefined, [
    { label: t("agent.attach"), icon: "file", run() { closeMenu(); attach(); } },
    { label: t("chat.beforeAct"), desc: t(mode === "ask" ? "chat.askFirst" : "chat.direct"), icon: "shield", submenu: true, run(row) {
      openMenu(row, [{ value: "ask", label: t("chat.askFirst") }, { value: "direct", label: t("chat.direct") }], mode, value => {
        void api.core("settings.set", { key: "action_mode", value }).catch(error => toaster.info(describeError(error)));
      }, t("chat.beforeAct"), [], { placement: "beside" });
    } },
  ]);
}

export function wavBytes(channels: readonly Float32Array[], rate: number): Uint8Array {
  const length = channels[0]?.length ?? 0, bytes = new Uint8Array(44 + length * 2), view = new DataView(bytes.buffer);
  const text = (at: number, value: string): void => { for (let i=0;i<value.length;i++) bytes[at+i]=value.charCodeAt(i); };
  text(0,"RIFF"); view.setUint32(4,bytes.length-8,true); text(8,"WAVEfmt ");view.setUint32(16,16,true);view.setUint16(20,1,true);view.setUint16(22,1,true);view.setUint32(24,rate,true);view.setUint32(28,rate*2,true);view.setUint16(32,2,true);view.setUint16(34,16,true);text(36,"data");view.setUint32(40,length*2,true);
  for(let i=0;i<length;i++){const sample=Math.max(-1,Math.min(1,channels.reduce((sum,c)=>sum+c[i],0)/channels.length));view.setInt16(44+i*2,sample*(sample<0?32768:32767),true);}
  return bytes;
}
const base64 = (bytes: Uint8Array): string => { let value="";for(let i=0;i<bytes.length;i+=8192)value+=String.fromCharCode(...bytes.subarray(i,i+8192));return btoa(value); };

export function createChatComposer(options: { placeholder(): string; transcriptTarget?(): (text: string) => void; send(text: string, files: WireAttachment[]): Promise<void> }) {
  const el=document.createElement("form");el.className="composer";el.noValidate=true;el.innerHTML=composerHTML();
  const input=el.querySelector<HTMLTextAreaElement>("textarea")!,send=el.querySelector<HTMLButtonElement>(".cmp-send")!,voice=el.querySelector<HTMLButtonElement>(".cmp-voice")!,picker=el.querySelector<HTMLInputElement>(".cmp-picker")!;
  const learning=bindComposerLearning(input);
  let revision=0;
  el.addEventListener("click",e=>e.stopPropagation());
  let files:WireAttachment[]=[],busy=false,attaching=false,recording=false,starting=false,stream:MediaStream|undefined,recorder:MediaRecorder|undefined;
  const draw=():void=>{input.placeholder=options.placeholder();input.setAttribute("aria-label",input.placeholder);send.disabled=busy||attaching||recording||starting||(!input.value.trim()&&!files.length);voice.innerHTML=avatarHTML({size:32,avatar:null,loop:recording});voice.setAttribute("aria-label",t(recording?"rooms.stopVoice":"agent.voice"));voice.setAttribute("aria-pressed",String(recording));voice.disabled=starting||busy;el.querySelector(".cmp-attach")!.setAttribute("aria-label",t("agent.attach"));const chips=el.querySelector<HTMLElement>(".cmp-file")!;chips.hidden=!files.length;chips.innerHTML=files.map((f,i)=>`<span class="cmp-chip"><span>${esc(f.name)}</span>${iconButton("x",t("common.cancel"),` data-file="${i}"`)}</span>`).join("");};
  const fit=():void=>{input.style.height="auto";input.style.height=`${Math.min(180,input.scrollHeight)}px`;draw();};
  el.addEventListener("submit",e=>{e.preventDefault();if(send.disabled)return;const text=input.value,snapshot=files,submitted=revision,dictation=learning.snapshot(),corrections=learning.take(text);busy=true;draw();void options.send(text.trim(),snapshot).then(()=>{void learnComposerCorrections(corrections);if(submitted===revision){if(input.value===text)input.value="";if(files===snapshot)files=[];}}).catch(error=>{if(submitted===revision&&!learning.snapshot())learning.restore(dictation);toaster.info(describeError(error));}).finally(()=>{busy=false;fit();input.focus();});});
  input.addEventListener("input",fit);input.addEventListener("keydown",e=>{if(e.key==="Enter"&&!e.shiftKey&&!e.isComposing&&e.keyCode!==229){e.preventDefault();el.requestSubmit();}});
  el.querySelector<HTMLElement>(".cmp-attach")!.addEventListener("click",event=>{void openComposerMenu(event.currentTarget as HTMLElement,()=>{picker.value="";picker.click();});});
  el.querySelector(".cmp-file")!.addEventListener("click",e=>{const button=(e.target as Element).closest<HTMLElement>("[data-file]");if(button){files=files.filter((_,i)=>i!==Number(button.dataset.file));draw();}});
  picker.addEventListener("change",()=>{attaching=true;draw();void(async()=>{const picked=[...picker.files??[]];if(files.length+picked.length>4||picked.some(f=>f.size>50*1024*1024)||picked.reduce((n,f)=>n+f.size,files.reduce((n,f)=>n+f.data.length*0.75,0))>100*1024*1024)throw new Error(t("rooms.fileLimit"));const added=await Promise.all(picked.map(async f=>({name:f.name,mime:f.type||"application/octet-stream",data:base64(new Uint8Array(await f.arrayBuffer()))})));files=[...files,...added];})().catch(error=>toaster.info(describeError(error))).finally(()=>{attaching=false;draw();});});
  const release=():void=>{stream?.getTracks().forEach(track=>track.stop());stream=undefined;recording=false;draw();};
  const stop=():void=>{if(recorder?.state==="recording")recorder.stop();};
  voice.addEventListener("click",()=>{if(recording){stop();return;}if(starting)return;starting=true;const transcript=options.transcriptTarget?.()??((text:string)=>{input.value+=(input.value?" ":"")+text;learning.seed(input.value,text);});draw();void(async()=>{stream=await navigator.mediaDevices.getUserMedia({audio:true});const chunks:Blob[]=[];recorder=new MediaRecorder(stream);recorder.ondataavailable=e=>{if(e.data.size)chunks.push(e.data);};recorder.onerror=()=>{release();toaster.info(t("rooms.voiceFailed"));};recorder.onstop=()=>{release();busy=true;draw();void(async()=>{const audio=new AudioContext();try{const decoded=await audio.decodeAudioData(await new Blob(chunks).arrayBuffer());const data=wavBytes(Array.from({length:decoded.numberOfChannels},(_,i)=>decoded.getChannelData(i)),decoded.sampleRate);const result=await api.roomTranscribe(base64(data), getLocale());transcript(result.text);fit();}finally{await audio.close();}})().catch(error=>toaster.info(describeError(error))).finally(()=>{busy=false;draw();});};recorder.start(1000);recording=true;})().catch(error=>{release();toaster.info(describeError(error));}).finally(()=>{starting=false;draw();});});
  document.addEventListener("keydown",e=>{if(recording&&e.key==="Escape"){e.preventDefault();e.stopImmediatePropagation();stop();}},true);
  onLocaleChange(draw);draw();
  return {el,input,draw,stopVoice:stop,seedDictation(text:string){learning.seed(input.value,text);},setDraft(text:string){learning.clear();revision++;input.value=text;files=[];fit();}};
}
