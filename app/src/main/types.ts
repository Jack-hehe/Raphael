// What the core and the Rust side send to the main window. Type-only: this file has no code.

import type { Undoable } from "./undo";

/** `status.get` */
export interface Status {
  openrouter: {
    /** How the latest contact went. `no_key` is told from the key store. */
    state: "ok" | "error" | "unknown" | "no_key";
    latency_ms: number | null;
    checked_at_ms: number | null;
    /** The kind of the OpenRouter error when `state` is `error`: auth, network, timeout, ... */
    error: string | null;
    key: KeyInfo | null;
    /** Last successful key usage reading; missing API fields stay unknown. Amounts are USD. */
    usage: {
      daily: number | null;
      weekly: number | null;
      monthly: number | null;
      total: number | null;
      limit: number | null;
      limit_remaining: number | null;
    } | null;
  };
  spend: {
    month_usd: number;
    today_usd: number;
    limit_usd: number;
    forecast_usd: number;
    daily: number[];
  };
  last: {
    text: string;
    created_at_ms: number;
    target_app: string | null;
    stt_ms: number;
    llm_ms: number;
  } | null;
}

export interface KeyInfo {
  label: string;
  limit: number | null;
  limit_remaining: number | null;
  limit_reset: string | null;
  usage_daily: number | null;
  usage_monthly: number | null;
  usage_weekly: number | null;
  usage_total: number | null;
}

/** What a model is chosen for: the dictation cleanup, Raphael's answers, speech to text (the core's brain/catalog.py). */
export type ModelRole = "cleanup" | "answer" | "stt";

/** Whether a role can use a model, and if not why: a word of the core such as `no_structured_output`. */
export interface RoleFit {
  usable: boolean;
  reason: string | null;
}

/** A model of `models.catalog`. Prices are US$; null is unknown or does not apply. */
export interface CatalogModel {
  id: string;
  /** Without its maker: "GPT-6 Luna". */
  name: string;
  /** Its maker: "OpenAI". */
  provider: string;
  /** Per million tokens: language models, and speech models priced by the token. */
  input_per_mtok: number | null;
  output_per_mtok: number | null;
  /** Per minute of audio: speech models priced by the second. */
  per_minute: number | null;
  free: boolean;
  /** No price of its own: a router, which picks a model for each request. */
  variable_price: boolean;
  /** In tokens, for a language model. */
  context_length: number | null;
  /** When OpenRouter added it. */
  created_at_ms: number | null;
  /** What it can do: `structured_output`, `reasoning`, `reasoning_off`, `max_tokens`, `tool_calling` (no role needs it: the plugins do); `dictionary_hints` for speech. */
  capabilities: Record<string, boolean>;
  /** The roles of its kind. */
  roles: Partial<Record<ModelRole, RoleFit>>;
}

/** A chosen model that OpenRouter no longer lists, whose setting went back to its default. */
export interface ReplacedModel {
  key: "cleanup_model" | "agent_model" | "stt_cloud_model";
  role: ModelRole;
  model: string;
  default: string;
}

/** `models.catalog`: every OpenRouter model Raphael could use, by kind. */
export interface ModelCatalog {
  /** Text in, text out: the cleanup and the answers. */
  llm: CatalogModel[];
  /** Speech to text. */
  stt: CatalogModel[];
  /** When the list was read from OpenRouter; null for the built-in one. */
  fetched_at_ms: number | null;
  /** `live`: read just now; `cached`: read earlier and kept; `builtin`: Raphael's own models only. */
  source: "live" | "cached" | "builtin";
  /** The settings this answer set back to their defaults: tell the user. */
  replaced: ReplacedModel[];
}

/** What was dictated in a stretch of time (`stats.summary`). */
export interface Period {
  dictations: number;
  words: number;
  audio_seconds: number;
  cost_usd: number;
}

/** `stats.summary`: the numbers of the home page, all from this computer's history. */
export interface StatsSummary {
  today: Period;
  week: Period;
  month: Period;
  all: Period;
  /** Days in a row, up to today, with at least one dictation. */
  streak_days: number;
  /** The last 14 days (or as many as asked for), oldest first, today last. */
  by_day: { date: string; dictations: number; words: number }[];
  /** Days with at least one dictation, in all the history (absent from older cores). */
  active_days?: number;
  /** The most days in a row with a dictation, ever (absent from older cores). */
  longest_streak_days?: number;
  /** Words per minute of speech; null before there is enough to tell. */
  speed_wpm: number | null;
  /** How much longer typing the same words would have taken. */
  time_saved_seconds: number;
}

