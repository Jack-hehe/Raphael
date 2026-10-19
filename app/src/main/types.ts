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

/** One price of `pricing.get`: US$ per million tokens or per minute of audio; null when unknown. */
export interface CleanupPrice {
  id: string;
  name: string;
  input_per_mtok: number | null;
  output_per_mtok: number | null;
}

export interface SttPrice extends CleanupPrice {
  per_minute: number | null;
}

/** `pricing.get`: the prices of the models the settings page offers. */
export interface Pricing {
  cleanup: CleanupPrice[];
  stt: SttPrice[];
  /** When the prices were read from OpenRouter; null for the built-in ones. */
  fetched_at_ms: number | null;
  /** `live`: read just now; `cached`: read earlier and kept; `builtin`: shipped with the app. */
  source: "live" | "cached" | "builtin";
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
  stt_cloud_model: string;
  /** Learn the words the user corrects by hand after a paste (default on). Absent from older cores. */
  learn_corrections?: boolean;
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

/** The settings in `device.json` (device_settings.rs). */
export interface DeviceSettings {
  dictation_keys: string[];
  /**
   * How the dictation key works: `toggle` (press to start, press again to stop and transcribe)
   * or `hold` (hold while talking, release to stop and transcribe).
   */
  dictation_mode: "toggle" | "hold";
  paste_last_keys: string[];
  /** The name of the microphone; null is the system's default one. */
  microphone: string | null;
  emblem_size: 120 | 150 | 194;
  subtitles: "both" | "zh" | "ja" | "off";
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
  | { type: "LearnLimited"; app: string; reason: string };

// ---------- what the pages share with main.ts ----------

export type PageId = "home" | "history" | "dictionary" | "settings";

/** One of the four pages of the main window. */
export interface Page {
  readonly id: PageId;
  readonly el: HTMLElement;
  /** The page comes on screen (or the window comes back): load what it shows. */
  show(): void | Promise<void>;
  /** The page goes off screen (or the window goes away): stop what runs for it. */
  hide(): void;
}

/** What main.ts gives the pages. */
export interface AppContext {
  go(page: PageId): void;
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
