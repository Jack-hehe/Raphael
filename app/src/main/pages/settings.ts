// Settings: eight tabs in three groups: basics, integrations and account. Search jumps to a row
// of any tab. Models & usage has two views, with legacy links selecting the matching view.
// This file holds what the panel knows and what it does; settings-view.ts draws each tab
// from a snapshot of it. A change takes effect at once; there is no save button. The settings of
// the brain (limit, models, languages, speech-to-text, how Raphael answers) go to the core with
// `settings.set`; the settings of this computer (hotkeys, microphone, emblem, theme, autostart) go
// to the Rust side with `device_settings_set`. What is always wanted (pausing the music while
// Raphael listens, learning from corrections) has no switch: it is simply on.

import { isUiLocale, onLocaleChange, t } from "../i18n";
import { createOwnerControls } from "../owner-settings";

import { getVersion } from "@tauri-apps/api/app";
import * as api from "../api";
import { createHiddenMembers } from "../hidden-members";
import { openActions } from "../member-column";
import "../member-panel.css";
import "../settings-polish.css";
import "../connections.css";
import "../services.css";
import "../providers.css";
import "../messaging.css";
import "../memory.css";
import { createMemory } from "../memory-view";
import "../safety-rules.css";
import { createRules } from "../safety-rules";
import { createConnections } from "../connections";
import { createMessaging } from "../messaging";
import { DICTATION_MODES, isDictationMode } from "../dictation";
import { describeError, keyReasonText, parseLimit } from "../format";
import { wireKeyField } from "../keyfield";
import {
  type LanguageGroup,
  chooseLanguageVariant,
  languagePanelHTML,
  languageSummary,
  toggleTranscriptionLanguage,
} from "../languages";
import { createHotkeyRecording } from "../hotkey-recording";
import { isTopLayer, registerDismissal } from "../dismissals";
import { type ModelPicker, type ModelPickerLook, type PickerRole, mountModelPicker, replacementNotices, withFavorite } from "../model-picker";
import { createServices } from "../services";
import { createCliBrains } from "../cli-brains";
import { createProviders } from "../providers";
import { mountUsageAnalytics, type UsageAnalyticsMount } from "../usage-analytics";
import { MonitorSwitch } from "../monitor";
import type { AppContext, AudioDevices, BrainSettings, DeviceSettings, LocalModel, MainEvent, ModelCatalog, ModelRole, Page, SettingsPane, Status } from "../types";
import {
  type Choice,
  type MenuAction,
  closeMenu,
  esc,
  ico,
  iconBtn,
  menuIsOpenFor,
  openMenu,
  openMenuOnArrow,
  openPopover,
  placeMenu,
  replay,
  selText,
  setMeter,
  swapHTML,
  toaster,
} from "../ui";
import { AUTO_MICROPHONE } from "../microphone";
import {
  APPEARANCES,
  HOTKEY_ROWS,
  agentCity,
  quickPickerLook,
  quickEffortChoices,
  quickEffort,
  WORKER_COST_CAPS,
  workerCostCap,
  MODEL_ROWS,
  canonicalPane,
  PANES,
  type SearchHit,
  SIZES,
  type SettingsView,
  type ModelsTab,
  UI_LANGUAGES,
  languagesOf,
  loadingHTML,
  settingsMicrophones,
  modelPickerLook,
  visionPickerLook,
  paneHTML,
  paneListHTML,
  localView,
  priceTexts,
  retentionChoices,
  retentionOf,
  searchResultsHTML,
  searchSettings,
  sttEngine,
  sttModel,
} from "./settings-view";

interface FocusStop { focus(): void }

/** A retention the dropdown can send: a whole number of days. */
const RETENTION_VALUES = /^[1-9]\d*$/;

/**
 * Keep settings and the window controls in one loop without entering the inert main page: the
 * panel first and the window controls last, as they are in the document. `start` is where the
 * panel opens with the focus (its heading), which has nothing before it either.
 */
export function trapSettingsFocus(
  event: Pick<KeyboardEvent, "shiftKey" | "preventDefault">,
  items: readonly FocusStop[],
  focused: FocusStop | null,
  start: FocusStop | null = null,
): void {
  if (items.length === 0) return;
  const first = items[0];
  const last = items[items.length - 1];
  if (event.shiftKey && (focused === first || (start !== null && focused === start))) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && focused === last) {
    event.preventDefault();
    first.focus();
  }
}

/**
 * Whether a key pressed while the settings are open is theirs: it comes from the panel or the
 * window's own controls (`owners`), or from `nowhere`, the body and the document, where the focus
 * falls when the element that had it is drawn away. The page under the panel is inert, so a key
 * from nowhere can only be meant for the settings.
 */
export function ownsSettingsKey(target: Node | null, owners: readonly (Node | null | undefined)[], nowhere: readonly (Node | null)[]): boolean {
  if (target === null) return false;
  return nowhere.includes(target) || owners.some((owner) => owner?.contains(target) === true);
}

/**
 * Draws the tab list again with `paint` and gives the focus back to the tab that had it, now a new
 * button for the same tab: without it the focus falls to the body when the list is drawn anew.
 * A list that did not have the focus does not take it.
 */
export function repaintKeepingFocus(list: HTMLElement, active: Element | null, paint: () => void): void {
  const pane = active !== null && list.contains(active) ? (active as HTMLElement).dataset?.pane : undefined;
  paint();
  // The tabs' ids are plain words (`SettingsPane`), so the selector needs no escaping.
  if (pane !== undefined) list.querySelector<HTMLElement>(`[data-pane="${pane}"]`)?.focus();
}

/** A dropdown's menu: what it offers, what is chosen, what choosing does, small print above the choices and actions below them. */
interface MenuSpec { choices: Choice[]; current: string; pick: (value: string) => void; head?: string; actions?: MenuAction[] }

/** The settings panel, which also says which tab is open. */
export interface SettingsPage extends Page {
  /** The tab that is open; while the panel is closed, the one it was left at. */
  pane(): SettingsPane;
}
/** How long 「移除金鑰」 waits for its second press. */
const KEY_REMOVAL_CONFIRM_MS = 5000;
/** How long a clear of 資料 waits for its second press. */
const CLEAR_CONFIRM_MS = 5000;
/** How long a row the search jumped to stays lit. */
const FOUND_MS = 1600;


