import chat from "./chat.mjs";
import automations from "./automations.mjs";
import settings from "./settings.mjs";

export const scenes = [...chat, ...automations, ...settings];
if (new Set(scenes.map(scene => scene.name)).size !== scenes.length) throw new Error("Duplicate look scene name");
