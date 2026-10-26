// The main window's way to the rest of the app: calls to the core through `core_call`, and the
// commands of commands.rs. Every function rejects with the reason as a string (`describeError`
// makes it fit to show).

import { Channel, invoke } from "@tauri-apps/api/core";
import { withWindowHotkeyPause } from "./window-hotkeys";
import type { LanguageGroup } from "./languages";
import type { TelegramStatus } from "./messaging";
import type { AvatarCrop } from "./avatar";
import type { UsageAnalytics, UsageRequest } from "./usage-analytics-types";
import type { HomeOverview } from "./home-types";
import type { CliAllowances, RunReport, RunRequest } from "./run-analytics-types";
import type {
  AgentQuestion,
  AgentTurn,
  AuditEntry,
  ConnectResult,
  ConnectionGroup,
  NewConnection,
  StoreService,
  AudioDevices,
  AvatarPreset,
  BrainSettings,
  CliBrain,
  CliBrainId,
  Member,
  MemberDraft,
  GroupDraft,
  MemberGroup,
  MemberPatch,
  MembersList,
  NewMember,
  Routine,
  RoutineOffer,
  RoutineRun,
  Room,
  RoomMessage,
  DeviceSettings,
  DictionaryEntry,
  HistoryItem,
  KeyCheck,
  LocalModels,
  MainEvent,
  MicAccess,
  ModelCatalog,
  ModelProvider,
  ProviderKind,
  ProviderModel,
  ProviderPatch,
  ProviderTest,
  ModelsStatus,
  ServicesStatus,
  Status,
  StatsSummary,
  WireAttachment,
} from "./types";

/** Calls `method` of the core. Only the methods of the main window go through (commands.rs). */
export function core<T>(method: string, params: object = {}): Promise<T> {
  return invoke<T>("core_call", { method, params });
}

// ---------- status ----------

export const statusGet = (): Promise<Status> => core("status.get");
export const homeOverview = (): Promise<HomeOverview> => core("home.overview", {
  tz: Intl.DateTimeFormat().resolvedOptions().timeZone,
  ui_language: document.documentElement.lang || "zh-TW",
});

/** Asks OpenRouter again; answers with the `openrouter` part of the status. */
export const statusCheckOpenrouter = (): Promise<Status["openrouter"]> => core("status.check_openrouter");

// ---------- usage and prices ----------

/** The numbers of the home page: words, time, speed, the last 14 days. */
export const statsSummary = (days?: number): Promise<StatsSummary> =>
  core("stats.summary", days === undefined ? {} : { days });
/** Daily local usage, without conversation contents or provider credentials. */
export async function usageAnalytics(params: UsageRequest): Promise<UsageAnalytics> {
  const [report,members] = await Promise.all([core<UsageAnalytics>("usage.analytics",params),membersList().catch(() => null)]);
  return !report.members ? report : {...report,members:report.members.map(row => {
    const member=members?.members?.find(member=>member.id===row.id);
    return {...row,name:member?.name ?? row.name,avatar_url:member?.avatar_url};
  })};
}
/** Every OpenRouter model Raphael could use, with its price and what each role can make of it. */
export const modelsCatalog = (): Promise<ModelCatalog> => core("models.catalog");
export const brainsCli = (): Promise<{ clis: CliBrain[] }> => core("brains.cli");
export const brainsCliLogin = (id: CliBrainId): Promise<{ started: boolean; reconnected?: boolean }> => core("brains.cli_login", { id });
export const brainsCliDisconnect = (id: CliBrainId): Promise<{ disconnected: true }> => core("brains.cli_disconnect", { id });

// Model provider connections. Keys are write-only and are kept by the core's vault.
export const providersKinds = (): Promise<{ kinds: ProviderKind[] }> => core("providers.kinds");
export const providersList = (): Promise<{ providers: ModelProvider[] }> => core("providers.list");
export const providersAdd = (patch: ProviderPatch & { kind: string }): Promise<{ provider: ModelProvider }> => core("providers.add", patch);
export const providersUpdate = (patch: ProviderPatch & { id: string }): Promise<{ provider: ModelProvider }> => core("providers.update", patch);
export const providersRemove = (id: string): Promise<{ removed: boolean }> => core("providers.remove", { id });
export const providersTest = (id: string): Promise<ProviderTest> => core("providers.test", { id });
export const providersModels = (id: string): Promise<{ models: ProviderModel[] }> => core("providers.models", { id });

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
  conversation_id?: number;
  around_id?: number;
  limit?: number;
  /** Only turns older than this id: the cursor of the next page. */
  before_id?: number;
  /** Whose thread: a member's id; absent is Raphael's (0), the core's default. */
  member_id?: number;
}

