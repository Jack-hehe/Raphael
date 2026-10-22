// The main window's way to the rest of the app: calls to the core through `core_call`, and the
// commands of commands.rs. Every function rejects with the reason as a string (`describeError`
// makes it fit to show).

import { Channel, invoke } from "@tauri-apps/api/core";
import { withWindowHotkeyPause } from "./window-hotkeys";
import type { LanguageGroup } from "./languages";
import type { TelegramStatus } from "./messaging";
import type { AvatarCrop } from "./avatar";
import type {
  AgentQuestion,
  AgentTurn,
  AuditEntry,
  ConnectResult,
  ConnectionGroup,
  NewConnection,
  StoreService,
  AudioDevices,
  BrainSettings,
  DeviceSettings,
  DictionaryEntry,
  HistoryItem,
  KeyCheck,
  LocalModels,
  MainEvent,
  MicAccess,
  ModelCatalog,
  ModelsStatus,
  Status,
  StatsSummary,
} from "./types";

/** Calls `method` of the core. Only the methods of the main window go through (commands.rs). */
export function core<T>(method: string, params: object = {}): Promise<T> {
  return invoke<T>("core_call", { method, params });
}

// ---------- status ----------

export const statusGet = (): Promise<Status> => core("status.get");

/** Asks OpenRouter again; answers with the `openrouter` part of the status. */
export const statusCheckOpenrouter = (): Promise<Status["openrouter"]> => core("status.check_openrouter");

// ---------- usage and prices ----------

/** The numbers of the home page: words, time, speed, the last 14 days. */
export const statsSummary = (days?: number): Promise<StatsSummary> =>
  core("stats.summary", days === undefined ? {} : { days });
/** Every OpenRouter model Raphael could use, with its price and what each role can make of it. */
export const modelsCatalog = (): Promise<ModelCatalog> => core("models.catalog");

// ---------- history ----------

export interface HistoryQuery {
  /** Only items whose text contains this. */
  query?: string;
  limit?: number;
  /** Only items older than this: the cursor of the next page. */
  before_ms?: number;
  /** Only items from this time on (with `before_ms`, one day: from its midnight to the next). */
  after_ms?: number;
}

export const historyList = (q: HistoryQuery): Promise<{ items: HistoryItem[] }> => core("history.list", q);
export const historyDelete = (id: number): Promise<{ deleted: boolean }> => core("history.delete", { id });
/** Deletes the whole dictation history (Settings › 資料); says how many items went. */
export const historyClear = (): Promise<{ deleted: number }> => core("history.clear");

// ---------- conversations with Raphael ----------

export interface AgentHistoryQuery {
  limit?: number;
  /** Only turns older than this id: the cursor of the next page. */
  before_id?: number;
}

/** The turns of the conversations, newest first. */
export const agentHistoryList = (q: AgentHistoryQuery = {}): Promise<{ turns: AgentTurn[] }> => core("agent.history.list", q);
/** Forgets every turn; says how many there were. */
export const agentHistoryClear = (): Promise<{ deleted: number }> => core("agent.history.clear");
/** Stops the background task `taskId` (the one that runs, or one that waits); says whether there was one to stop. */
export const workerCancel = (taskId: number): Promise<{ cancelled: boolean }> => core("worker.cancel", { task_id: taskId });
/** Runs a task that did not finish again, as a new task; its id. */
export const workerRerun = (taskId: number): Promise<{ task_id: number }> => core("worker.rerun", { task_id: taskId });
/** The turns whose words hold `query`, newest first, over the whole history (a core that has the method). */
export const agentHistorySearch = (query: string): Promise<{ turns: AgentTurn[] }> => core("agent.history.search", { query, limit: 100 });

/**
 * Asks Raphael in writing (the composer of the 拉斐爾 page). The question goes the way a spoken one
 * goes once its words are heard, with the source `app`; this settles when its turn is over (its
 * answer is in the history, or it failed), and rejects when Raphael is busy with another.
 */
export const agentAskText = (text: string): Promise<void> => invoke("agent_ask_text", { text });
/** The YES/NO question that waits for an answer now, if one does. */
export const agentQuestionOpen = (): Promise<AgentQuestion | null> => invoke("agent_question_open");
/** Answers the question `id` from the page, as the overlay's buttons do; the window must have the focus. */
export const agentQuestionAnswer = (id: string, approved: boolean): Promise<void> => invoke("agent_question_answer", { id, approved });
/** The question key pressed (`down`) or let go (`up`) from the page's voice button: the same path as the key itself. */
export const agentVoice = (edge: "down" | "up"): Promise<void> => invoke("window_hotkey", { kind: edge === "down" ? "agent_down" : "agent_up" });

