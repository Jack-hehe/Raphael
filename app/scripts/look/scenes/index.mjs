import chat from "./chat.mjs";
import automations from "./automations.mjs";
import settings from "./settings.mjs";
import overlay from "./overlay.mjs";
import home from "./home.mjs";

export const scenes = [...home, ...chat, ...automations, ...settings, ...overlay];
if (new Set(scenes.map(scene => scene.name)).size !== scenes.length) throw new Error("Duplicate look scene name");