/** The turns of the conversations, newest first. */
export const agentHistoryList = (q: AgentHistoryQuery = {}): Promise<{ turns: AgentTurn[] }> => core("agent.history.list", q);
/** Forgets every turn; says how many there were. */
export const agentHistoryClear = (): Promise<{ deleted: number }> => core("agent.history.clear");
/** Stops the background task `taskId` (the one that runs, or one that waits); says whether there was one to stop. */
export const workerCancel = (taskId: number): Promise<{ cancelled: boolean }> => core("worker.cancel", { task_id: taskId });
/** Runs a task that did not finish again, as a new task; its id. */
export const workerRerun = (taskId: number): Promise<{ task_id: number }> => core("worker.rerun", { task_id: taskId });
/** The answered turns whose words hold `query`, best first, over the whole history, in the shape of `agentHistoryList`. */
export const agentHistorySearch = (query: string, memberId = 0, conversationId?: number): Promise<{ turns: AgentTurn[] }> =>
  core("agent.history.search", { query, limit: 100, member_id: memberId, ...(conversationId !== undefined ? { conversation_id: conversationId } : {}) });

/**
 * Asks Raphael in writing (the composer of the 拉斐爾 page). The question goes the way a spoken one
 * goes once its words are heard, with the source `app`; this settles when its turn is over (its
 * answer is in the history, or it failed), and rejects when Raphael is busy with another. The
 * images and PDFs of the composer go with it as `attachments` (`agent.ask`'s, in base64), and only
 * when there are some: a question with files may have no words. `memberId` asks a member in its
 * own thread instead of Raphael (`agent.ask`'s `member_id`); it goes only when it is not 0.
 * `destination: "quick"` names Raphael's quick conversation, which the core never picks by default.
 */
export const agentAskText = (text: string, attachments: WireAttachment[] = [], memberId = 0, options: { requestId?: string; replaceTurnId?: number; quote?: import("./types").MessageQuote; conversationId?: number; destination?: "quick" } = {}): Promise<void> =>
  invoke("agent_ask_text", {
    text,
    ...(attachments.length > 0 ? { attachments } : {}),
    ...(memberId ? { memberId } : {}),
    ...options,
  });
/** Stops and removes exactly one owner's request, including a request still on its way. */
export const agentRetract = (sessionId: string, memberId: number, turnId?: number): Promise<void> =>
  invoke("agent_retract", { sessionId, memberId, ...(turnId !== undefined ? { turnId } : {}) });
/** Stop one typed task tree while retaining completed actions and history. */
export const agentStop = (sessionId: string, memberId: number): Promise<unknown> => core("agent.history.stop", {session_id:sessionId,member_id:memberId});
/** The YES/NO question that waits for an answer now, if one does. */
export const agentQuestionOpen = (): Promise<AgentQuestion | null> => invoke("agent_question_open");
/**
 * Answers the question `id` from the page, as the overlay's buttons do; the window must have the
 * focus. `always` (with `approved`) is the third answer, 「一律允許」, and is sent only when true.
 */
export const agentQuestionAnswer = (id: string, approved: boolean, always = false, drafts?: MemberDraft[], groups?: GroupDraft[]): Promise<void> =>
  invoke("agent_question_answer", {
    id,
    approved,
    ...(always ? { always: true } : {}),
    ...(drafts !== undefined ? { drafts } : {}),
    ...(groups !== undefined ? { groups } : {}),
  });

/**
 * Tells the app whether the 拉斐爾 page is what the window shows (no settings over it, no guide in
 * its place). While it is and the window is in front, a question to Raphael is shown on the page
 * alone: its voice mode, the conversation and the card, and nothing at the bottom right.
 */
export const agentPageShown = (shown: boolean): Promise<void> => invoke("agent_page_shown", { shown });
/** A thread's voice button uses the key's recording flow, with its recipient fixed at the press. */
export const agentVoice = (edge: "down" | "up", memberId = 0): Promise<void> =>
  invoke("window_hotkey", { kind: edge === "down" ? "agent_down" : "agent_up", ...(memberId === 0 ? {} : { memberId }) });

