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
  usage_daily: number;
  usage_monthly: number;
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
}

/** An entry of `dictionary.list` */
export interface DictionaryEntry {
  id: number;
  word: string;
  misheard: string[];
  source: "manual" | "suggested";
}

/** `settings.get`: the settings of the brain. */
export interface BrainSettings {
  monthly_limit_usd: number;
  cleanup_model: string;
  language: string;
  history_retention_days: number;
}

/** `secrets.set_openrouter_key` */
export type KeyCheck =
  | { valid: true; label: string; limit: number | null; limit_remaining: number | null; limit_reset: string | null }
  | { valid: false; reason: string };

/** `audio.devices` */
export interface AudioDevices {
  devices: { index: number; name: string; is_default: boolean }[];
  selected: string | null;
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
  autostart: boolean;
  onboarded: boolean;
}

/** What commands.rs sends on the `main_events` channel (`MainEvent`). */
export type MainEvent =
  | { type: "Level"; value: number }
  | { type: "ModelProgress"; name: string; done: number; total: number };

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
}