/** A row of `history.list` */
export interface HistoryItem {
  id: number;
  created_at_ms: number;
  kind: string;
  /** The speech-to-text result, before cleanup. */
  raw_text: string;
  /** The final text. */
  text: string;
  cleaned: boolean;
  fallback_reason: string | null;
  target_app: string | null;
  audio_seconds: number;
  stt_ms: number;
  llm_ms: number;
  model: string | null;
  cost_usd: number;
  /** The speech-to-text that heard it: a cloud model's slug or the local engine's name ("a+b" when the cloud fell back midway). Absent from older cores. */
  stt?: string | null;
}

/** A turn of the conversation with Raphael (`agent.history.list`, newest first). */
export interface AgentTurn {
  id: number;
  created_at_ms: number;
  /** What the person said, as the speech recognition heard it. */
  question: string;
  /** How the question was routed: a command, a question, a chat... (the core's word). */
  route: string;
  /** The word before Raphael's reply: 答, 告, 否, 了 or 問 (Answer, Notice, ... in English). */
  prefix: string;
  /** Raphael's reply, the line of the overlay. */
  text: string;
  /** The longer version of the reply, when there is one. */
  detail: string | null;
  status: string;
}

/** An entry of `dictionary.list` */
export interface DictionaryEntry {
  id: number;
  word: string;
  misheard: string[];
  /** The core uses "learned" for corrections; "auto" is also accepted. Absent: typed. */
  source?: "manual" | "suggested" | "learned" | "auto";
}

/** `settings.get`: the settings of the brain. */
export interface BrainSettings {
  monthly_limit_usd: number;
  cleanup_model: string;
  /** The languages the speech recognition listens for: locale ids such as "zh-TW", one to five. */
  languages: string[];
  /** Legacy language setting, derived from `languages` by the core. */
  language?: string;
  history_retention_days: number;
  /** "cloud" (OpenRouter, the local engine as fallback) or "local". */
  stt_engine: string;
  /** Which model of the catalogue the local engine is. */
  stt_local_model?: string;
  stt_cloud_model: string;
  /** The model that writes Raphael's answers. */
  agent_model?: string;
  /** The city Raphael looks the weather up for. */
  agent_city?: string;
  /** The models starred in the pickers, per role, in the order they were added. */
  model_favorites?: Partial<Record<ModelRole, string[]>>;
}

/** `secrets.set_openrouter_key` */
export type KeyCheck =
  | { valid: true; label: string; limit: number | null; limit_remaining: number | null; limit_reset: string | null }
  | { valid: false; reason: string };

/** `audio.devices` */
export interface AudioDevices {
  devices: { index: number; name: string; is_default: boolean }[];
  selected: string | null;
  /** Actual automatic device after signal detection, or the default before detection. */
  automatic_device?: string | null;
}

/** `models.status` */
export interface ModelsStatus {
  models: { name: string; ready: boolean }[];
}

/** A speech model of the catalogue for this computer (`models.local`). */
export interface LocalModel {
  /** The id: the name in the core's registry, and the value of the `stt_local_model` setting. */
  name: string;
  title: string;
  /** Codes: `zh`, `en`, `ja`, `ko`, `yue`, and `multi` for many more. */
  languages: string[];
  /** What it is for, as a key the window puts in words. */
  note: string;
  /** Bytes to download. */
  size: number;
  /** On disk. */
  ready: boolean;
}

/** `models.local` */
export interface LocalModels {
  models: LocalModel[];
  /** The model that is the local engine (it is in use only when `stt_engine` is "local"). */
  active: string;
  /** The model being downloaded, if one is. */
  downloading: string | null;
}

/** The settings in `device.json` (device_settings.rs). */
export interface DeviceSettings {
  dictation_keys: string[];
  /**
   * How the dictation key works: `toggle` (press to start, press again to stop and transcribe)
   * or `hold` (hold while talking, release to stop and transcribe).
   */
  dictation_mode: "toggle" | "hold";
  paste_last_keys: string[];
  /** The shortcut that asks Raphael (Right Alt+Space by default); never the same as another one. */
  agent_keys: string[];
  /** The name of the microphone; null is the system's default one. */
  microphone: string | null;
  emblem_size: 120 | 150 | 194;
  /** Reports beside the emblem, on or off. (Files from before had "both", "zh" and "ja": they mean on.) */
  subtitles: "on" | "off";
  appearance: "system" | "light" | "dark";
  ui_language: "zh-TW" | "en" | "ja";
  autostart: boolean;
  onboarded: boolean;
}

/** What commands.rs sends on the `main_events` channel (`MainEvent`). */
export type MainEvent =
  | { type: "Level"; value: number }
  | { type: "ModelProgress"; name: string; done: number; total: number }
  /** An editor hides its text from UI Automation, so corrections in it cannot be learned. */
  | { type: "LearnLimited"; app: string; reason: string }
  /** A tray menu item asks for a page, and perhaps one thing to do there (tray-open.ts). */
  | { type: "TrayOpen"; page: TrayPage; action?: TrayAction };

