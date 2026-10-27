import { scene, check } from "./steps.mjs";

// The answer the owner saw run out of the top of the corner on 2026-10-27, with an address in it.
const LONG = "1. 建立帳號並登入控制台。 2. 若設定只要求金鑰，前往「Settings → Project Settings → API Keys」取得。" +
  "[官方說明](https://docs.composio.dev/reference/authenticating-to-composio) 3. 將金鑰填入設定頁，再回到 Gmail 連結卡完成 Google 授權。" +
  "金鑰請只填入專用欄位，不要貼到聊天中。";

// Exercise the production overlay.html and native event contract; no alternate renderer.
export default [{
  ...scene("overlay-long-answer", "A long voiced answer with an address: the corner tells it a page of about two lines at a time, with no address, inside its window", [
    { action: "advance-time", ms: 1200 }, check("overlay-pages"),
  ], { overlayEvents: [
    { type: "settings", px: 96, subtitles: "on", language: "zh-TW" },
    { type: "say", prefix: "答", text: LONG, hold: true },
  ] }), entry: "overlay.html", ready: ".subs.on .sub-line",
}, {
  ...scene("overlay-step", "Quick question latest public step in the existing subtitle position", [
    { action: "advance-time", ms: 1200 }, check("overlay-step"),
  ], { overlayEvents: [
    { type: "settings", px: 96, subtitles: "on", language: "zh-TW" },
    { type: "show", mode: "listening" },
    { type: "mode", mode: "thinking", quiet: true },
    { type: "step", text: "《告。正在查看今天下午的行程。》", language: "zh-TW" },
  ] }), entry: "overlay.html",
}, ...["transcribing", "cleaning"].map(stage => ({
  ...scene(`overlay-${stage}`, `Overlay ${stage} percentage caption`, [{ action: "advance-time", ms: 500 }, check("overlay-percentage")], {
    overlayEvents: [
      { type: "settings", px: 96, subtitles: "all", language: "zh-TW" },
      { type: "show", mode: "listening" },
      { type: "mode", mode: "thinking" },
      { type: "progress", stage, done: 1, total: 2, audio_seconds: 4 },
    ],
  }),
  entry: "overlay.html",
}))];