// ---------- members, their groups and their routines (the 拉斐爾 page) ----------

/** Raphael (member 0, his name in `uiLanguage`) and the members, and the groups. */
export const membersList = (uiLanguage?: string): Promise<MembersList> => core("members.list", uiLanguage ? { ui_language: uiLanguage } : {});
/**
 * Adds a member; the core answers with it and, for a member of a group with places to look, the
 * routine it offers (「每天早上的進度」, in `ui_language`), which only `routinesCreate` makes: the
 * page offers it to the owner.
 */
export const membersCreate = (member: NewMember): Promise<{ member: Member; routine_offer: RoutineOffer | null }> =>
  core("members.create", member);
/** Changes a member (never Raphael: the core refuses id 0 with the kind `raphael`). */
export const membersUpdate = (id: number, patch: MemberPatch): Promise<{ member: Member }> => core("members.update", { id, ...patch });
/** Deletes a member with its conversation, its memory and its routines. The page asks the owner first. */
export const membersDelete = (id: number): Promise<{ deleted: boolean }> => core("members.delete", { id });
/** A picture of this PC for a member: name, type and bytes in base64, checked by the core as the composer's images are. */
export const membersSetAvatar = (id: number, file: WireAttachment): Promise<{ member: Member }> => core("members.set_avatar", { id, ...file });
/** The images of the presets folder (`<app data>\avatars\presets`), none when it is not there. */
export const membersPresets = (): Promise<{ presets: AvatarPreset[] }> => core("members.presets");
/** The owner read the thread of member `id`: it is no longer unread. */
export const membersRead = (id: number): Promise<unknown> => core("members.read", { id });
/** The thread on screen now (null for none): what arrives there is no news. */
export const membersViewing = (id: number | null): Promise<unknown> => core("members.viewing", { id });
/** Raphael's own tools a member may be given, by id, and the connections. */
export const membersToolChoices = (): Promise<{ tools: string[]; connections: { id: string; name: string; enabled: boolean }[] }> =>
  core("members.tool_choices");

export const groupsCreate = (name: string): Promise<{ group: MemberGroup }> => core("groups.create", { name });
export const groupsUpdate = (id: number, patch: Partial<Pick<MemberGroup, "name" | "position" | "sources">>): Promise<{ group: MemberGroup }> =>
  core("groups.update", { id, ...patch });
/** Deletes a group; its members are 未分組 from then on. */
export const groupsDelete = (id: number): Promise<{ deleted: boolean }> => core("groups.delete", { id });

export const routinesList = (memberId?: number): Promise<{ routines: Routine[] }> => core("routines.list", memberId === undefined ? {} : { member_id: memberId });
export const routinesCreate = (
  routine: Pick<Routine, "member_id" | "title" | "prompt" | "schedule"> & { enabled?: boolean },
): Promise<{ routine: Routine }> => core("routines.create", routine);
export const routinesUpdate = (
  id: number,
  patch: Partial<Pick<Routine, "title" | "prompt" | "schedule" | "enabled">>,
): Promise<{ routine: Routine }> => core("routines.update", { id, ...patch });
export const routinesDelete = (id: number): Promise<{ deleted: boolean }> => core("routines.delete", { id });
/** The routine the core would offer member `memberId` now (its group's places), in `uiLanguage`; null for none. */
export const routinesTemplate = (memberId: number, uiLanguage?: string): Promise<{ offer: RoutineOffer | null }> =>
  core("routines.template", uiLanguage ? { member_id: memberId, ui_language: uiLanguage } : { member_id: memberId });

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

// ---------- the services of Raphael's tools (Settings › 拉斐爾 › 服務) ----------

/** Who searches the web, reads pages and PDFs, and looks at images; whether each keyed service has its key. Never a key. */
export const servicesStatus = (): Promise<ServicesStatus> => core("services.status");
/** Keeps `key` for `service` in the vault (Windows Credential Manager). The key never comes back. */
export const servicesSetKey = (service: string, key: string): Promise<{ ok: boolean }> => core("services.set_key", { service, key });
/** Deletes the key of `service` from the vault. */
export const servicesClearKey = (service: string): Promise<{ ok: boolean }> => core("services.clear_key", { service });