export function createSettings(app: AppContext): SettingsPage {
  const el = document.createElement("div");
  el.className = "modal-scrim";
  el.dataset.page = "settings";
  el.hidden = true;
  el.innerHTML =
    `<div class="modal" role="dialog" aria-modal="true" aria-labelledby="set-title">` +
    `<nav class="modal-nav" aria-label="${esc(t("nav.settings"))}">` +
    `<label class="field set-search">${ico("search")}<input id="set-search" type="text" autocomplete="off" spellcheck="false"></label>` +
    `<div class="set-list"></div>` +
    `</nav>` +
    `<div class="modal-body"></div>` +
    `${iconBtn("x", t("common.close"), ' data-act="close"', "modal-x")}` +
    `</div>`;
  const hiddenMembers = createHiddenMembers();
  const body = el.querySelector<HTMLElement>(".modal-body")!;
  const searchField = el.querySelector<HTMLInputElement>("#set-search")!;
  const navList = el.querySelector<HTMLElement>(".set-list")!;
  let pane: SettingsPane = "general";
  let modelsTab: ModelsTab = "models";
  const modelsScroll: Record<ModelsTab, number> = { models: 0, usage: 0 };
  const usageHost = document.createElement("div");
  usageHost.dataset.usageHost = "";
  let usage: UsageAnalyticsMount | null = null;
  let usageRefresh: Promise<void> | null = null;
  let usageAgain = false;
  /** What 「搜尋設定」 found for what is typed in it; the tabs are listed again when it is empty. */
  let hits: SearchHit[] = [];
  /** A row the search chose, to be lit once its tab is drawn with its rows (not while it is being read). */
  let pendingJump: SearchHit | null = null;

  let brain: BrainSettings | null = null;
  const ownerControls = createOwnerControls(settings => { brain = settings; if (visible) render(); });
  let hasKey = false;
  let openrouter: Status["openrouter"] | null = null;
  let spend: Status["spend"] | null = null;
  /** OpenRouter's catalog of models, with their prices: being read (`loading`), read, or not to be had (`failed`). */
  let catalog: ModelCatalog | "loading" | "failed" = "loading";
  let catalogLoads = 0;
  /** The pickers of the model rows (and of 服務's 看圖片) on the tab that is drawn (settings-view.ts draws their buttons). */
  const pickers = new Map<PickerRole | "quick", ModelPicker>();
  /** The speech models of this computer, and the download of one that is under way. */
  let localModels: LocalModel[] | "loading" | "failed" = "loading";
  let localDownload: { name: string; done: number; total: number } | null = null;
  let checkingOpenrouter = false;
  let devices: AudioDevices | null = null;
  let altgr = false;
  let version = "";
  /** The key field is shown although there is a key (「變更」). */
  let replacingKey = false;
  let keyBusy = false;
  /** 「移除金鑰」 was pressed once: pressing it again, within a few seconds, removes the key. */
  let confirmingKeyRemoval = false;
  let keyRemovalTimer: ReturnType<typeof setTimeout> | undefined;
  let keyError: (() => string) | null = null;
  /** What was typed in the limit field while it is not a limit, and what is wrong with it. */
  let limitDraft: string | null = null;
  let limitError: (() => string) | null = null;
  /** The same for the weather city. */
  let cityDraft: string | null = null;
  let cityError: (() => string) | null = null;
  /** A shortcut being recorded (「變更」): the row is drawn again when it is over. */
  const recording = createHotkeyRecording(app, () => {
    if (visible) render();
  });
  /** The languages the core offers (`languages.list`); null while unknown or when it cannot say. */
  let languageGroups: LanguageGroup[] | null = null;
  let languagePanel: HTMLElement | null = null;
  /** The search for microphones that 「重新搜尋麥克風」 started is under way. */
  let searchingMicrophones = false;
  let visible = false;
  let stopOutside = (): void => {};
  let loading = false;
  let loads = 0;
  /** In 資料, the clear that was pressed once and waits for its second press. */
  let confirmingClear: "dictation" | "conversation" | null = null;
  let clearTimer: ReturnType<typeof setTimeout> | undefined;
  /** The 「連接」 tab: it keeps its own state and asks to be drawn again. 「試試看」 leaves for the 拉斐爾 page with a draft. */
  const connections = createConnections({
    root: el,
    visible: () => visible && pane === "connections",
    redraw: () => render(),
    tryPrompt: (text) => app.go("agent", `draft:${text}`),
  });
  app.onEvent((event) => connections.followEvent(event));
  /** Telegram, in the 「訊息平台」 tab: it keeps its own state too. */
  const messaging = createMessaging({ visible: () => visible && pane === "messaging", redraw: () => render() });
  /**
   * The memory, in the 「拉斐爾」 tab: 「共同記憶」, which every agent shares, and Raphael's own (its
   * notes and the summary of his conversation). Each list reads the other again when a note moves.
   */
  const memoryHost = { visible: () => visible && pane === "agent", redraw: () => render(), undo: app.undo };
  const memoryShared = createMemory({ ...memoryHost, space: "shared", onMoved: () => void memory.load() });
  const memory = createMemory({ ...memoryHost, space: "own", onMoved: () => void memoryShared.load() });
  const memories = [memoryShared, memory];
  app.onEvent((event) => {
    if (event.type !== "MemoryChanged" || !memoryHost.visible()) return;
    if (event.space !== "own") void memoryShared.load();
    if (event.space !== "shared" && event.member_id === 0) void memory.load();
  });
  /** The owner's safety rules, in the 「拉斐爾」 tab: what Raphael asks about first, does at once, or never does. */
  const rules = createRules({ visible: () => visible && pane === "agent", redraw: () => render(), undo: app.undo });
  /** 服務, in the 「拉斐爾」 tab: who does the work of Raphael's tools. Choosing is a change of the brain's settings. */
  const services = createServices({ root: el, visible: () => visible && pane === "agent", redraw: () => render(), brain: () => brain, changeSetting: (key, value) => changeBrain(key, value) });
  const cliBrains = createCliBrains({ visible: () => visible, redraw: () => render() });
  const providers = createProviders({ root: el, visible: () => visible && pane === "models" && modelsTab === "models", redraw: () => render() });
  app.onEvent((event) => {
    providers.followEvent(event);
    if (event.type === "SettingsChanged" && visible) void api.settingsGet().then(result => { brain = result.settings; render(); }, () => {});
    cliBrains.followEvent(event);
    if (event.type === "ProvidersChanged" && visible) void loadCatalog();
    if (event.type === "ProvidersChanged" || event.type === "AgentTurn" || event.type === "QuotaChanged" || event.type === "RoutinesChanged" || event.type === "Dictation" && event.phase === "pasted") refreshUsage();
  });

  // ---------- drawing ----------

  /** What the tabs are drawn from, as the panel knows it now. */
  const view = (): SettingsView => ({
    pane, modelsTab, device: app.device(), brain, hasKey, openrouter, spend, catalog, checkingOpenrouter, devices, altgr, version,
    replacingKey, confirmingKeyRemoval, keyBusy, keyError, limitDraft, limitError, cityDraft, cityError, languageGroups, now: Date.now(),
    connections: connections.view(), messaging: messaging.view(), memory: memory.view(), memoryShared: memoryShared.view(), rules: rules.view(), services: services.view(), providers: providers.view(), cliBrains: cliBrains.view(), confirmingClear, localModels, localDownload,
  });

  /** The item at the foot of the microphones' menu that looks for them again. */
  const searchAction = (anchor: HTMLElement): MenuAction => ({
    label: searchingMicrophones ? t("settings.searchingMicrophones") : t("action.refreshMicrophones"),
    icon: "refresh",
    busy: searchingMicrophones,
    run: () => void searchMicrophones(anchor),
  });

  /** Opens `menu` under `anchor`, or draws it again there. */
  const showMenu = (anchor: HTMLElement, menu: MenuSpec): void =>
    openMenu(anchor, menu.choices, menu.current, menu.pick, menu.head, menu.actions);

  /** The menu of each `sel` (by its `data-k`): what it offers, what is chosen and what choosing does. */
  function menuFor(k: string): MenuSpec | null {
    const v = view();
    const d = v.device;
    switch (k) {
      case "quick-effort":
        return { choices: quickEffortChoices(v), current: quickEffort(v), pick: value => void changeBrain("quick_effort", value) };
      case "mic": {
        const anchor = el.querySelector<HTMLElement>('.sel[data-k="mic"]');
        return {
          choices: settingsMicrophones(v),
          current: d.microphone ?? AUTO_MICROPHONE,
          pick: (value) => void changeDevice({ microphone: value === AUTO_MICROPHONE ? null : value }),
          actions: anchor ? [searchAction(anchor)] : [],
        };
      }
      case "ui-language":
        return {
          choices: UI_LANGUAGES,
          current: d.ui_language,
          pick: (value) => { if (isUiLocale(value)) void changeDevice({ ui_language: value }); },
        };
      case "worker-cap":
        return {
          choices: WORKER_COST_CAPS,
          current: workerCostCap(v),
          pick: (value) => {
            const cap = Number(value);
            if (WORKER_COST_CAPS.some((c) => c.value === value) && cap !== (brain?.worker_cost_cap_usd ?? 0.2)) void changeBrain("worker_cost_cap_usd", cap);
          },
        };
      case "mode":
        return {
          choices: DICTATION_MODES,
          current: d.dictation_mode,
          pick: (value) => {
            if (isDictationMode(value) && value !== app.device().dictation_mode) void changeDevice({ dictation_mode: value });
          },
        };
      case "appearance":
        return {
          choices: APPEARANCES,
          current: d.appearance,
          pick: (value) => {
            const appearance = value as DeviceSettings["appearance"];
            if (APPEARANCES.some((c) => c.value === appearance) && appearance !== app.device().appearance) void changeDevice({ appearance });
          },
        };
      case "retention":
        return {
          choices: retentionChoices(v),
          current: retentionOf(v),
          pick: (value) => {
            if (RETENTION_VALUES.test(value) && value !== retentionOf(view())) void changeBrain("history_retention_days", Number(value));
          },
        };
      case "size":
        return {
          choices: SIZES,
          current: String(d.emblem_size),
          pick: (value) => {
            if (SIZES.some((c) => c.value === value) && value !== String(app.device().emblem_size)) {
              void changeDevice({ emblem_size: Number(value) as DeviceSettings["emblem_size"] });
            }
          },
        };
      default:
        return rules.menuFor(k) ?? connections.menuFor(k);
    }
  }

  /** Shows the prices where they go, without drawing the page again (a key being typed stays). */
  function paintPrices(): void {
    const texts = priceTexts(view());
    el.querySelectorAll<HTMLElement>("[data-price]").forEach((node) => {
      node.textContent = texts[node.dataset.price ?? ""] ?? "";
    });
  }

  /** The button whose menu or panel is open, by what the page calls it. */
  function openAnchorKey(): string | undefined {
    const anchor = Array.from(el.querySelectorAll<HTMLElement>(".sel")).find(menuIsOpenFor);
    return anchor?.dataset.k;
  }

  function destroyUsage(): void {
    usage?.destroy(); usage = null; usageRefresh = null; usageAgain = false;
    usageHost.remove();
  }

  /** Coalesce activity bursts and wait for the first reading before refreshing it. */
  function refreshUsage(): void {
    if (!usage || !visible || pane !== "models" || modelsTab !== "usage") return;
    usageAgain = true;
    if (usageRefresh) return;
    const current = usage;
    usageRefresh = (async () => {
      await current.ready;
      while (usage === current && usageAgain && visible && pane === "models" && modelsTab === "usage") {
        usageAgain = false;
        await current.refresh();
      }
    })().finally(() => { if (usage === current) usageRefresh = null; });
  }

  function render(): void {
    const active = document.activeElement as HTMLElement | null;
    const usageFocus = active && usageHost.contains(active) ? active : null;
    const focused = active?.dataset?.k;
    const focusedId = active?.id;
    const ownerFocus = active?.dataset?.owner;
    const ownerDrafts = Array.from(el.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>('[data-owner]:not(:disabled)')).filter(input => input === active).map(input => [input.dataset.owner!, input.value] as const);
    const keyDraft = el.querySelector<HTMLInputElement>("#key-input")?.value;
    const activeLimit = focusedId === "limit-input" ? el.querySelector<HTMLInputElement>("#limit-input")?.value : undefined;
    // What is being typed in 服務's fields stays through a drawing (a status that came in, say): it
    // is carried from the old field to the new one here, and kept nowhere else.
    const serviceDrafts = Array.from(el.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>('.svc-line input, .pv-field input:not([type="file"]), .pv-field textarea')).map((input) => [input.id, input.value] as const);
    // A menu that is open stays open when the page is drawn again (a status that came in, say).
    const openK = openAnchorKey();
    const scroll = body.scrollTop;
    body.setAttribute("aria-busy", String(loading && pane !== "about"));
    // Only what changed moves: a list drawn again stands still, a switch that was turned slides.
    // Analytics owns its controls and filters; keep that live subtree out of page HTML redraws.
    usageHost.remove();
    swapHTML(body, pane !== "about" && loading ? loadingHTML(view()) : paneHTML(view()));
    const slot = body.querySelector<HTMLElement>("[data-usage-host]");
    if (slot) {
      slot.replaceWith(usageHost);
      usage ??= mountUsageAnalytics(usageHost, { usageAnalytics: api.usageAnalytics, usageRuns: api.usageRuns, usageExport: api.usageExport, usageAllowances: api.usageAllowances, saveUsageCsv: api.saveUsageCsv, modelsCatalog: api.modelsCatalog, brainsCli: api.brainsCli });
      if (usageFocus?.isConnected) usageFocus.focus({ preventScroll: true });
    }
    // Before the focus goes back by `data-k`, which the pickers' buttons have too.
    mountPickers();
    for (const [key, value] of ownerDrafts) {
      const input = el.querySelector<HTMLInputElement | HTMLTextAreaElement>(`[data-owner="${key}"]`);
      if (input && !input.disabled) input.value = value;
    }
    if (ownerFocus) el.querySelector<HTMLElement>(`[data-owner="${ownerFocus}"]`)?.focus({ preventScroll: true });
    hiddenMembers.mount(body);
    const keyInput = el.querySelector<HTMLInputElement>("#key-input");
    if (keyInput && keyDraft !== undefined) keyInput.value = keyDraft;
    const limitInput = el.querySelector<HTMLInputElement>("#limit-input");
    if (limitInput && activeLimit !== undefined) limitInput.value = activeLimit;
    for (const [id, value] of serviceDrafts) {
      const input = id ? el.querySelector<HTMLInputElement>(`#${CSS.escape(id)}`) : null;
      if (input && !input.disabled) input.value = value;
    }
    body.scrollTop = scroll;
    el.querySelectorAll<HTMLElement>("[data-pane]").forEach((b) => b.setAttribute("aria-current", b.dataset.pane === pane ? "page" : "false"));

    if (focused) el.querySelector<HTMLElement>(`[data-k="${CSS.escape(focused)}"]`)?.focus();
    else if (focusedId) el.querySelector<HTMLElement>(`#${CSS.escape(focusedId)}`)?.focus();
    if (openK) {
      const anchor = el.querySelector<HTMLElement>(`.sel[data-k="${openK}"]`);
      const menu = menuFor(openK);
      if (anchor && openK === "languages") openLanguages(anchor);
      else if (anchor && menu) showMenu(anchor, menu);
    }
    if (pendingJump && !(loading && pane !== "about")) light(pendingJump);
  }

  // ---------- the tabs and 「搜尋設定」 ----------

  /** The list on the left: the tabs in their groups, or, while something is typed, what the search found. */
  function paintNav(): void {
    // A tab chosen with Enter is drawn anew here: the keyboard stays on it, and Esc still closes.
    repaintKeepingFocus(navList, document.activeElement, () => {
      navList.innerHTML = searchField.value.trim() ? searchResultsHTML(hits) : paneListHTML();
    });
    navList.querySelectorAll<HTMLElement>("[data-pane]").forEach((b) => b.setAttribute("aria-current", b.dataset.pane === pane ? "page" : "false"));
  }

  function translateNav(): void {
    el.querySelector(".modal-nav")!.setAttribute("aria-label", t("nav.settings"));
    searchField.placeholder = t("settings.search");
    searchField.setAttribute("aria-label", t("settings.search"));
    hits = searchSettings(searchField.value);
    paintNav();
  }
  translateNav();

  /** Goes to a tab: what the tab before it had open is dropped, and the new one comes in. */
  function openPane(next: SettingsPane, tab?: ModelsTab): void {
    closeMenu();
    const hadUsage = usage !== null;
    const jumping = pendingJump !== null;
    if (pane === "models") modelsScroll[modelsTab] = body.scrollTop;
    if (next === "usage") modelsTab = "usage";
    else if (tab) modelsTab = tab;
    pane = canonicalPane(next);
    if (pane === "general") void hiddenMembers.load();
    if (pane !== "models") destroyUsage();
    if (pane === "connections") void connections.load();
    else connections.reset();
    if (pane === "messaging") void messaging.load();
    else messaging.reset();
    if (pane === "agent") for (const list of memories) void list.load();
    else for (const list of memories) list.reset();
    if (pane === "agent") void rules.load();
    else rules.reset();
    services.reset();
    providers.reset();
    if (pane === "agent") void services.load();
    if (pane === "models" && modelsTab === "models") void providers.load();
    clearTimeout(clearTimer);
    confirmingClear = null;
    syncMonitor();
    body.scrollTop = 0;
    render();
    if (!jumping && pane === "models") body.scrollTop = modelsScroll[modelsTab];
    if (hadUsage && pane === "models" && modelsTab === "usage") refreshUsage();
    paintNav();
    replay(body, "pane-in");
  }

  function switchModelsTab(next: ModelsTab): void {
    if (next === modelsTab) return;
    const hadUsage = usage !== null;
    closeMenu();
    modelsScroll[modelsTab] = body.scrollTop;
    modelsTab = next;
    if (next === "usage") providers.reset();
    render();
    body.scrollTop = modelsScroll[next];
    body.querySelector<HTMLElement>(`[data-models-tab="${next}"]`)?.focus({ preventScroll: true });
    if (next === "models") void providers.load();
    else if (hadUsage) refreshUsage();
  }

  /** Goes where a hit of the search is: its tab, then its row, which is lit for a moment and gets the focus. */
  function jump(hit: SearchHit): void {
    searchField.value = "";
    hits = [];
    pendingJump = hit.label ? hit : null;
    openPane(hit.pane, hit.label && canonicalPane(hit.pane) === "models" ? hit.pane === "usage" ? "usage" : "models" : undefined);
    if (!hit.label) el.querySelector<HTMLElement>("#set-title")?.focus();
  }

  /** Lights the row a jump was for, if its tab shows it, scrolls it into view and focuses its control. */
  function light(hit: SearchHit): void {
    pendingJump = null;
    if (pane !== canonicalPane(hit.pane)) return;
    if (hit.label === "providers.add") {
      const button = body.querySelector<HTMLButtonElement>('[data-pv="directory"]');
      if (button?.disabled && providers.view().state !== "failed") { pendingJump = hit; return; }
      button?.scrollIntoView({ block: "center" }); button?.focus({ preventScroll: true });
      return;
    }
    const rows = Array.from(body.querySelectorAll<HTMLElement>(".row"));
    const found =
      rows.find((r) => r.querySelector(":scope > .lbl > .t")?.textContent?.trim() === hit.text) ??
      // a capability of 服務 is a row of the rounded list, named by its `.nm`
      Array.from(body.querySelectorAll<HTMLElement>(".cs-item.svc-item")).find((r) => r.querySelector(".nm")?.textContent?.trim() === hit.text);
    if (!found) {
      // A hit that names a whole section (電腦操作) brings its heading into view and lights its list.
      const list = Array.from(body.querySelectorAll<HTMLElement>(".sgroup")).find((g) => g.querySelector(":scope > h2")?.textContent?.trim() === hit.text)?.querySelector<HTMLElement>(":scope > .slist, :scope > .cs-list");
      if (!list) return;
      list.scrollIntoView({ block: "center" });
      list.classList.add("found");
      setTimeout(() => list.classList.remove("found"), FOUND_MS);
      return;
    }
    const control = found.querySelector<HTMLElement>(":scope > .ctl :is(button, input):not([disabled]), :scope > .cs-open");
    control?.focus({ preventScroll: true });
    found.scrollIntoView({ block: "center" });
    found.classList.add("found");
    setTimeout(() => found.classList.remove("found"), FOUND_MS);
  }

  searchField.addEventListener("input", () => {
    hits = searchSettings(searchField.value);
    paintNav();
  });
  searchField.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && hits.length) {
      e.preventDefault();
      jump(hits[0]);
    } else if (e.key === "ArrowDown" && hits.length) {
      e.preventDefault();
      navList.querySelector<HTMLElement>(".set-hit")?.focus();
    } else if (e.key === "Escape" && searchField.value) {
      // Esc empties the search first; the next one closes the settings.
      e.preventDefault();
      searchField.value = "";
      hits = [];
      paintNav();
    }
  });
  navList.addEventListener("keydown", (e) => {
    const items = Array.from(navList.querySelectorAll<HTMLElement>(".set-hit"));
    const i = items.indexOf(document.activeElement as HTMLElement);
    if (i < 0 || (e.key !== "ArrowDown" && e.key !== "ArrowUp")) return;
    e.preventDefault();
    if (e.key === "ArrowUp" && i === 0) searchField.focus();
    else items[Math.min(items.length - 1, Math.max(0, i + (e.key === "ArrowDown" ? 1 : -1)))].focus();
  });

  onLocaleChange(() => {
    translateNav();
    const close = el.querySelector('[data-act="close"]')!;
    close.setAttribute("aria-label", t("common.close"));
    close.setAttribute("title", t("common.close"));
    if (visible) render();
  });

  // ---------- the languages ----------

  /** Opens the one picker of the languages and their variants under the dropdown of the row. */
  function openLanguages(anchor: HTMLElement): void {
    const panel = openPopover(anchor, languagePanelHTML(languageGroups, languagesOf(view())), {
      className: "menu lang-panel",
      role: "dialog",
      label: t("transcription.title"),
      onKey: languageKey,
    });
    languagePanel = panel;
    panel.addEventListener("click", (e) => {
      const item = (e.target as Element).closest<HTMLElement>(".lang-item");
      if (!item) return;
      if (item.dataset.language) void pickLanguage(item.dataset.language, false);
      else if (item.dataset.variant) void pickLanguage(item.dataset.variant, true);
    });
    (panel.querySelector<HTMLElement>('[aria-pressed="true"], .lang-item') ?? panel).focus();
    if (languageGroups === null) void loadLanguages();
  }

  /** Left and right move and select within the current variant's radio group. */
  function languageKey(e: KeyboardEvent): boolean {
    const active = document.activeElement as HTMLElement | null;
    if ((e.key !== "ArrowLeft" && e.key !== "ArrowRight") || !active?.dataset.variant || !languagePanel?.contains(active)) return false;
    const items = Array.from(active.closest('[role="radiogroup"]')!.querySelectorAll<HTMLElement>("[data-variant]"));
    const next = items[(items.indexOf(active) + (e.key === "ArrowRight" ? 1 : items.length - 1)) % items.length];
    next.focus();
    void pickLanguage(next.dataset.variant!, true);
    return true;
  }

  /** Shows the chosen languages on the dropdown, and in the picker if it is open (keeping the focus). */
  function paintLanguages(): void {
    const button = el.querySelector<HTMLElement>('[data-k="languages"]');
    if (!button) return;
    const chosen = languagesOf(view());
    const summary = languageSummary(languageGroups ?? [], chosen);
    button.querySelector("span")!.textContent = summary;
    button.title = summary;
    button.setAttribute("aria-label", t("transcription.label", { p0: summary }));
    if (!languagePanel || !menuIsOpenFor(button)) return;
    const active = document.activeElement as HTMLElement | null;
    let again: string | null = null;
    if (active && languagePanel.contains(active)) {
      if (active.dataset.variant) again = `[data-variant="${CSS.escape(active.dataset.variant)}"]`;
      else if (active.dataset.language) again = `[data-language="${CSS.escape(active.dataset.language)}"]`;
    }
    languagePanel.innerHTML = languagePanelHTML(languageGroups, chosen);
    placeMenu();
    (again ? languagePanel.querySelector<HTMLElement>(again) : null)?.focus();
  }

  /** Chooses or drops a language; the dropdown shows it at once, and goes back if the core refuses. */
  async function pickLanguage(id: string, variant: boolean): Promise<void> {
    const before = languagesOf(view());
    const result = variant
      ? chooseLanguageVariant(languageGroups ?? [], before, id)
      : toggleTranscriptionLanguage(languageGroups ?? [], before, id);
    if (!result.ok) {
      toaster.info(result.reason);
      return;
    }
    if (brain) brain = { ...brain, languages: result.languages };
    paintLanguages();
    try {
      brain = (await api.settingsSet("languages", result.languages)).settings;
    } catch (err) {
      if (brain) brain = { ...brain, languages: before };
      toaster.info(describeError(err));
    }
    if (visible) paintLanguages();
  }

  /** Reads the list of languages (again, when the picker is opened without one). */
  async function loadLanguages(): Promise<void> {
    try {
      languageGroups = (await api.languagesList()).groups;
    } catch {
      return; // the picker says the list cannot be had
    }
    if (visible) paintLanguages();
  }

  // ---------- changes ----------

  async function changeBrain(key: keyof BrainSettings, value: string | number | boolean | string[]): Promise<void> {
    try {
      brain = (await api.settingsSet(key, value)).settings;
      if (spend) spend = { ...spend, limit_usd: brain.monthly_limit_usd };
    } catch (err) {
      toaster.info(describeError(err));
    }
    render();
  }

  /**
   * A cloud model chosen in the speech-to-text picker sets the model, then the engine to the cloud
   * (the local model is kept for a return to this computer). In that order, a model the core refuses
   * leaves the engine as it was.
   */
  async function changeStt(value: string): Promise<void> {
    try {
      if (value !== sttModel(view())) brain = (await api.settingsSet("stt_cloud_model", value)).settings;
      if (sttEngine(view()) !== "cloud") brain = (await api.settingsSet("stt_engine", "cloud")).settings;
    } catch (err) {
      toaster.info(describeError(err));
    }
    render();
  }

  /** Reads the connection and the spend again (after a new key, or 「重新檢查」). */
  async function readStatus(): Promise<void> {
    try {
      const status = await api.statusGet();
      openrouter = status.openrouter;
      spend = status.spend;
    } catch {
      openrouter = null;
    }
  }

  async function checkOpenrouter(): Promise<void> {
    if (checkingOpenrouter) return;
    checkingOpenrouter = true;
    render();
    try {
      await api.statusCheckOpenrouter();
    } catch (err) {
      toaster.info(describeError(err));
    }
    await readStatus();
    checkingOpenrouter = false;
    if (visible) render();
  }

  /**
   * Reads OpenRouter's catalog of models. The page is shown at once with 「價格載入中…」; the prices and
   * the pickers' lists go in when it comes, without drawing the page again (a key being typed stays).
   * A chosen model that OpenRouter no longer lists went back to its default: that is told, and drawn.
   */
  async function loadCatalog(): Promise<void> {
    const mine = loads;
    const request = ++catalogLoads;
    let next: ModelCatalog | "failed";
    try {
      [next] = await Promise.all([api.modelsCatalog(), providers.load()]);
    } catch {
      // keep a catalog read earlier; else say that the prices cannot be had
      next = typeof catalog === "object" ? catalog : "failed";
    }
    if (mine !== loads || request !== catalogLoads || !visible) return;
    catalog = next;
    if (typeof next === "object" && next.replaced.length > 0) {
      if (brain) for (const r of next.replaced) brain = { ...brain, [r.key]: r.default };
      for (const message of replacementNotices(next)) toaster.info(message);
      render();
      return;
    }
    paintPrices();
    for (const [role, picker] of pickers) picker.update(lookFor(role));
  }

  /** What the picker of `role` is drawn from now. */
  const lookFor = (role: PickerRole | "quick"): ModelPickerLook => role === "quick" ? quickPickerLook(view()) : (role === "vision" ? visionPickerLook(view()) : modelPickerLook(view(), role));

  // ---------- the speech models of this computer ----------

  /** Reads the catalogue (`models.local`); one that is not to be had is said in the local tab. */
  async function loadLocalModels(): Promise<void> {
    const mine = loads;
    let next: LocalModel[] | "failed";
    try {
      const listing = await api.modelsLocal();
      next = listing.models;
      // With the cloud in use the core says which model backs it up, which a failed delete may have changed.
      if (brain && brain.stt_engine !== "local" && listing.fallback !== undefined) brain = { ...brain, stt_local_model: listing.fallback };
    } catch {
      next = Array.isArray(localModels) ? localModels : "failed";
    }
    if (mine !== loads || !visible) return;
    localModels = next;
    paintLocal();
  }

  /** The speech-to-text picker and the price under its row follow the local models. */
  function paintLocal(): void {
    paintPrices();
    pickers.get("stt")?.update({ local: localView(view()) });
  }

  const localTitle = (name: string): string => (Array.isArray(localModels) ? localModels.find((m) => m.name === name)?.title : undefined) ?? name;

  /**
   * The user chose a model of the catalogue (and, if it had to be downloaded, said yes to its size).
   * The core downloads it, with progress that arrives as events, and only then makes it the local
   * engine: a download that fails or is cancelled leaves the engine in use as it was.
   */
  async function useLocal(name: string): Promise<void> {
    const model = Array.isArray(localModels) ? localModels.find((m) => m.name === name) : undefined;
    if (model && !model.ready) {
      localDownload = { name, done: 0, total: 0 };
      paintLocal();
    }
    try {
      const result = await api.modelsLocalUse(name);
      if (result.ok) brain = result.settings;
    } catch {
      toaster.info(t("local.failed", { p0: localTitle(name) }));
    }
    localDownload = null;
    try {
      localModels = (await api.modelsLocal()).models;
    } catch {
      // the list stays as it was
    }
    if (visible) paintLocal();
  }

  async function cancelLocal(): Promise<void> {
    try {
      await api.modelsLocalCancel();
    } catch {
      // the download goes on; the user can press the button again
    }
  }

  async function deleteLocal(name: string): Promise<void> {
    try {
      const reply = await api.modelsLocalDelete(name);
      // Deleting the model that backed up the cloud names another, or none, in its place.
      if (brain && reply.fallback !== undefined) brain = { ...brain, stt_local_model: reply.fallback };
    } catch {
      toaster.info(t("local.deleteFailed", { p0: localTitle(name) }));
    }
    await loadLocalModels();
  }

  /** The progress of the download under way. */
  function followDownload(event: MainEvent): void {
    if (event.type !== "ModelProgress" || localDownload?.name !== event.name) return;
    localDownload = { name: event.name, done: event.done, total: event.total };
    pickers.get("stt")?.update({ local: localView(view()) });
  }
  app.onEvent(followDownload);

  /**
   * Makes the pickers of the model rows work, on the buttons settings-view.ts drew, and the 看圖片
   * picker of 服務, whose stars are the answer model's (`visionPickerLook`).
   */
  function mountPickers(): void {
    pickers.clear();
    const v = view();
    const quick = body.querySelector<HTMLElement>('[data-row="quick-model"] .mp-slot');
    if (quick) pickers.set("quick", mountModelPicker(quick, {
      ...quickPickerLook(v),
      onSources: () => openPane("models", "models"),
      onChoose: (id) => void changeBrain("quick_model", id.split("?effort=")[0]),
      onFavorite: (id, on) => void changeFavorite("answer", id, on),
      onCliAction: (id) => {
        const button = document.createElement("button"); button.dataset.cli = id;
        void cliBrains.click(button);
      },
    }));
    const vision = body.querySelector<HTMLElement>('[data-model-picker="vision"]');
    if (vision) {
      pickers.set(
        "vision",
        mountModelPicker(vision, {
          ...visionPickerLook(v),
          onSources: () => openPane("models", "models"),
          onChoose: (id) => void services.chooseVision(id),
          onFavorite: (id, on) => void changeFavorite("answer", id, on),
        }),
      );
    }
    for (const { role } of MODEL_ROWS) {
      const slot = body.querySelector<HTMLElement>(`[data-row]:not([data-row="quick-model"]) [data-model-picker="${role}"]`);
      if (slot) {
        pickers.set(
          role,
          mountModelPicker(slot, {
            ...modelPickerLook(v, role),
            onSources: () => openPane("models", "models"),
            onCliAction: (id) => {
              const button = document.createElement("button"); button.dataset.cli = id;
              void cliBrains.click(button);
            },
            onChoose: (id) => void chooseModel(role, id),
            onFavorite: (id, on) => void changeFavorite(role, id, on),
            onUseLocal: (name) => void useLocal(name),
            onCancelLocal: () => void cancelLocal(),
            onDeleteLocal: (name) => void deleteLocal(name),
          }),
        );
      }
    }
  }

  /** The saving of favourites, one after the other, so that a quick second star is not saved before the first. */
  let savingFavorites: Promise<void> = Promise.resolve();

  /**
   * A star pressed in a picker. The picker shows it at once; here it is saved (the whole map: the core
   * keeps the favourites of every role in one setting). A refusal puts the list back as it was.
   */
  function changeFavorite(role: ModelRole, id: string, on: boolean): Promise<void> {
    const asked = brain?.model_favorites ?? {};
    const next = { ...asked, [role]: withFavorite(asked[role] ?? [], id, on) };
    if (brain) brain = { ...brain, model_favorites: next };
    savingFavorites = savingFavorites.then(async () => {
      try {
        brain = (await api.settingsSet("model_favorites", next)).settings;
      } catch (err) {
        toaster.info(describeError(err));
        if (brain) brain = { ...brain, model_favorites: asked };
      }
      pickers.get(role)?.update({ favorites: brain?.model_favorites?.[role] ?? [] });
      // 看圖片 shows the answer model's stars.
      if (role === "answer") pickers.get("vision")?.update({ favorites: brain?.model_favorites?.answer ?? [] });
      if (role === "answer") pickers.get("quick")?.update({ favorites: brain?.model_favorites?.answer ?? [] });
    });
    return savingFavorites;
  }

  /** A model chosen in a picker (a local speech model is chosen in its own tab: `useLocal`). */
  function chooseModel(role: ModelRole, id: string): Promise<void> {
    if (role === "stt") return changeStt(id);
    return changeBrain(role === "cleanup" ? "cleanup_model" : "agent_model", id);
  }

  async function changeDevice(patch: Partial<DeviceSettings>): Promise<void> {
    try {
      await app.setDevice(patch);
    } catch (err) {
      toaster.info(describeError(err));
    }
    render();
  }

  async function removeComputerApp(exe: string): Promise<void> {
    try {
      await app.removeComputerApp(exe);
    } catch (err) {
      toaster.info(describeError(err));
    }
    render();
  }

  async function submitKey(text: string): Promise<void> {
    const key = text.trim();
    if (!key || keyBusy) return;
    keyBusy = true;
    keyError = null;
    render();
    try {
      const result = await api.secretsSetOpenrouterKey(key);
      if (result.valid) {
        hasKey = true;
        replacingKey = false;
        toaster.info(t("common.keySaved"));
        await readStatus();
      } else {
        keyError = () => keyReasonText(result.reason);
      }
    } catch (err) {
      keyError = () => describeError(err);
    }
    keyBusy = false;
    render();
  }

  /** The first press asks again for a few seconds; the second removes the key. */
  async function removeKey(): Promise<void> {
    if (keyBusy) return;
    clearTimeout(keyRemovalTimer);
    if (!confirmingKeyRemoval) {
      confirmingKeyRemoval = true;
      keyRemovalTimer = setTimeout(() => {
        confirmingKeyRemoval = false;
        if (visible) render();
      }, KEY_REMOVAL_CONFIRM_MS);
      render();
      el.querySelector<HTMLElement>('[data-act="key-remove"]')?.focus();
      return;
    }
    confirmingKeyRemoval = false;
    keyBusy = true;
    render();
    try {
      await api.secretsClearOpenrouterKey();
      hasKey = false;
      replacingKey = false;
      keyError = null;
      openrouter = null;
      toaster.info(t("settings.keyRemoved"));
      await readStatus();
    } catch (err) {
      toaster.info(describeError(err));
    }
    keyBusy = false;
    render();
    el.querySelector<HTMLElement>("#key-input")?.focus();
  }

  async function pasteKey(): Promise<void> {
    if (keyBusy) return;
    const input = el.querySelector<HTMLInputElement>("#key-input");
    if (input?.value.trim()) {
      void submitKey(input.value);
      return;
    }
    try {
      void submitKey(await navigator.clipboard.readText());
    } catch {
      // the webview does not let the page read the clipboard: the field takes Ctrl+V
      input?.focus();
      toaster.info(t("common.keyPasteHint"));
    }
  }

  function commitLimit(input: HTMLInputElement): void {
    const limit = parseLimit(input.value);
    if (limit === null) {
      limitDraft = input.value;
      limitError = () => t("common.limitInvalid");
      render();
      return;
    }
    limitDraft = null;
    limitError = null;
    if (limit === brain?.monthly_limit_usd) {
      render();
      return;
    }
    void changeBrain("monthly_limit_usd", limit);
  }

  /**
   * A clear of 資料: the first press asks again for a few seconds; the second empties what it names
   * once the 10 seconds of 「復原」 have run out (`app.undo`), through the core.
   */
  function clear(what: "dictation" | "conversation"): void {
    clearTimeout(clearTimer);
    if (confirmingClear !== what) {
      confirmingClear = what;
      clearTimer = setTimeout(() => {
        confirmingClear = null;
        if (visible) render();
      }, CLEAR_CONFIRM_MS);
      render();
      return;
    }
    confirmingClear = null;
    render();
    app.undo.begin({
      message: what === "dictation" ? t("data.dictationCleared") : t("data.conversationCleared"),
      commit: async () => {
        if (what === "dictation") await api.historyClear();
        else await api.agentHistoryClear();
        // The history or the 拉斐爾 page under the panel loaded what is gone now: they drop it.
        app.cleared.say(what);
      },
      restore: () => {}, // nothing was taken off the screen here
      failure: (err) => t("undo.001", { p0: () => describeError(err) }),
    });
  }

  function commitCity(input: HTMLInputElement): void {
    const city = input.value.trim();
    if (city === "") {
      cityDraft = input.value;
      cityError = () => t("settings.agentCityEmpty");
      render();
      return;
    }
    cityDraft = null;
    cityError = null;
    if (city === agentCity(view())) {
      render();
      return;
    }
    void changeBrain("agent_city", city);
  }

  // ---------- the microphone and its level ----------

  // After a core restart the app switches the new core's monitoring back on by itself.
  const monitor = new MonitorSwitch(api.audioMonitor);
  /** The window is in use. It is taken to be, until it says otherwise: the page was just opened by a person. */
  let windowFocused = true;
  let microphoneTimer: ReturnType<typeof setInterval> | undefined;

  /** The microphone is listened to while the page is on screen and the window is in use. */
  function syncMonitor(): void {
    const want = visible && pane === "dictation" && !document.hidden && windowFocused;
    monitor.set(want);
    if (want && microphoneTimer === undefined) {
      microphoneTimer = setInterval(() => {
        // a search that 「重新搜尋麥克風」 started is not overtaken by a quicker, plainer read
        const anchor = el.querySelector<HTMLElement>('[data-k="mic"]');
        if (anchor && !searchingMicrophones) void refreshMicrophones(anchor, false);
      }, 750);
    } else if (!want) {
      clearInterval(microphoneTimer);
      microphoneTimer = undefined;
      microphoneReads++;
      // No more levels come: the meter would stay lit at the last one.
      onLevel(0);
    }
  }

  function onLevel(value: number): void {
    setMeter(el.querySelector(".mic-ctl .meter"), value);
  }

  /** Which reading of the microphones is the latest, so that an older, slower one is dropped. */
  let microphoneReads = 0;

  /**
   * Reads the microphones again when their menu is opened: a headset may have been plugged in, or
   * the Windows default changed, since the page was shown. The menu is already open with the list
   * there was; if the new list differs, the menu and the button's own label are drawn again.
   */
  async function refreshMicrophones(anchor: HTMLElement, refresh = true): Promise<void> {
    const mine = ++microphoneReads;
    let fresh: AudioDevices;
    try {
      fresh = await api.audioDevices(refresh);
    } catch {
      return; // the list there was stays
    }
    if (mine !== microphoneReads || !visible) return;
    const before = JSON.stringify(settingsMicrophones(view()));
    devices = fresh;
    const choices = settingsMicrophones(view());
    if (JSON.stringify(choices) === before) return;
    const current = app.device().microphone ?? AUTO_MICROPHONE;
    const label = anchor.querySelector("span");
    const { shown, full } = selText(choices.find((c) => c.value === current), current);
    if (label) label.textContent = shown;
    anchor.title = full;
    const menu = menuFor("mic");
    if (menu && menuIsOpenFor(anchor)) showMenu(anchor, menu);
  }

  /**
   * 「重新搜尋麥克風」: reads the microphones again while the menu stays open, its item saying that it
   * searches, and then shows the list that was found (the same one, if nothing was plugged in).
   */
  async function searchMicrophones(anchor: HTMLElement): Promise<void> {
    if (searchingMicrophones) return;
    const redraw = (): void => {
      const menu = menuFor("mic");
      if (menu && menuIsOpenFor(anchor)) showMenu(anchor, menu);
    };
    searchingMicrophones = true;
    redraw();
    await refreshMicrophones(anchor);
    searchingMicrophones = false;
    redraw();
  }

  // ---------- events ----------

  el.addEventListener("click", (e) => {
    const target = e.target as Element;
    if (ownerControls.click(target)) return;
    const modelTab = target.closest<HTMLElement>("[data-models-tab]");
    if (modelTab) { switchModelsTab(modelTab.dataset.modelsTab as ModelsTab); return; }
    const paneButton = target.closest<HTMLElement>("[data-pane]");
    if (paneButton) {
      openPane(paneButton.dataset.pane as SettingsPane);
      return;
    }
    const hit = target.closest<HTMLElement>("[data-hit]");
    if (hit) {
      const chosen = hits[Number(hit.dataset.hit)];
      if (chosen) jump(chosen);
      return;
    }

    if (target.closest("[data-cli], [data-cli-menu]")) { void cliBrains.click(target); return; }
    if (providers.click(target)) return;
    // the usage view opens its own dropdowns' menus (usage-analytics.ts)
    const choose = target.closest<HTMLElement>(".sel");
    if (choose && !choose.closest(".usage-analytics, .run-analytics")) {
      if (menuIsOpenFor(choose)) {
        closeMenu();
        return;
      }
      if (choose.dataset.k === "languages") {
        openLanguages(choose);
        return;
      }
      const menu = menuFor(choose.dataset.k ?? "");
      if (menu) showMenu(choose, menu);
      if (choose.dataset.k === "mic") void refreshMicrophones(choose);
      return;
    }

    if (connections.click(target) || messaging.click(target) || memories.some((list) => list.click(target)) || rules.click(target) || services.click(target)) return;

    if (target.closest('.tg[data-k="autostart"]')) {
      void changeDevice({ autostart: !app.device().autostart });
      return;
    }

    if (target.closest('.tg[data-k="subtitles"]')) {
      void changeDevice({ subtitles: app.device().subtitles === "off" ? "on" : "off" });
      return;
    }

    const act = target.closest<HTMLElement>("[data-act]")?.dataset.act;
    switch (act) {
      case "close":
        app.closeSettings();
        break;
      case "rerun-guide":
        app.rerunGuide();
        break;
      case "rec":
      case "rec-agent": {
        const field = act === "rec" ? "dictation_keys" : "agent_keys";
        const cell = el.querySelector<HTMLElement>(`[data-row="${HOTKEY_ROWS[field]}"] .caps`);
        if (cell) void recording.start(field, cell);
        break;
      }
      case "key-replace":
        replacingKey = true;
        keyError = null;
        render();
        el.querySelector<HTMLElement>("#key-input")?.focus();
        break;
      case "key-cancel":
        replacingKey = false;
        keyError = null;
        render();
        el.querySelector<HTMLElement>('[data-act="key-replace"]')?.focus();
        break;
      case "key-remove":
        void removeKey();
        break;
      case "key-paste":
        void pasteKey();
        break;
      case "or-check":
        void checkOpenrouter();
        break;
      case "computer-menu": {
        // one app at a time, taken off under the app's own lock: a list computed from the
        // settings the window has would put back an app another quick removal takes away
        const exe = target.closest<HTMLElement>("[data-exe]")?.dataset.exe;
        if (exe !== undefined) {
          openActions(target.closest<HTMLElement>('[data-act]')!, [{ id: "delete", label: t("settings.computerRemove"), icon: "trash" }], () => void removeComputerApp(exe));
        }
        break;
      }
      case "clear-dictation":
        clear("dictation");
        break;
      case "clear-conversation":
        clear("conversation");
        break;
      case "help":
        void api.openExternalUrl("https://github.com/Jack-hehe/Raphael/issues").catch(error => toaster.info(describeError(error)));
        break;
      case "license":
        e.preventDefault();
        toaster.info(t("settings.085"));
        break;
    }
  });

  // `change` comes when a field loses focus or Enter is pressed: no save button needed. The key is
  // the exception: it is sent by Enter or 貼上 only, never by leaving the field (keyfield.ts), so
  // that 取消 after typing a replacement does not store it.
  el.addEventListener("change", (e) => {
    void ownerControls.change(e.target as Element);
    void providers.change(e.target as Element);
    const input = e.target as HTMLInputElement;
    if (input.id === "limit-input") commitLimit(input);
    else if (input.id === "agent-city") commitCity(input);
  });
  el.addEventListener("input", (e) => {
    const input = e.target as HTMLInputElement;
    providers.input(input);
    if (connections.input(input) || messaging.input(input) || memories.some((list) => list.input(input)) || rules.input(input)) return;
    if (input.id === "limit-input") limitDraft = input.value;
    else if (input.id === "agent-city") cityDraft = input.value;
  });
  el.addEventListener("keydown", (e) => {
    const tab = (e.target as Element).closest<HTMLElement>("[data-models-tab]");
    if (tab && ["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) {
      e.preventDefault();
      switchModelsTab(e.key === "Home" ? "models" : e.key === "End" ? "usage" : modelsTab === "models" ? "usage" : "models");
      return;
    }
    void (connections.key(e) || messaging.key(e) || memories.some((list) => list.key(e)) || rules.key(e) || services.key(e) || providers.key(e));
  });
  // Menus consume their own Escape; the panel and native window controls share one Tab loop.
  const settingsKey = (e: KeyboardEvent): void => {
    const chrome = document.getElementById("window-chrome");
    if (!visible || !(e.target instanceof Node) || !ownsSettingsKey(e.target, [el, chrome], [document.body, document.documentElement])) return;
    if (openMenuOnArrow(e)) return;
    if (e.key === "Escape" && isTopLayer(el.querySelector(".modal")) && !recording.active() && !e.defaultPrevented) {
      e.preventDefault();
      app.closeSettings();
    } else if (e.key === "Tab") {
      const selector = "button:not([disabled]):not([tabindex='-1']), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), a[href], [tabindex='0']";
      const active = document.activeElement as HTMLElement | null;
      // A dialog over the settings (a service of 連接) keeps the focus to itself.
      const trap = el.querySelector<HTMLElement>("[data-trap]");
      if (trap) {
        const inside = Array.from(trap.querySelectorAll<HTMLElement>(selector)).filter((item) => item.offsetParent !== null);
        if (!active || !trap.contains(active)) {
          e.preventDefault();
          (inside[0] ?? trap).focus();
          return;
        }
        trapSettingsFocus(e, inside, active, trap);
        return;
      }
      const items = [
        ...Array.from(el.querySelectorAll<HTMLElement>(selector)),
        ...Array.from(chrome?.querySelectorAll<HTMLElement>(selector) ?? []),
      ].filter((item) => item.offsetParent !== null);
      trapSettingsFocus(e, items, active, el.querySelector<HTMLElement>("#set-title"));
    }
  };
  wireKeyField(el, { inputId: "key-input", pasteSelector: '[data-act="key-paste"]', submit: (value) => void submitKey(value) });

  const onFocus = (): void => {
    windowFocused = true;
    syncMonitor();
  };
  const onBlur = (): void => {
    windowFocused = false;
    syncMonitor();
  };
  const onVisibility = (): void => syncMonitor();
  let stopEvents: (() => void) | null = null;

  return {
    id: "settings",
    el,
    pane: () => pane,
    async show(open?: SettingsPane) {
      const hadUsage = usage !== null;
      visible = true;
      stopOutside();
      stopOutside = registerDismissal(el.querySelector<HTMLElement>(".modal")!, () => app.closeSettings(), () => !recording.active());
      cliBrains.start();
      document.addEventListener("keydown", settingsKey);
      const mine = ++loads;
      loading = true;
      // the first tab, unless the way in knows better (the report that says there is no key goes to the key)
      pane = open && PANES.some((p) => p.id === canonicalPane(open)) ? canonicalPane(open) : "general";
      if (open === "usage" || open === "models") modelsTab = open;
      if (pane !== "models") destroyUsage();
      connections.reset();
      messaging.reset();
      for (const list of memories) list.reset();
      rules.reset();
      services.reset();
      providers.reset();
      if (pane === "connections") void connections.load();
      if (pane === "messaging") void messaging.load();
      if (pane === "agent") for (const list of memories) void list.load();
      if (pane === "agent") void rules.load();
      if (pane === "agent") void services.load();
      if (pane === "models" && modelsTab === "models") void providers.load();
      searchField.value = "";
      hits = [];
      pendingJump = null;
      clearTimeout(clearTimer);
      confirmingClear = null;
      body.scrollTop = 0;
      closeMenu();
      render();
      paintNav();
      el.querySelector<HTMLElement>("#set-title")?.focus();
      const [brainResult, keyResult, statusResult, devicesResult, altgrResult, versionResult, languagesResult] = await Promise.allSettled([
        api.settingsGet(),
        api.secretsStatus(),
        api.statusGet(),
        api.audioDevices(),
        api.keyboardHasAltgr(),
        getVersion(),
        api.languagesList(),
        // Read again: the settings may have changed since the window started. `refreshDevice` puts
        // the answer in `app.device()` itself, or drops it if a newer read or write overtook it.
        app.refreshDevice(),
      ]);
      if (mine !== loads || !visible) return;
      brain = brainResult.status === "fulfilled" ? brainResult.value.settings : null;
      hasKey = keyResult.status === "fulfilled" && keyResult.value.has_key;
      openrouter = statusResult.status === "fulfilled" ? statusResult.value.openrouter : null;
      spend = statusResult.status === "fulfilled" ? statusResult.value.spend : null;
      devices = devicesResult.status === "fulfilled" ? devicesResult.value : null;
      altgr = altgrResult.status === "fulfilled" && altgrResult.value;
      version = versionResult.status === "fulfilled" ? versionResult.value : "";
      if (languagesResult.status === "fulfilled") languageGroups = languagesResult.value.groups;
      replacingKey = false;
      keyError = null;
      limitDraft = null;
      limitError = null;
      cityDraft = null;
      cityError = null;
      loading = false;
      render();
      void hiddenMembers.load();
      if (!el.contains(document.activeElement)) el.querySelector<HTMLElement>("#set-title")?.focus();
      void loadCatalog();
      void loadLocalModels();

      stopEvents?.();
      const stopLevels = app.onEvent((event) => {
        if (event.type === "Level") onLevel(event.value);
        if (event.type === "MembersChanged") void hiddenMembers.load();
      });
      windowFocused = true;
      window.addEventListener("focus", onFocus);
      window.addEventListener("blur", onBlur);
      document.addEventListener("visibilitychange", onVisibility);
      stopEvents = () => {
        stopLevels();
        window.removeEventListener("focus", onFocus);
        window.removeEventListener("blur", onBlur);
        document.removeEventListener("visibilitychange", onVisibility);
      };
      syncMonitor();
      if (hadUsage) refreshUsage();
      await usage?.ready;
    },
    hide() {
      visible = false;
      stopOutside();
      for (const picker of pickers.values()) picker.close();
      cliBrains.stop();
      destroyUsage();
      connections.reset();
      messaging.reset();
      for (const list of memories) list.reset();
      rules.reset();
      services.reset();
      providers.reset();
      document.removeEventListener("keydown", settingsKey);
      loading = false;
      loads++;
      closeMenu();
      void recording.cancel();
      stopEvents?.();
      stopEvents = null;
      syncMonitor();
    },
  };
}