/** The pages the tray's menu can open. */
export type TrayPage = "home" | "history" | "dictionary" | "settings";

/** What the tray asks the page to do once it is open: start adding a word to the dictionary. */
export type TrayAction = "add-word";

// ---------- what the pages share with main.ts ----------

export type PageId = "home" | "agent" | "history" | "dictionary" | "settings";

/** The tabs of the settings panel. */
export type SettingsPane = "general" | "dictation" | "agent" | "connections" | "usage" | "about";

/** One of the five pages of the main window. */
export interface Page {
  readonly id: PageId;
  readonly el: HTMLElement;
  /** The page comes on screen (or the window comes back): load what it shows. The settings open at the tab given. */
  show(pane?: SettingsPane): void | Promise<void>;
  /** The page goes off screen (or the window goes away): stop what runs for it. */
  hide(): void;
}

/** What main.ts gives the pages. */
export interface AppContext {
  /** Goes to a page. The settings panel opens at `pane` if one is given (the key and the limit are in "usage"). */
  go(page: PageId, pane?: SettingsPane): void;
  /** Opens the history at one day, `2026-10-14`: its dictations only, with a way back to all of them. */
  showDay(date: string): void;
  /** The device settings as they are now. */
  device(): DeviceSettings;
  /** Reads the device settings from the app again; rejects if they cannot be read. */
  refreshDevice(): Promise<DeviceSettings>;
  /** Changes some device settings; rejects with the reason. */
  setDevice(patch: Partial<DeviceSettings>): Promise<DeviceSettings>;
  /** Hears the events of the core (microphone level, model download). Returns how to stop hearing. */
  onEvent(handler: (event: MainEvent) => void): () => void;
  /** Things that happen only after 10 seconds, with 「復原」. */
  undo: Undoable;
  /** Closes the settings panel (it opens with `go("settings")`). */
  closeSettings(): void;
}

// ---------- connections (the plugins of the core and the store) ----------

/** A tool of a connected service. Every tool is on until the user turns it off; `kind` only groups it. */
export interface ConnectionTool {
  name: string;
  description: string;
  enabled: boolean;
  /** `write` changes something, `read` only looks; from the server's own word about it, else from its name. */
  kind: "write" | "read";
  /** The human name the server gives the tool, if it does. */
  title?: string | null;
}

/** One connected service, as `toolsets.list` has it. Never a secret, only the names of them. */
export interface ConnectionGroup {
  id: string;
  name: string;
  enabled: boolean;
  status: "disabled" | "error" | "unchecked" | "ready";
  /** The kind of the latest failure, one word; `auth` is a login that ran out. */
  error: string | null;
  /** `oauth` is signed in to with a login in the browser. */
  auth: "none" | "oauth";
  /** The entry of the store it was added from. */
  catalog_id: string | null;
  tools: ConnectionTool[];
  transport: "stdio" | "http";
  command: string;
  url: string;
  env: string[];
  headers: string[];
  args: string[];
  tool_count: number;
  enabled_count: number;
  write_count: number;
  read_count: number;
  checked_at_ms: number | null;
}

/** The words of the window, in each language the user can choose. */
export type Words = Record<"zh-TW" | "en" | "ja", string>;

/** A service of the store (`integrations.catalog`). */
export interface StoreService {
  id: string;
  name: string;
  publisher: string;
  url: string;
  category: string;
  /** The mark in `service-icons.ts`, or null for a letter. */
  icon: string | null;
  popular: boolean;
  summary: Words;
  about: Words;
  examples: Record<"zh-TW" | "en" | "ja", string[]>;
  /** "none": a public server, added with no login. */
  auth?: "oauth" | "none";
  /** The id of the connected service it was added as; null when it is not added. */
  server_id: string | null;
}

/** What a login came to: the service, or the kind of what went wrong (`denied`, `cancelled`, `timeout`, ...). */
export type ConnectResult = { connected: true; server: { id: string } } | { connected: false; error: string };

/** One line of the record of calls: metadata only, never an argument or a result. */
export interface AuditEntry {
  id: number;
  at_ms: number;
  server: string;
  tool: string;
  approver: "auto" | "user" | "none";
  outcome: "ok" | "error" | "timeout" | "denied" | "expired" | "blocked";
}

/** What the user typed to connect a server. The values of `env` and `headers` go to the vault of the core and nowhere else. */
export interface NewConnection {
  name: string;
  transport: "stdio" | "http";
  command?: string;
  args?: string[];
  url?: string;
  env?: { name: string; value: string }[];
  headers?: { name: string; value: string }[];
}