/** Speaks `text` (a short line, at most 200 characters) in the voice that is set up; `kind` says why it could not. */
export const ttsPreview = (text: string): Promise<{ ok: boolean; kind?: string }> => core("tts.preview", { text });

// ---------- connections (the plugins and the store) ----------

type ConnectorReply<T> = T | { ok: false; error: string };
/** Connector RPCs return typed failures; never surface provider payloads or keys. */
async function connectorCall<T>(method: string, params: object = {}): Promise<T> {
  const result = await core<ConnectorReply<T>>(method, params);
  if (result && typeof result === "object" && "ok" in result && result.ok === false) throw new Error(result.error);
  return result as T;
}
export const connectorsStatus = (): Promise<import("./plugins-data").ConnectorStatus> => connectorCall("connectors.status", { refresh: false });
export const connectorsCatalog = (params: { search?: string; category?: string; cursor?: string }): Promise<{ items: import("./plugins-data").Toolkit[]; next_cursor?: string | null }> => connectorCall("connectors.catalog", { ...params, limit: 50 });
export const connectorsCategories = (): Promise<{ items: Array<{ id: string; name: string }> }> => connectorCall("connectors.categories");
export const connectorsTools = (id: string): Promise<{ items: import("./plugins-data").ConnectorTool[] }> => connectorCall("connectors.tools", { id });
export const connectorsSetTools = (id: string, changes: { disabled_tools?: string[]; enabled?: boolean }): Promise<unknown> => connectorCall("connectors.set_tools", { id, ...changes });
export const connectorsConnect = (toolkit: string): Promise<{ connected: boolean; error?: string }> => connectorCall("connectors.connect", { toolkit });
export const connectorsGoogleConnect = (): Promise<{ connected: boolean; error?: string }> => connectorCall("connectors.google.connect");
export const connectorsDisconnect = (id: string): Promise<unknown> => connectorCall("connectors.disconnect", { id });
export const connectorsSetKey = (key: string): Promise<{ configured: boolean }> => connectorCall("connectors.set_key", { key });

export const toolsetsList = (): Promise<{ groups: ConnectionGroup[] }> => core("toolsets.list");
export const integrationsCatalog = (): Promise<{ services: StoreService[] }> => core("integrations.catalog");
/** Adds a service of the store, or signs in to a connected one again (`id`). Waits for the login in the browser: minutes. */
export const integrationsConnect = (target: { catalog_id: string } | { id: string }): Promise<ConnectResult> =>
  core("integrations.connect", target);
/** Ends the wait for a login; the call that waits then answers `cancelled`. */
export const integrationsConnectCancel = (): Promise<{ cancelled: boolean }> => core("integrations.connect_cancel");
/** Connects a server of the user's own. The values of its variables and headers go to the vault and never come back. */
export const integrationsAdd = (server: NewConnection): Promise<{ server: { id: string } }> => core("integrations.add", server);
export const integrationsUpdate = (id: string, server: NewConnection): Promise<{ server: { id: string } }> => core("integrations.update", { ...server, id });
export const integrationsTest = (server: (NewConnection & { id?: string }) | { id: string }): Promise<import("./types").McpTest> => core("integrations.test", server);
export const integrationsPreview = (text: string): Promise<import("./types").McpPreview> => core("integrations.preview", { text });
export const integrationsDiscover = (): Promise<import("./types").McpPreview> => core("integrations.discover");
export const integrationsImport = (ids: string[]): Promise<unknown> => core("integrations.import", { ids });
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

/** Opens a fixed settings/login link or registered provider documentation. */
export const openLink = (url: string): Promise<void> => invoke("open_link", { url });
/** Opens a chat http(s) address through the shell's scheme and credential checks. */
export const openExternalUrl = (url: string): Promise<void> => invoke("open_external_url", { url });


export const deviceSettingsGet = (): Promise<DeviceSettings> => invoke("device_settings_get");
export const deviceSettingsSet = (patch: Partial<DeviceSettings>): Promise<DeviceSettings> =>
  invoke("device_settings_set", { patch });
/** Takes one app off the always-allowed list of 電腦操作, under the app's lock on the settings. */
export const deviceComputerAppRemove = (exe: string): Promise<DeviceSettings> =>
  invoke("device_computer_app_remove", { exe });
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

/** Open only an opaque stored attachment, never a path supplied by a page. */
export const openAttachment = (id: string): Promise<void> => invoke("open_attachment", { id });

