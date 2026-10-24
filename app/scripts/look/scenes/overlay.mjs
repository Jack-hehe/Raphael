import { scene, check } from "./steps.mjs";

// Exercise the production overlay.html and native event contract; no alternate renderer.
export default ["transcribing", "cleaning"].map(stage => ({
  ...scene(`overlay-${stage}`, `Overlay ${stage} percentage caption`, [{ action: "advance-time", ms: 500 }, check("overlay-percentage")], {
    overlayEvents: [
      { type: "settings", px: 96, subtitles: "all", language: "zh-TW" },
      { type: "show", mode: "listening" },
      { type: "mode", mode: "thinking" },
      { type: "progress", stage, done: 1, total: 2, audio_seconds: 4 },
    ],
  }),
  entry: "overlay.html",
}));