// ---------- Raphael's picture ----------

/** The person's picture for Raphael, as a data URL and its crop; null while he is his emblem. */
export const avatarGet = (): Promise<unknown> => invoke("avatar_get");
/** Opens the file dialog of Windows; the picture chosen, as a data URL for the crop dialog, or null when none was. */
export const avatarPick = (): Promise<string | null> => invoke("avatar_pick");
/** Keeps the picture last picked, cut as `crop` says, as Raphael's picture. */
export const avatarSave = (crop: AvatarCrop): Promise<void> => invoke("avatar_save", { crop });
/** Raphael is his emblem again: the picture is deleted. */
export const avatarClear = (): Promise<void> => invoke("avatar_clear");

// ---------- dictionary ----------

export const dictionaryList = (): Promise<{ entries: DictionaryEntry[] }> => core("dictionary.list");
export const dictionaryAdd = (word: string): Promise<{ entry: DictionaryEntry }> => core("dictionary.add", { word });
export const dictionaryDelete = (id: number): Promise<{ deleted: boolean }> => core("dictionary.delete", { id });
/** Adds the words as typed entries; says how many were new, already there, or blank. */
export const dictionaryImport = (words: string[]): Promise<{ added: number; skipped: number; invalid: number }> =>
  core("dictionary.import", { words });

// ---------- the brain's settings and the key ----------

export const settingsGet = (): Promise<{ settings: BrainSettings }> => core("settings.get");
export const settingsSet = (key: keyof BrainSettings, value: string | number | boolean | string[] | Record<string, string[]>): Promise<{ settings: BrainSettings }> =>
  core("settings.set", { key, value });
/** The languages the speech recognition can listen for, in groups (中文: 台灣、香港、…). */
export const languagesList = (): Promise<{ groups: LanguageGroup[] }> => core("languages.list");
export const secretsStatus = (): Promise<{ has_key: boolean }> => core("secrets.status");
/** Checks the key with OpenRouter and, if it works, stores it. */
export const secretsSetOpenrouterKey = (key: string): Promise<KeyCheck> => core("secrets.set_openrouter_key", { key });
/** Removes the stored key. Nothing comes back: the key never reaches the page. */
export const secretsClearOpenrouterKey = (): Promise<Record<string, never>> => core("secrets.clear_openrouter_key");

// ---------- connections (the plugins and the store) ----------

export const toolsetsList = (): Promise<{ groups: ConnectionGroup[] }> => core("toolsets.list");
export const integrationsCatalog = (): Promise<{ services: StoreService[] }> => core("integrations.catalog");
/** Adds a service of the store, or signs in to a connected one again (`id`). Waits for the login in the browser: minutes. */
export const integrationsConnect = (target: { catalog_id: string } | { id: string }): Promise<ConnectResult> =>
  core("integrations.connect", target);
/** Ends the wait for a login; the call that waits then answers `cancelled`. */
export const integrationsConnectCancel = (): Promise<{ cancelled: boolean }> => core("integrations.connect_cancel");
/** Connects a server of the user's own. The values of its variables and headers go to the vault and never come back. */
export const integrationsAdd = (server: NewConnection): Promise<{ server: { id: string } }> => core("integrations.add", server);
export const integrationsRefresh = (id: string): Promise<Record<string, unknown>> => core("integrations.refresh", { id });
export const integrationsSetEnabled = (id: string, enabled: boolean): Promise<Record<string, unknown>> =>
  core("integrations.set_enabled", { id, enabled });
/** Revokes the login where the server allows it, forgets the secrets and removes the service. */
export const integrationsDisconnect = (id: string): Promise<{ removed: boolean; revoked: boolean }> =>
  core("integrations.disconnect", { id });
export const integrationsSetToolEnabled = (id: string, tool: string, enabled: boolean): Promise<Record<string, unknown>> =>
  core("integrations.set_tool_enabled", { id, tool, enabled });
export const integrationsSetToolsEnabled = (id: string, tools: string[], enabled: boolean): Promise<Record<string, unknown>> =>
  core("integrations.set_tools_enabled", { id, tools, enabled });