// Retained execution metrics; exporting always requests the entire selected period.
export async function usageRuns(params: RunRequest): Promise<RunReport> {
  const [report, members] = await Promise.all([core<RunReport>("usage.runs", params), membersList().catch(() => null)]);
  return { ...report, ...(report.groups ? { groups: { ...report.groups, member: report.groups.member.map(group => {
    const member = members?.members.find(member => member.name === group.label || member.id === 0 && group.label === "Raphael");
    return { ...group, member_id: member?.id ?? (group.label === "Raphael" ? 0 : undefined), avatar_url: member?.avatar_url };
  }) } } : {}) };
}
export const usageExport = (params: RunRequest): Promise<{ csv: string }> => core("usage.export", params);
export const usageAllowances = (): Promise<CliAllowances> => core("usage.allowances");
export const saveUsageCsv = (csv: string): Promise<boolean> => invoke("save_usage_csv", { csv });
// Group threads reuse the same worker and safety gate as individual conversations.
export const routineRuns = (routineId: number): Promise<{ runs: RoutineRun[]; total?: number }> => core("routines.runs", { routine_id: routineId });
export const roomsList = (): Promise<{ rooms: Room[] }> => core("rooms.list");
export const roomsCreate = (room: { name?: string; member_ids: number[]; group_id?: number | null; is_team?: boolean; default_responder?: string; request_id?: string }): Promise<{ room: Room }> => core("rooms.create", room);
export const roomsDelete = (roomId: number): Promise<{ deleted: boolean }> => core("rooms.delete", { room_id: roomId });
export const roomsUpdate = (roomId: number, patch: { name?: string; member_ids?: number[]; default_responder?: string; roles?: Record<string, string> }): Promise<{ room: Room }> => core("rooms.update", { room_id: roomId, ...patch });
export const roomTranscribe = (data: string, uiLanguage: string): Promise<{ text: string }> => core("rooms.transcribe", { data, ui_language: uiLanguage });
export const roomMessages = (roomId: number, before?: number): Promise<{ messages: RoomMessage[]; exchange?: import("./types").RoomExchange | null; handoffs?: import("./types").HandoffView[] }> => core("rooms.messages", { room_id: roomId, before });
export const roomSend = (roomId: number, text: string, requestId: string, uiLanguage: string, attachments: WireAttachment[] = [], replyTo?: number): Promise<{ message_id: number; duplicate: boolean }> => core("rooms.send", { room_id: roomId, text, request_id: requestId, ui_language: uiLanguage, ...(attachments.length ? { attachments } : {}), ...(replyTo !== undefined ? { reply_to: replyTo } : {}) });
export const roomStop = (roomId: number): Promise<{ stopped: boolean }> => core("rooms.stop", { room_id: roomId });
export const keepAwakeGet = (): Promise<boolean> => invoke("schedule_keep_awake_get");
export const keepAwakeSet = (enabled: boolean): Promise<boolean> => invoke("schedule_keep_awake_set", { enabled });
// Member profile resources use the same member boundary as conversations.
export const membersSkillsList = (memberId: number): Promise<{ skills: import("./types").MemberSkill[] }> => core("members.skills.list", { member_id: memberId });
export const membersSkillsImport = (memberId: number, source: string): Promise<{ skill: import("./types").MemberSkill; skills?: import("./types").MemberSkill[] }> => core("members.skills.import", { member_id: memberId, source });
export const membersSkillsUpdate = (memberId: number, id: number, enabled: boolean): Promise<unknown> => core("members.skills.update", { member_id: memberId, id, enabled });
export const membersSkillsDelete = (memberId: number, id: number): Promise<unknown> => core("members.skills.delete", { member_id: memberId, id });
export const membersCommandsList = (memberId: number): Promise<{ commands: import("./types").MemberCommand[] }> => core("members.commands.list", { member_id: memberId });
export const membersCommandsSave = (memberId: number, command: Omit<import("./types").MemberCommand, "id"> & { id?: number }): Promise<unknown> => core("members.commands.save", { member_id: memberId, ...command });
export const membersCommandsDelete = (memberId: number, id: number): Promise<unknown> => core("members.commands.delete", { member_id: memberId, id });
export const membersVoices = (): Promise<{ voices: import("./types").MemberVoice[]; configured: boolean }> => core("members.voices");
