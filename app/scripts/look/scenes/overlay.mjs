import { scene, check } from "./steps.mjs";

// Exercise the production overlay.html and native event contract; no alternate renderer.
export default [{
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
