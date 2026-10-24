// What the core and the Rust side send to the main window. Type-only: this file has no code.

import type { Undoable } from "./undo";
import type { Cleared } from "./cleared";

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
export type ModelPurpose = "agent" | "cleanup" | "router" | "vision" | "pdf" | "stt" | "memory";

export type ProviderField = "key" | "base_url" | "api_version" | "region" | "key_id" | "secret" | "service_account" | "project" | "location";
export interface ProviderKind {
  kind: string;
  name: string;
  group: "official" | "gateway" | "local";
  fields: ProviderField[];
  default_base_url: string | null;
  available: boolean;
  docs_url?: string | null;
  verified?: boolean;
  local_context_hint?: boolean;
}
/** Public provider metadata. Secrets never come back from the core. */
export interface ModelProvider {
  id: string;
  kind: string;
  name: string;
  base_url: string;
  has_key: boolean;
  enabled: boolean;
  status: "ok" | "auth" | "offline" | "unknown";
  models: number;
}
export interface ProviderPatch {
  name?: string;
  base_url?: string;
  key?: string;
  key_id?: string;
  region?: string;
  api_version?: string;
  secret?: string;
  service_account?: string;
  project?: string;
  location?: string;
  enabled?: boolean;
}
export interface ProviderTest {
  ok: boolean;
  kind?: "auth" | "offline" | "error";
  models: number;
}
export interface ProviderModel {
  id: string;
  name: string;
  context: number | null;
  context_warning?: boolean;
  vision: boolean;
  tools: boolean;
  audio_in: boolean;
  price_in: number | null;
  price_out: number | null;
}

/** Whether a role can use a model, and if not why: a word of the core such as `no_structured_output`. */
export interface RoleFit {
  usable: boolean;
  reason: string | null;
}

/** Public local CLI detection; credentials never cross this interface. */
export type CliBrainId = "claude" | "codex" | "gemini";
export interface CliBrain {
  id: CliBrainId;
  name: string;
  installed: boolean;
  logged_in: boolean | null;
  version?: string;
  login_command: string;
  models: CliModel[];
  notes?: string;
}

export interface CliModel {
  id: string;
  name: string;
  provider?: string;
  effort_models?: Record<string, string>;
  description?: string;
  is_default?: boolean;
  reasoning_efforts?: string[];
  default_effort?: string | null;
}