export const integrationsAudit = (limit = 8): Promise<{ entries: AuditEntry[] }> => core("integrations.audit", { limit });

// ---------- messaging apps (Telegram) ----------

export const telegramStatus = (): Promise<TelegramStatus> => core("messaging.telegram.status");
/** Checks the bot token with Telegram and, if it works, keeps it in the vault. The token never comes back. */
export const telegramConnect = (
  token: string,
): Promise<{ ok: true; status: TelegramStatus } | { ok: false; reason: "invalid_token" | "network" }> =>
  core("messaging.telegram.connect", { token });
export const telegramSetEnabled = (enabled: boolean): Promise<TelegramStatus> => core("messaging.telegram.set_enabled", { enabled });
/** Allows the person who was given this code. */
export const telegramApprove = (code: string): Promise<{ approved: boolean; status: TelegramStatus }> =>
  core("messaging.telegram.approve", { code });
export const telegramReject = (code: string): Promise<TelegramStatus> => core("messaging.telegram.reject", { code });
export const telegramRemoveUser = (user_id: string): Promise<TelegramStatus> => core("messaging.telegram.remove_user", { user_id });
/** Deletes the token from the vault and forgets who was allowed. */
export const telegramDisconnect = (): Promise<TelegramStatus> => core("messaging.telegram.disconnect");

// ---------- the device side of the core ----------

/** Passive reads keep the settings meter running while automatic detection settles. */
export const audioDevices = (refresh = true): Promise<AudioDevices> => core("audio.devices", { refresh });
/** Turns the loudness readings on or off (they arrive as `Level` events). */
export const audioMonitor = (on: boolean): Promise<Record<string, never>> => core("audio.monitor", { on });
export const modelsStatus = (): Promise<ModelsStatus> => core("models.status");
/** The catalogue of speech models for this computer: which are on disk, which is the engine, which is being fetched. */
export const modelsLocal = (): Promise<LocalModels> => core("models.local");
/**
 * Makes a model of the catalogue the local engine, downloading it first if it is not on disk
 * (progress arrives as `ModelProgress` events). Settles when it is the engine, or has been cancelled.
 */
export const modelsLocalUse = (name: string): Promise<{ ok: true; settings: BrainSettings } | { ok: false; cancelled: true }> =>
  core("models.local_use", { name });
export const modelsLocalCancel = (): Promise<{ cancelled: boolean }> => core("models.local_cancel");
export const modelsLocalDelete = (name: string): Promise<{ deleted: boolean; fallback?: string | null }> => core("models.local_delete", { name });
/** Downloads the models that are missing; progress arrives as `ModelProgress` events. */
export const modelsEnsure = (names: string[]): Promise<{ ok: boolean }> => core("models.ensure", { names });

// ---------- commands of the app ----------

/** Opens one of the few fixed links in the browser (commands.rs refuses any other). */
export const openLink = (url: string): Promise<void> => invoke("open_link", { url });


export const deviceSettingsGet = (): Promise<DeviceSettings> => invoke("device_settings_get");
export const deviceSettingsSet = (patch: Partial<DeviceSettings>): Promise<DeviceSettings> =>
  invoke("device_settings_set", { patch });
/** Whether the keyboard layout has an AltGr key (Right Alt types characters). */
export const keyboardHasAltgr = (): Promise<boolean> => invoke("keyboard_has_altgr");
/** Pauses the global hotkeys while the page records a new one. */
export const hotkeysPause = (paused: boolean): Promise<void> =>
  withWindowHotkeyPause(paused, () => invoke("hotkeys_pause", { paused }));

/**
 * Practises the keys: while on, they are reported as `Hotkey` events and start no dictation and no
 * question (a lesson that shows the key being pressed). Always switch it off again.
 */
export const hotkeysPractice = (on: boolean): Promise<void> => invoke("hotkeys_practice", { on });
/** Esc for a dictation or question that is under way (the app ignores it when none is). */
export const cancelSession = (): Promise<void> => invoke("window_hotkey", { kind: "cancel" });
/** Whether the privacy settings of Windows let desktop apps use the microphone. */
export const micAccess = (): Promise<MicAccess> => invoke("mic_access");

/** Starts hearing the events of the core. Call once; a page that calls again replaces the first. */
export async function subscribeMainEvents(handler: (event: MainEvent) => void): Promise<void> {
  const channel = new Channel<MainEvent>(handler);
  await invoke("main_events", { channel });
}
