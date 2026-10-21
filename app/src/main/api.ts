// The main window's way to the rest of the app: calls to the core through `core_call`, and the
// commands of commands.rs. Every function rejects with the reason as a string (`describeError`
// makes it fit to show).

import { Channel, invoke } from "@tauri-apps/api/core";
import { withWindowHotkeyPause } from "./window-hotkeys";
import type { LanguageGroup } from "./languages";
import type {
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
export const integrationsAudit = (limit = 8): Promise<{ entries: AuditEntry[] }> => core("integrations.audit", { limit });

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
export const modelsLocalDelete = (name: string): Promise<{ deleted: boolean }> => core("models.local_delete", { name });
/** Downloads the models that are missing; progress arrives as `ModelProgress` events. */
export const modelsEnsure = (names: string[]): Promise<{ ok: boolean }> => core("models.ensure", { names });

// ---------- commands of the app ----------

export const deviceSettingsGet = (): Promise<DeviceSettings> => invoke("device_settings_get");
export const deviceSettingsSet = (patch: Partial<DeviceSettings>): Promise<DeviceSettings> =>
  invoke("device_settings_set", { patch });
/** Whether the keyboard layout has an AltGr key (Right Alt types characters). */
export const keyboardHasAltgr = (): Promise<boolean> => invoke("keyboard_has_altgr");
/** Pauses the global hotkeys while the page records a new one. */
export const hotkeysPause = (paused: boolean): Promise<void> =>
  withWindowHotkeyPause(paused, () => invoke("hotkeys_pause", { paused }));

/** Starts hearing the events of the core. Call once; a page that calls again replaces the first. */
export async function subscribeMainEvents(handler: (event: MainEvent) => void): Promise<void> {
  const channel = new Channel<MainEvent>(handler);
  await invoke("main_events", { channel });
}