/** A model of `models.catalog`. Prices are US$; null is unknown or does not apply. */
export interface CatalogModel {
  effort_models?: Record<string, string>;
  description?: string;
  is_default?: boolean;
  reasoning_efforts?: string[];
  default_effort?: string | null;
  id: string;
  /** Connection that serves the model; absent on legacy OpenRouter catalogs. */
  provider_id?: string;
  provider_name?: string;
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
  /**
   * What it reads, as OpenRouter's list says (`text`, `image`, `file`, `audio`...). The 看圖片
   * picker of Settings › 拉斐爾 offers the models whose list holds `image`. A catalog from before
   * the core sent it has none: see `acceptsImages` (model-picker.ts).
   */
  input_modalities?: string[];
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

/** `models.catalog`: models from enabled providers, by kind; legacy catalogs contain OpenRouter alone. */
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

/** `stats.summary`: lasting local totals, independent of retained history. */
export interface StatsSummary {
  today: Period;
  week: Period;
  month: Period;
  all: Period;
  /** Days in a row, up to today, with at least one dictation. */
  streak_days: number;
  /** The last 14 days (or as many as asked for), oldest first, today last. */
  by_day: { date: string; dictations: number; words: number }[];
  /** Days with at least one dictation, ever. */
  active_days: number;
  /** The most days in a row with a dictation, ever. */
  longest_streak_days: number;
  /** Words per minute of speech; null before there is enough to tell. */
  speed_wpm: number | null;
  /** How much longer typing the same words would have taken. */
  time_saved_seconds: number;
  /** Recovery estimates included in the totals; absent in older cores. */
  estimated_words?: number;
  estimated_dictations?: number;
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
  /** The speech-to-text that heard it: a cloud model's slug or the local engine's name ("a+b" when the cloud fell back midway); null when it is not known. */
  stt: string | null;
}

export type TurnMessageKind = "thinking" | "progress" | "reply" | "event" | "media";

export interface TurnMessage {
  seq: number;
  kind: TurnMessageKind;
  text: string;
  at_ms: number;
}

export interface MediaPayload {
  type: "link" | "image" | "video" | "pdf";
  url: string;
  title?: string;
  site?: string;
  description?: string;
  thumbnail?: string | null;
  embed?: string | null;
}

/** A turn of the conversation with Raphael (`agent.history.list`, newest first). */
export interface AgentTurn {
  /** Ordered public lines; older cores and turns may have none. Text includes Raphael's framing. */
  messages?: TurnMessage[];
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
  /** The request the turn answered: one press of the key, or one chat of a chat app. */
  session_id?: string;
  /** Where the request came from: `voice` (the key and the overlay) or a chat app such as `telegram`. */
  source?: string;
  /** What the turn cost in US dollars, the background task's work included. */
  cost_usd?: number;
  /** The model that worded the answer, when one did. */
  model?: string | null;
  /** What Raphael did between the question and the answer, in order; a background task's steps last. */
  steps?: AgentStep[];
  /** The background task the turn handed over, if it handed one over. */
  task?: AgentTask | null;
  /** The images and PDFs the request came with, by their names and types only: never their bytes. */
  attachments?: TurnAttachment[];
  /** Whose thread the turn is in: 0 (or none, in a history from before the members) is Raphael's. */
  member_id?: number;
  /**
   * For an exchange between two members (`route` "exchange", `source` "member": one asked the other
   * with `ask_member`, `question` what was asked and `text` the answer, written in both threads),
   * the member on the other side: 0 is Raphael. The thread shows it as one collapsed line,
   * 「N 則訊息往來：<name>」.
   */
  peer_id?: number | null;
}

/** A file a turn's request came with, as the history keeps it: what the conversation shows as a chip. */
export interface TurnAttachment {
  name: string;
  mime: string;
}

/**
 * A file handed with a typed question, as `agent.ask` takes it (`attachments`, the core's
 * services/attachments.py `from_wire`): its name, its type and its bytes in base64.
 */
export interface WireAttachment {
  name: string;
  mime: string;
  data: string;
}

/** One thing Raphael did for a turn. Never what a tool was given or what it returned. */
export interface AgentStep {
  /**
   * `tool` (a lookup or a connected service's tool), `command` (something done to this PC), `computer`
   * (Raphael operating an app: `tool` is `apps`, `look`, `open`, `focus`, `click`, `type`, `keys` or
   * `scroll`, `server` the app's name) or `model` (a model call of a background task).
   */
  kind: string;
  /** The tool's name on its server; null for a call the task was not given. */
  tool: string | null;
  /** The connected service the tool belongs to, `Raphael` for Raphael's own lookups, null for a command. */
  server: string | null;
  /** `ok`, `error`, `timeout`, `blocked` or `cancelled`. */
  outcome: string;
  ms: number;
  /** Why it failed, one word (`auth`, `timeout`, `not_found`...); null when it went well. */
  reason: string | null;
  /** When it ended. */
  at_ms: number;
}

/** A background task as the conversations show it: how it stands, without what was said. */
export interface AgentTask {
  id: number;
  /** `queued`, `running`, `done`, `failed`, `limit`, `cancelled` or `interrupted`. */
  state: string;
  created_at_ms?: number;
  started_at_ms: number | null;
  finished_at_ms: number | null;
  reason?: string | null;
  model_calls?: number;
  tool_calls?: number;
  cost_usd?: number;
}

/** An entry of `dictionary.list` */
export interface DictionaryEntry {
  id: number;
  word: string;
  misheard: string[];
  /** "manual" for a word the user typed or imported, "suggested" for one taken from a suggestion of an earlier version, "learned" for one Raphael added by itself after the user corrected it in a pasted dictation. */
  source: "manual" | "suggested" | "learned";
}

/** `settings.get`: the settings of the brain. */
export interface BrainSettings {
  agent_fallbacks?: string[];
  cleanup_fallbacks?: string[];
  router_fallbacks?: string[];
  vision_fallbacks?: string[];
  pdf_fallbacks?: string[];
  stt_fallbacks?: string[];
  memory_fallbacks?: string[];
  monthly_limit_usd: number;
  cleanup_model: string;
  /** The languages the speech recognition listens for: locale ids such as "zh-TW", one to five. */
  languages: string[];
  history_retention_days: number;
  /** "cloud" (OpenRouter, the local engine as fallback when there is one) or "local". */
  stt_engine: string;
  /** Which model of the catalogue the local engine is. */
  stt_local_model?: string | null;
  stt_cloud_model: string;
  /** The model that writes Raphael's answers. */
  agent_model?: string;
  /** The city Raphael looks the weather up for. */
  agent_city?: string;
  /** The most one background task may spend, in US$: 0.1, 0.2, 0.5 or 1. */
  worker_cost_cap_usd?: number;
  /** The models starred in the pickers, per role, in the order they were added. */
  model_favorites?: Partial<Record<ModelRole, string[]>>;
  /** Who looks the web up: `openrouter` (its `web` plugin, today's way), `brave`, `tavily`, `exa` or `searxng`. */
  web_search_service?: string;
  /** Who reads a web page: `builtin` (this computer, at no cost), `jina`, `firecrawl` or `tavily`. */
  web_read_service?: string;
  /** The model that looks at images; "" is the model that answers (`agent_model`). */
  vision_model?: string;
  /** Who reads a PDF: `builtin` (its text, on this computer) or `openrouter`. */
  pdf_service?: string;
  /** The address of the owner's own SearXNG, `http(s)`; "" while there is none. */
  searxng_url?: string;
  /** Whether each capability of the tools is used at all (the switch of its row in 服務): the first four are on until turned off. */
  web_search_enabled?: boolean;
  web_read_enabled?: boolean;
  vision_enabled?: boolean;
  pdf_enabled?: boolean;
  /** Whether Raphael speaks his answers aloud: off until the owner turns it on. */
  tts_enabled?: boolean;
  /** Who speaks Raphael's answers aloud: `fish` (Fish Audio), the one voice service; "no voice" is `tts_enabled` off. */
  tts_service?: string;
  /** The id of the owner's Fish Audio voice model: 32 hexadecimal characters, or "" while none is set. Not a secret. */
  tts_fish_voice?: string;
  /** The Fish Audio model; the window never changes it. */
  tts_fish_model?: string;
}

/** One service a capability can use, as `services.status` lists it. Never a key, only whether there is one. */
export interface ServiceOption {
  id: string;
  /** It works only with a key of the owner's. */
  needs_key: boolean;
  /** A key of the owner's is in the vault (Windows Credential Manager). */
  has_key: boolean;
  /** A key is taken but not needed (Jina, which has lower limits without one). */
  key_optional: boolean;
  /** SearXNG only: whether its address is set (`searxng_url`). */
  url_set?: boolean;
}

/** A capability's services: the one chosen and the ones it could use. */
export interface ServiceChoice {
  /** The owner has the capability on (a capability that is off is not used at all). */
  enabled: boolean;
  service: string;
  options: ServiceOption[];
}

/** `services.status`: who searches, reads pages, reads PDFs and looks at images. */
export interface ServicesStatus {
  search: ServiceChoice;
  read: ServiceChoice;
  pdf: ServiceChoice;
  /** Raphael's voice: `fish` (which needs a key), and whether the voice model id is filled in. */
  voice: ServiceChoice & { fish_voice_set: boolean };
  vision: {
    /** The owner has reading images on. */
    enabled: boolean;
    /** The `vision_model` setting: "" is the model that answers. */
    model: string;
    /** The model that will look: `model`, or the answer model when it is "". */
    resolved_model: string;
    /** Whether the catalog says `resolved_model` reads images. */
    supports_images: boolean;
  };
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
  /** The model that backs up the cloud (null when the engine is local, or when there is none). */
  fallback?: string | null;
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
  /**
   * The apps Raphael may always operate on this PC (the 一律允許 answer of a question), by the
   * executable's file name in lower case (`excel.exe`) and the name to show (`Microsoft Excel`).
   * Settings written before the feature lack it: read it with `computerApps`. An app is taken off
   * it one at a time, with `device_computer_app_remove`, never with a whole list computed here.
   */
  computer_apps: { exe: string; name: string }[];
}

/** What commands.rs sends on the `main_events` channel (`MainEvent`). */
export type MainEvent =
  | { type: "Level"; value: number }
  | { type: "ModelProgress"; name: string; done: number; total: number }
  /** An editor hides its text from UI Automation, so corrections in it cannot be learned. */
  | { type: "LearnLimited"; app: string; reason: string }
  /** A tray menu item asks for a page, and perhaps one thing to do there (tray-open.ts). */
  | { type: "TrayOpen"; page: TrayPage; action?: TrayAction }
  /** A turn of the conversations was written or completed, in the thread of `member_id` (0: Raphael's; none from an older shell). */
  | { type: "AgentTurn"; turn_id: number; member_id?: number }
  | { type: "RoutinesChanged"; member_id: number }
  | { type: "MembersChanged" }
  | { type: "ProvidersChanged" }
  | { type: "BrainsChanged" }
  | { type: "MemoryChanged"; member_id: number; space?: "own" | "shared" }
  /** A background task moved on: a step, a start or an end. */
  | { type: "WorkerChanged"; task_id: number; state: string; running: boolean }
  /** A service is signed in to with a code typed at its own page: show the code, open the address. */
  | { type: "DeviceCode"; catalog_id: string; user_code: string; verification_uri: string; expires_in: number }
  /** A key of Raphael went down or up (from the hook or from the window itself), whether or not the guide practises. */
  | { type: "Hotkey"; kind: HotkeyKind }
  /** A dictation (`agent` false) or a question to Raphael (`agent` true) moved on. */
  | { type: "Dictation"; phase: DictationPhase; agent: boolean }
  /** Raphael asks a YES/NO question (the overlay shows it too): the 拉斐爾 page's card answers it as well. */
  | ({ type: "AgentQuestion" } & AgentQuestion)
  /** The question `id` was answered (here or on the overlay), ran out or was dropped. */
  | { type: "AgentQuestionClosed"; id: string }
  /** The microphone's loudness, 0 to 1, while a question to Raphael is recorded (never a dictation's). */
  | { type: "VoiceLevel"; value: number };

/** A YES/NO question of Raphael's that waits for its answer (`AgentQuestion`, `agent_question_open`). */
export interface AgentQuestion {
  id: string;
  /** 問, or its word in the language of the window. */
  prefix: string;
  text: string;
  /** `T2` is answered by a click; `T3`, which cannot be undone, by a YES that is held first. */
  tier: "T2" | "T3";
  /** How long the question waits for an answer in all, in ms. */
  timeout_ms: number;
  /** The words of the two plain answers, in the language of the window; the page has its own 是 and 否 when they are absent. */
  yes?: string;
  no?: string;
  /**
   * The label of a third answer, 「一律允許」 (YES, and keep allowing): only the question about letting
   * Raphael operate an app has it. Such a question is always T2.
   */
  always?: string;
}

// ---------- members (members.*, groups.*, routines.*) ----------

/**
 * A member as `members.list` lists it, Raphael first as member 0 (he is no row of the core).
 * `avatar_url` is a picture the page may show as it is (a `data:image/...` URL), absent for a member
 * with none; Raphael's is the owner's picture of him with its `avatar_crop`, absent while he is his
 * emblem. `unread`: a turn arrived while the owner was not reading its thread (until
 * `members.read`); `waiting`: a question waits for the owner there. `preview` is the last line of
 * its thread (the core cuts it to one line), `last_at` when it was said.
 */
export interface Member {
  id: number;
  name: string;
  role: string;
  instructions: string;
  /** Character and speaking style; absent in older cached member lists. */
  personality?: string;
  /** `""`, `"preset:<stem>"` or `"file:<stored name>"`. */
  avatar: string;
  avatar_url?: string;
  /** Raphael's only: the cut of the owner's picture of him (avatar.ts `AvatarCrop`). */
  avatar_crop?: { x: number; y: number; zoom: number };
  /** Null: 未分組. */
  group_id: number | null;
  /** "" is the model that answers for Raphael. */
  model: string;
  /** The tool and plugin ids it may use; null is every one that is on (the core's default). */
  tools: string[] | null;
  position: number;
  /** When it was made, in ms. */
  created: number;
  unread: boolean;
  waiting: boolean;
  preview: string;
  last_at: number | null;
}

/** A place a group's members look at for progress: a GitHub `owner/repo`, or a Notion page id or URL. */
export interface MemberSource {
  kind: "github" | "notion";
  ref: string;
}

/** A group of `members.list`. */
export interface MemberGroup {
  id: number;
  name: string;
  position: number;
  sources: MemberSource[];
}

/** `members.list` */
export interface MembersList {
  members: Member[];
  groups: MemberGroup[];
}

/** What `members.create` takes (`ui_language` names the language of the routine it may offer). */
export interface NewMember {
  name: string;
  role?: string;
  instructions?: string;
  personality?: string;
  group_id?: number | null;
  avatar?: string;
  tools?: string[];
  model?: string;
  ui_language?: string;
}

/**
 * What `members.update` may change, beside its `id`: the avatar only as `""` or `"preset:<stem>"`
 * (a picture of the PC goes by `members.set_avatar`).
 */
export type MemberPatch = Partial<Pick<Member, "name" | "role" | "personality" | "instructions" | "group_id" | "model" | "tools" | "avatar" | "position">>;

/** An image of the presets folder (`members.presets`): its avatar value, its file stem, and the picture as a data URL. */
export interface AvatarPreset {
  /** What `members.update`/`members.create` take as the avatar: `preset:<stem>`. */
  avatar: string;
  name: string;
  url: string;
}

/** The routine the core offers with a new member of a group with places to look (`members.create`, `routines.template`). */
export interface RoutineOffer {
  title: string;
  prompt: string;
  schedule: RoutineSchedule;
}

/** When a routine runs: at a time each day, on days of the week (0 is Sunday) at a time, or every 1 to 24 hours. */
export type RoutineSchedule =
  | { kind: "daily"; time: string }
  | { kind: "weekly"; days: number[]; time: string }
  | { kind: "every"; hours: number };

/** A routine of `routines.list`. Times are ms; null for never. */
export interface Routine {
  id: number;
  member_id: number;
  title: string;
  prompt: string;
  schedule: RoutineSchedule;
  enabled: boolean;
  last_run: number | null;
  next_run: number | null;
}

export type HotkeyKind = "dictation_down" | "dictation_up" | "agent_down" | "agent_up" | "paste_last" | "cancel";
export type DictationPhase = "listening" | "processing" | "pasted" | "cancelled" | "failed";

/** What `mic_access` answers: whether the privacy settings of Windows let desktop apps use the microphone. */
export interface MicAccess {
  allowed: boolean;
  /** The first switch that is off: the device, the person, or the one for desktop apps. */
  blocked_by: "system" | "user" | "desktop_apps" | null;
}

/** The pages the tray's menu can open. */
export type TrayPage = "home" | "history" | "dictionary" | "settings";

/** What the tray asks the page to do once it is open: start adding a word to the dictionary. */
export type TrayAction = "add-word";

// ---------- what the pages share with main.ts ----------

export type PageId = "home" | "agent" | "history" | "dictionary" | "settings";

/** The tabs of the settings panel. */
export type SettingsPane = "general" | "dictation" | "agent" | "connections" | "messaging" | "models" | "usage" | "data" | "about";

/**
 * What the 拉斐爾 page is to put in its composer when it opens, ready to be sent or changed: the
 * 「試試看」 of a connected service (Settings › 連接) goes there with one of its example questions.
 */
export type AgentDraft = `draft:${string}`;

/** One of the five pages of the main window. */
export interface Page {
  readonly id: PageId;
  readonly el: HTMLElement;
  /**
   * The page comes on screen (or the window comes back): load what it shows. The settings open at
   * the tab given; the 拉斐爾 page may be given a draft for its composer.
   */
  show(arg?: SettingsPane | AgentDraft): void | Promise<void>;
  /** The page goes off screen (or the window goes away): stop what runs for it. */
  hide(): void;
}

/** What main.ts gives the pages. */
export interface AppContext {
  /**
   * Goes to a page. The settings panel opens at `arg` if a tab is given (the key and the limit are in
   * "usage"); the 拉斐爾 page takes a draft for its composer (the settings close on the way).
   */
  go(page: PageId, arg?: SettingsPane | AgentDraft): void;
  /** Opens the history at one day, `2026-10-14`: its dictations only, with a way back to all of them. */
  showDay(date: string): void;
  /** The device settings as they are now. */
  device(): DeviceSettings;
  /** Reads the device settings from the app again; rejects if they cannot be read. */
  refreshDevice(): Promise<DeviceSettings>;
  /** Changes some device settings; rejects with the reason. */
  setDevice(patch: Partial<DeviceSettings>): Promise<DeviceSettings>;
  /** Takes one app off the always-allowed list (`device_computer_app_remove`); rejects with the reason. */
  removeComputerApp(exe: string): Promise<DeviceSettings>;
  /** Hears the events of the core (microphone level, model download). Returns how to stop hearing. */
  onEvent(handler: (event: MainEvent) => void): () => void;
  /** Things that happen only after 10 seconds, with 「復原」. */
  undo: Undoable;
  /**
   * The dictation history or the conversation was emptied from Settings › 資料: the pages that
   * show it listen here and drop what they loaded.
   */
  cleared: Cleared;
  /** Closes the settings panel (it opens with `go("settings")`). */
  closeSettings(): void;
  /** Opens the first-run guide again over the window (the settings close first); it does not clear `onboarded`. */
  rerunGuide(): void;
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
  /** `oauth` is signed in to with a login in the browser, `device` with a code typed at the service. */
  auth: "none" | "oauth" | "device";
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
  /** The mark in `service-icons.ts`, or null for a neutral plug. */
  icon: string | null;
  popular: boolean;
  summary: Words;
  about: Words;
  examples: Record<"zh-TW" | "en" | "ja", string[]>;
  /** "none": a public server, added with no login. */
  auth?: "oauth" | "none" | "device";
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
