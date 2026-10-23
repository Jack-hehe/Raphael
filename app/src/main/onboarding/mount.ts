// The guide in the main window: it takes the place of the pages on a first run, and opens again
// over them from the settings. flow.ts says which screen is next, screens.ts draws it, practice.ts
// says whether a lesson was done; this file holds the state and does what the buttons say, through
// the core and the app. The window is the app in front, so a dictation lands in the lesson's own
// box; the keys are heard as events of the app (and, while the window has the focus, as keys of
// the window, so that the caps light at the moment the key goes down).
//
// Never kept or logged here: what was dictated, asked or answered. The lessons show it on the
// screen and let it go.

import { onLocaleChange, t } from "../i18n";

import * as api from "../api";
import { DICTATION_MODES, type DictationMode } from "../dictation";
import { describeError, keyReasonText } from "../format";
import { type HotkeyField, createHotkeyRecording } from "../hotkey-recording";
import { wireKeyField } from "../keyfield";
import { canonicalKey } from "../keys";
import { createMessaging, messagingHTML } from "../messaging";
import { MonitorSwitch } from "../monitor";
import { ModelDownload, type ModelPhase, formatProgress } from "../models";
import { UI_LANGUAGES } from "../pages/settings-view";
import { Stage } from "../stage";
import { keyEntryRow, micRow as micRowShared } from "../setup-rows";
import type { AppContext, AudioDevices, MainEvent, MicAccess, StoreService } from "../types";
import { actionRow, btn, closeMenu, esc, group, menuIsOpenFor, note, openMenu, openMenuOnArrow, row, sel, setMeter, st, toaster } from "../ui";
import { AUTO_MICROPHONE, automaticMicrophoneLabel, microphoneChoices } from "../microphone";
import type { AbilityId, LessonId } from "./abilities";
import { heardAfter, limitAction, lookUp, modelPercent } from "./helpers";
import {
  type FlowEvent,
  type FlowState,
  type Mode,
  type Readiness,
  canAdvance,
  applyReadiness,
  canSkip,
  currentLesson,
  hasKey,
  initialState,
  reduce,
  segments,
  stepsFor,
} from "./flow";
import { type HookDown, allDown, bindingParts, hookAfter, litKeys, paintCaps } from "./keycaps";
import { CONFLICT_MS, type Lesson, type PracticeEvent, conflictSuspected, feed, isNewItem, isNewTurn, retry as retryLesson, startLesson } from "./practice";
import {
  type LessonView,
  type MicBlock,
  abilitiesHTML,
  connectionsHTML,
  doneHTML,
  keyHTML,
  micHTML,
  modelControlHTML,
  practiceHTML,
  lessonStatus,
  setupText,
  telegramHTML,
  topHTML,
  welcomeHTML,
} from "./screens";

/** What the guide tells whoever opened it when it is over. */
export interface GuideResult {
  /** The guide was gone through to its end (a first run then saved `onboarded`). False: it was left. */
  finished: boolean;
  /** The end asked for the settings, at the tab with the key and the limit. */
  toSettings: boolean;
}

export interface GuideOptions {
  mode: Mode;
  onDone: (result: GuideResult) => void;
}

/** The model bar is drawn at most this often. */
const MODEL_PAINT_MS = 120;
/** How often the privacy settings are asked again while the microphone step is on screen. */
const ACCESS_POLL_MS = 2000;
/** The services the connections step offers when the store marks none as popular. */
const CONNECTIONS_SHOWN = 6;

/** A selector for the element that has focus inside `host`, to give it focus again after a redraw. */
function focusSelector(host: HTMLElement): string {
  const active = document.activeElement;
  if (!(active instanceof HTMLElement) || !host.contains(active)) return "";
  if (active.id) return `#${active.id}`;
  if (active.dataset.ob) return `[data-ob="${active.dataset.ob}"]`;
  return active.dataset.ability ? `[data-ability="${active.dataset.ability}"]` : "";
}

export function mountOnboarding(root: HTMLElement, options: GuideOptions, app: AppContext): void {
  const mode = options.mode;
  let flow: FlowState = initialState(mode, { key: false, connections: false, telegram: false });
  let stage: Stage | null = null;
  let advancing = false;
  let finishing = false;
  let destroyed = false;
  let windowFocused = true;
  /** The person ticked or unticked an ability, so late news of what is set up must not overwrite it. */
  let ticksChanged = false;
  /** Every listener on the root ends with the guide: a guide opened again on the same root is a new one. */
  const lifetime = new AbortController();
  const { signal } = lifetime;

  // the key and the limit
  let keyName: string | null = null;
  let replacingKey = false;
  let keyBusy = false;
  let keyError: (() => string) | null = null;
  let keyLoaded = false;
  let limitCurrent = 10;
  let limitText = "10";
  let limitEdited = false;
  let limitError: (() => string) | null = null;
  let limitJob: Promise<boolean> = Promise.resolve(true);
  /** Speech is recognised in the cloud, so the model on this computer is not waited for. */
  let cloudSpeech = false;

  // the microphone
  let devices: AudioDevices | null = null;
  let microphoneReads = 0;
  let blocked: MicBlock | null = null;
  let heard = false;
  let heardSince: number | null = null;
  let microphoneTimer: ReturnType<typeof setInterval> | undefined;
  let accessTimer: ReturnType<typeof setInterval> | undefined;
  let modelTimer: ReturnType<typeof setTimeout> | undefined;

  // connections
  let services: StoreService[] | "loading" | "failed" = "loading";
  let waitingService: string | null = null;
  let connectFailed: string | null = null;

  // the practice
  let lesson: Lesson | null = null;
  let lessonToken = 0;
  let practising = false;
  let hook: HookDown = { dictation: false, agent: false };
  const held = new Set<string>();
  let altgr = false;
  let conflictTimer: ReturnType<typeof setTimeout> | undefined;
  let conflict = false;

  const monitor = new MonitorSwitch(api.audioMonitor);
  const download = new ModelDownload({ status: api.modelsStatus, ensure: api.modelsEnsure }, onModelChange);
  const messaging = createMessaging({
    visible: () => !destroyed && flow.step === "telegram",
    redraw: paintTelegram,
  });
  const recording = createHotkeyRecording(app, () => {
    if (destroyed) return;
    // The recorder is done with the keys: the lesson of the key practises them again.
    if (lesson?.id === "key") void setPractice(true);
    repaintBody();
  });

  const device = () => app.device();

  // ---------- drawing ----------

  function micRow(): string {
    const saved = device().microphone;
    return micRowShared({
      choices: microphoneChoices(devices, saved),
      current: saved ?? AUTO_MICROPHONE,
      selAttrs: ' data-ob="mic"',
      meter: { place: "before" },
      row: { attrs: ' id="ob-mic-row"' },
    });
  }

  function keyRow(): string {
    if (flow.setup.key === "done" && !replacingKey) {
      return row(
        t("common.apiKey"),
        keyName === null ? t("onboarding.keyStored") : t("onboarding.keyNamed", { p0: esc(keyName) }),
        `<span class="val num">••••••••</span>${st("ok", keyName === null ? t("onboarding.keyIsSet") : t("onboarding.keyValid"))}${btn(esc(t("onboarding.keyReplace")), "sm", ' data-ob="key-replace"')}`,
      );
    }
    return keyEntryRow({
      desc: t("onboarding.keyRowDesc"),
      fieldWidth: 220,
      inputId: "ob-key-input",
      hook: (name) => ` data-ob="${name}"`,
      busy: keyBusy,
      invalid: keyError !== null,
      canCancel: flow.setup.key === "done",
    });
  }

  function keyGroup(): string {
    return group(
      [
        keyRow(),
        ...(keyError ? [note(st("err", keyError()), ' role="alert"')] : []),
      ],
      flow.setup.key === "done" ? "" : esc(t("onboarding.keyHelp")),
    );
  }

  function modelRow(): string {
    return row(t("onboarding.modelName"), needsLocalModel() ? t("onboarding.modelDesc") : t("onboarding.modelBackground"), `<span class="ctl-model" data-model="ctl"></span>`);
  }

  function modelControl(phase: ModelPhase): string {
    const percent = modelPercent(phase);
    const kind = phase.kind === "checking" ? "idle" : phase.kind;
    return modelControlHTML({
      kind,
      percent,
      detail: phase.kind === "downloading" ? `${percent ?? 0}% · ${formatProgress(phase.done, phase.total)}` : "",
    });
  }

  const lessonLanguage = (id: LessonId): string => {
    switch (id) {
      case "dictate":
        return t("onboarding.sayDictate");
      case "cleanup":
        return t("onboarding.sayCleanup");
      case "ask":
        return t("onboarding.sayAsk");
      case "act":
        return t("onboarding.sayAct");
      default:
        return "";
    }
  };

  function lessonView(l: Lesson): LessonView {
    const phase = download.phase;
    return {
      lesson: l,
      mode: device().dictation_mode,
      dictationKeys: device().dictation_keys[0] ?? "AltRight",
      agentKeys: device().agent_keys?.[0] ?? "AltRight+Space",
      altgr,
      conflict: conflict && conflictSuspected(l, Date.now()),
      waitingForModel: needsLocalModel() && phase.kind !== "ready" ? { percent: modelPercent(phase) } : null,
      say: lessonLanguage(l.id),
    };
  }

  function doneRows(): { label: string; state: "ok" | "warn"; text: string }[] {
    const steps = stepsFor(flow);
    const rows: { label: string; state: "ok" | "warn"; text: string }[] = [];
    if (steps.includes("key")) rows.push({ label: t("common.apiKey"), ...setupText(flow.setup.key) });
    if (steps.includes("mic")) {
      const saved = device().microphone;
      rows.push({ label: t("common.microphone"), state: "ok", text: saved ?? automaticMicrophoneLabel(devices) });
    }
    if (steps.includes("connections")) rows.push({ label: t("onboarding.doneConnections"), ...setupText(flow.setup.connections) });
    if (steps.includes("telegram")) rows.push({ label: "Telegram", ...setupText(flow.setup.telegram) });
    return rows;
  }

  /** The speech model of this computer is waited for unless speech is recognised in the cloud (which needs the key). */
  const needsLocalModel = (): boolean => !cloudSpeech || !hasKey(flow);

  const noKey = (): boolean => stepsFor(flow).includes("key") && !hasKey(flow);

  function bodyHTML(): string {
    switch (flow.step) {
      case "welcome":
        return welcomeHTML({
          rerun: mode === "rerun",
          languageSelect: sel(UI_LANGUAGES, device().ui_language, t("settings.059"), ' data-ob="lang"'),
        });
      case "abilities":
        return abilitiesHTML(flow.abilities);
      case "key":
        return keyHTML(keyGroup());
      case "mic":
        return micHTML({ micRowHTML: micRow(), blocked, heard, modelRowHTML: modelRow() });
      case "connections":
        return connectionsHTML({ services, waiting: waitingService, failed: connectFailed });
      case "telegram":
        return telegramHTML(messagingHTML(messaging.view()));
      case "practice":
        return lesson ? practiceHTML(lessonView(lesson)) : "";
      case "done":
        return doneHTML({
          rerun: mode === "rerun",
          rows: doneRows(),
          dictationKeys: device().dictation_keys[0] ?? "AltRight",
          agentKeys: device().agent_keys?.[0] ?? "AltRight+Space",
          pasteKeys: device().paste_last_keys[0] ?? "",
          noKey: noKey(),
        });
    }
  }

  const topHTMLNow = (): string =>
    topHTML({
      segments: segments(flow),
      back: flow.step !== "welcome" || mode === "rerun",
      skip: canSkip(flow),
    });

  function footHTML(): string {
    const disabled = !canAdvance(flow) || finishing ? " disabled" : "";
    if (flow.step === "done") {
      if (noKey()) {
        return actionRow({ cls: "ob-actions", secondary: btn(esc(t(mode === "rerun" ? "onboarding.finishRerun" : "onboarding.finish")), "", ` data-ob="next"${finishing ? " disabled" : ""}`), primary: { label: esc(t("onboarding.setKey")), attrs: ` data-ob="finish-key"${finishing ? " disabled" : ""}` } });
      }
      return actionRow({ cls: "ob-actions", primary: { label: esc(t(mode === "rerun" ? "onboarding.finishRerun" : "onboarding.finish")), attrs: ` data-ob="next"${finishing ? " disabled" : ""}` } });
    }
    return actionRow({ cls: "ob-actions", primary: { label: esc(t(flow.step === "welcome" ? "onboarding.start" : "onboarding.next")), attrs: ` data-ob="next"${disabled}` } });
  }

  function paintTop(): void {
    const top = root.querySelector<HTMLElement>(".ob-top");
    if (!top) return;
    const focus = focusSelector(top);
    top.innerHTML = topHTMLNow();
    if (focus) top.querySelector<HTMLElement>(focus)?.focus({ preventScroll: true });
  }

  function paintFoot(): void {
    const foot = root.querySelector<HTMLElement>(".ob-foot");
    if (!foot) return;
    const focus = focusSelector(foot);
    foot.innerHTML = footHTML();
    if (focus) foot.querySelector<HTMLElement>(focus)?.focus({ preventScroll: true });
  }

  /** Draws the whole screen. `keepLesson`: the lesson that is on screen goes on (a new language, say). */
  function render(keepLesson = false): void {
    stage?.dispose();
    stage = null;
    closeMenu();
    if (!keepLesson) endLesson();
    if (flow.step === "practice" && !lesson && !keepLesson) beginLesson();
    const quiet = flow.step === "welcome" && mode === "first";
    root.innerHTML =
      `<div class="ob-top${quiet ? " quiet" : ""}">${topHTMLNow()}</div>` +
      `<div class="ob-body"><div class="ob-col">${bodyHTML()}</div></div>` +
      `<div class="ob-foot">${footHTML()}</div>`;
    enterScreen();
  }

  /** Draws the part between the bar and the button again, keeping what is typed and where the focus is. */
  function repaintBody(): void {
    const col = root.querySelector<HTMLElement>(".ob-col");
    if (!col) return;
    const typed = Array.from(col.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>("input, textarea")).map((input) => [input.id, input.value] as const);
    const focus = focusSelector(col);
    closeMenu();
    col.innerHTML = bodyHTML();
    for (const [id, value] of typed) {
      const input = id ? col.querySelector<HTMLInputElement | HTMLTextAreaElement>(`#${CSS.escape(id)}`) : null;
      if (input) input.value = value;
    }
    if (focus) col.querySelector<HTMLElement>(focus)?.focus({ preventScroll: true });
    afterBody();
  }

  /** What has to be filled in or started once the body is on screen. */
  function afterBody(): void {
    paintModel();
    paintCapsNow();
    if (flow.step === "practice") {
      // The box takes the paste of a dictation: it must have the focus.
      root.querySelector<HTMLElement>("#ob-box")?.focus({ preventScroll: true });
    }
  }

  function enterScreen(): void {
    syncMonitor();
    switch (flow.step) {
      case "welcome": {
        const canvas = root.querySelector<HTMLCanvasElement>("canvas.emb");
        const subs = root.querySelector<HTMLElement>(".subs");
        if (canvas && subs) {
          stage = new Stage(canvas, subs, () => {});
          stage.play();
          stage.show({ text: mode === "first" ? t("report.firstRun") : t("onboarding.rerunLine") });
        }
        break;
      }
      case "key":
        void loadKeyStep();
        break;
      case "mic":
        void download.start();
        void loadDevices();
        void checkAccess();
        break;
      case "connections":
        if (services === "loading" || services === "failed") void loadServices();
        break;
      case "telegram":
        void messaging.load();
        break;
    }
    afterBody();
    if (flow.step !== "practice") root.querySelector<HTMLElement>("h2")?.focus({ preventScroll: true });
  }

  // ---------- the flow ----------

  function dispatch(event: FlowEvent): void {
    if (destroyed) return;
    if (event.type === "TICK") ticksChanged = true;
    const before = flow;
    const after = reduce(before, event);
    if (after === before) return;
    flow = after;
    if (before.step !== after.step || before.lesson !== after.lesson) {
      render();
      return;
    }
    paintTop();
    paintFoot();
    if (event.type === "TICK") repaintBody();
  }

  /** The bar, the button and the lesson list after the setup changed, without drawing the screen again. */
  function setupChanged(need: "key" | "connections" | "telegram", state: "done" | "todo"): void {
    if (flow.setup[need] === state) return;
    dispatch({ type: "SETUP", need, state });
  }

  async function next(): Promise<void> {
    if (advancing || finishing) return;
    advancing = true;
    try {
      if (flow.step === "key" && !(await commitLimit())) {
        if (limitError) root.querySelector<HTMLElement>("#ob-limit")?.focus();
        return;
      }
      if (flow.step === "done") {
        await finish(false);
        return;
      }
      dispatch({ type: "NEXT" });
    } finally {
      advancing = false;
    }
  }

  async function skip(): Promise<void> {
    if (advancing || finishing) return;
    advancing = true;
    try {
      // A skip is counted only when the limit is fine: it would otherwise unlock 「下一步」 behind the person's back.
      if (flow.step === "key" && !(await commitLimit())) return;
      dispatch({ type: "SKIP" });
    } finally {
      advancing = false;
    }
  }

  function back(): void {
    if (flow.step === "welcome") {
      // Only a rerun can be left from its first screen: a first run has nothing to go back to.
      if (mode === "rerun") leave({ finished: false, toSettings: false });
      return;
    }
    dispatch({ type: "BACK" });
  }

  async function finish(toSettings: boolean): Promise<void> {
    finishing = true;
    paintFoot();
    if (mode === "first") {
      try {
        await app.setDevice({ onboarded: true });
      } catch (err) {
        toaster.info(describeError(err));
        finishing = false;
        paintFoot();
        return;
      }
    }
    leave({ finished: true, toSettings });
  }

  function leave(result: GuideResult): void {
    destroy();
    options.onDone(result);
  }

  function destroy(): void {
    destroyed = true;
    endLesson();
    syncMonitor();
    stage?.dispose();
    stage = null;
    closeMenu();
    clearTimeout(modelTimer);
    clearTimeout(conflictTimer);
    messaging.reset();
    void recording.cancel();
    stopEvents();
    stopLocale();
    lifetime.abort();
    window.removeEventListener("focus", onFocus);
    window.removeEventListener("blur", onBlur);
    window.removeEventListener("keydown", onKey, true);
    window.removeEventListener("keyup", onKey, true);
    document.removeEventListener("visibilitychange", syncMonitor);
    root.replaceChildren();
  }

  // ---------- what is already set up ----------

  async function loadReadiness(): Promise<void> {
    const [stored, groups, telegram, settings] = await Promise.allSettled([api.secretsStatus(), api.toolsetsList(), api.telegramStatus(), api.settingsGet()]);
    if (destroyed) return;
    const ready: Readiness = {
      key: stored.status === "fulfilled" && stored.value.has_key,
      connections: groups.status === "fulfilled" && groups.value.groups.length > 0,
      telegram: telegram.status === "fulfilled" && telegram.value.connected && telegram.value.allowed.length > 0,
    };
    if (settings.status === "fulfilled") {
      limitCurrent = settings.value.settings.monthly_limit_usd;
      if (!limitEdited) limitText = String(limitCurrent);
      cloudSpeech = settings.value.settings.stt_engine === "cloud";
    }
    keyLoaded = stored.status === "fulfilled" && settings.status === "fulfilled";
    // What was set up is known; what the person has decided meanwhile stays.
    flow = applyReadiness(flow, ready, ticksChanged);
    paintTop();
    paintFoot();
    if (flow.step === "key") paintKeyGroup();
    if (flow.step === "abilities") repaintBody();
  }

  // ---------- the key and the limit ----------

  /** Reads whether a key is stored and what the limit is. Tried again next time if the core did not answer. */
  async function loadKeyStep(): Promise<void> {
    if (keyLoaded) return;
    const [stored, settings] = await Promise.allSettled([api.secretsStatus(), api.settingsGet()]);
    if (destroyed) return;
    if (stored.status === "fulfilled" && stored.value.has_key) setupChanged("key", "done");
    if (settings.status === "fulfilled") {
      limitCurrent = settings.value.settings.monthly_limit_usd;
      cloudSpeech = settings.value.settings.stt_engine === "cloud";
      if (!limitEdited) limitText = String(limitCurrent);
    }
    keyLoaded = stored.status === "fulfilled" && settings.status === "fulfilled";
    if (flow.step === "key") paintKeyGroup();
  }

  /** Draws the key and limit rows again. What is typed in the key field stays (and is never kept anywhere else). */
  function paintKeyGroup(): void {
    const host = root.querySelector<HTMLElement>("#ob-keygroup");
    if (!host) return;
    const typed = host.querySelector<HTMLInputElement>("#ob-key-input")?.value ?? "";
    const focus = focusSelector(host);
    host.innerHTML = keyGroup();
    const input = host.querySelector<HTMLInputElement>("#ob-key-input");
    if (input) input.value = typed;
    if (focus) host.querySelector<HTMLElement>(focus)?.focus();
  }

  async function submitKey(text: string): Promise<void> {
    const key = text.trim();
    if (!key || keyBusy) return;
    keyBusy = true;
    keyError = null;
    paintKeyGroup();
    try {
      const result = await api.secretsSetOpenrouterKey(key);
      if (result.valid) {
        keyName = result.label;
        replacingKey = false;
        setupChanged("key", "done");
        toaster.info(t("common.keySaved"));
      } else {
        keyError = () => keyReasonText(result.reason);
      }
    } catch (err) {
      keyError = () => describeError(err);
    }
    keyBusy = false;
    const active = document.activeElement as HTMLElement | null;
    const keyFocused = active?.id === "ob-key-input" || active?.dataset.ob === "key-paste";
    if (flow.step === "key") paintKeyGroup();
    paintFoot();
    if (flow.setup.key === "done" && !replacingKey && keyFocused && !destroyed && flow.step === "key") {
      root.querySelector<HTMLElement>('[data-ob="next"]')?.focus({ preventScroll: true });
    }
  }

  async function pasteKey(): Promise<void> {
    if (keyBusy) return;
    const input = root.querySelector<HTMLInputElement>("#ob-key-input");
    if (input?.value.trim()) {
      void submitKey(input.value);
      return;
    }
    let copied: string;
    try {
      copied = await navigator.clipboard.readText();
    } catch {
      // the webview does not let the page read the clipboard: the field takes Ctrl+V
      input?.focus();
      toaster.info(t("common.keyPasteHint"));
      return;
    }
    if (copied.trim()) {
      void submitKey(copied);
    } else {
      input?.focus();
      toaster.info(t("onboarding.clipboardEmpty"));
    }
  }

  /** Saves the limit in the field if it is another one. True if the limit is fine afterwards. One at a time. */
  function commitLimit(): Promise<boolean> {
    limitJob = limitJob.then(saveLimit);
    return limitJob;
  }

  async function saveLimit(): Promise<boolean> {
    const input = root.querySelector<HTMLInputElement>("#ob-limit");
    const action = limitAction(input ? input.value : limitText, limitCurrent);
    if (action.kind === "invalid") {
      limitError = () => t("common.limitInvalid");
    } else {
      limitError = null;
      if (action.kind === "save") {
        try {
          limitCurrent = (await api.settingsSet("monthly_limit_usd", action.value)).settings.monthly_limit_usd;
          limitText = String(limitCurrent);
        } catch (err) {
          limitError = () => describeError(err);
        }
      }
    }
    if (!destroyed && flow.step === "key") paintKeyGroup();
    return limitError === null;
  }

  // ---------- the microphone and the speech model ----------

  function syncMonitor(): void {
    const want = !destroyed && flow.step === "mic" && !document.hidden && windowFocused;
    monitor.set(want);
    if (want && microphoneTimer === undefined) {
      microphoneTimer = setInterval(() => {
        const anchor = root.querySelector<HTMLElement>('[data-ob="mic"]');
        if (anchor) void refreshMicrophones(anchor, false);
      }, 750);
      accessTimer = setInterval(() => void checkAccess(), ACCESS_POLL_MS);
    } else if (!want) {
      clearInterval(microphoneTimer);
      clearInterval(accessTimer);
      microphoneTimer = undefined;
      accessTimer = undefined;
      microphoneReads++;
      heardSince = null;
    }
  }

  /** Asks whether the privacy settings of Windows keep the microphone shut, and draws the answer if it changed. */
  async function checkAccess(): Promise<void> {
    let access: MicAccess | null = null;
    try {
      access = await api.micAccess();
    } catch {
      access = null; // no answer is no reason to blame the settings
    }
    if (destroyed || flow.step !== "mic") return;
    const now: MicBlock | null = access && !access.allowed ? (access.blocked_by ?? "desktop_apps") : null;
    if (now === blocked) return;
    blocked = now;
    repaintBody();
  }

  function paintMicState(): void {
    const slot = root.querySelector<HTMLElement>("[data-ob-mic-state]");
    if (!slot || blocked) return;
    slot.innerHTML = st(heard ? "ok" : "idle", heard ? t("onboarding.micHeard") : t("onboarding.micWaiting"));
  }

  async function loadDevices(): Promise<void> {
    try {
      devices = await api.audioDevices();
    } catch {
      devices = null;
    }
    if (!destroyed && flow.step === "mic") paintMicRow();
  }

  function paintMicRow(): void {
    const current = root.querySelector<HTMLElement>("#ob-mic-row");
    if (!current) return;
    const focus = focusSelector(current);
    current.outerHTML = micRow();
    if (focus) root.querySelector<HTMLElement>(`#ob-mic-row ${focus}`)?.focus();
  }

  function openMicrophoneMenu(anchor: HTMLElement): void {
    const current = device().microphone;
    openMenu(anchor, microphoneChoices(devices, current), current ?? AUTO_MICROPHONE, (value) => void pickMicrophone(value));
  }

  /**
   * Reads the microphones again when their menu is opened: a headset may have been plugged in, or
   * the Windows default changed, since the step was shown. The menu is already open with the list
   * there was; if the new list differs, the row is drawn again and the menu opened again under it.
   */
  async function refreshMicrophones(anchor: HTMLElement, refresh = true): Promise<void> {
    const mine = ++microphoneReads;
    let fresh: AudioDevices;
    try {
      fresh = await api.audioDevices(refresh);
    } catch {
      return; // the list there was stays
    }
    if (destroyed || flow.step !== "mic" || mine !== microphoneReads) return;
    const saved = device().microphone;
    const before = JSON.stringify(microphoneChoices(devices, saved));
    devices = fresh;
    if (JSON.stringify(microphoneChoices(devices, saved)) === before) return;
    const wasOpen = menuIsOpenFor(anchor);
    paintMicRow();
    const redrawn = root.querySelector<HTMLElement>('[data-ob="mic"]');
    if (wasOpen && redrawn) openMicrophoneMenu(redrawn);
  }

  async function pickMicrophone(value: string): Promise<void> {
    try {
      await app.setDevice({ microphone: value === AUTO_MICROPHONE ? null : value });
    } catch (err) {
      toaster.info(describeError(err));
    }
    if (!destroyed && flow.step === "mic") paintMicRow();
  }

  function paintModel(): void {
    const phase = download.phase;
    root.querySelectorAll<HTMLElement>('[data-model="ctl"]').forEach((node) => {
      node.innerHTML = modelControl(phase);
    });
    // A lesson that waits for the model says how far it is.
    if (flow.step === "practice" && lesson) {
      const slot = root.querySelector<HTMLElement>("[data-ob-status]");
      if (slot) slot.innerHTML = lessonStatus(lessonView(lesson));
    }
  }

  function onModelChange(phase: ModelPhase): void {
    if (destroyed) return;
    // The bytes come often; drawing them a few times a second is plenty.
    if (phase.kind === "downloading") {
      if (modelTimer === undefined) {
        modelTimer = setTimeout(() => {
          modelTimer = undefined;
          paintModel();
        }, MODEL_PAINT_MS);
      }
      return;
    }
    clearTimeout(modelTimer);
    modelTimer = undefined;
    paintModel();
  }

  // ---------- connections ----------

  async function loadServices(): Promise<void> {
    services = "loading";
    if (flow.step === "connections") repaintBody();
    try {
      const all = (await api.integrationsCatalog()).services;
      const popular = all.filter((s) => s.popular);
      services = (popular.length > 0 ? popular : all).slice(0, popular.length > 0 ? popular.length : CONNECTIONS_SHOWN);
      if (services.some((s) => s.server_id !== null)) setupChanged("connections", "done");
    } catch {
      services = "failed";
    }
    if (!destroyed && flow.step === "connections") repaintBody();
  }

  async function connectService(id: string): Promise<void> {
    if (waitingService) return;
    waitingService = id;
    connectFailed = null;
    repaintBody();
    try {
      const result = await api.integrationsConnect({ catalog_id: id });
      if (result.connected) {
        if (Array.isArray(services)) services = services.map((s) => (s.id === id ? { ...s, server_id: result.server.id } : s));
        setupChanged("connections", "done");
      } else if (result.error !== "cancelled") {
        connectFailed = t("onboarding.connFailed");
      }
    } catch (err) {
      connectFailed = describeError(err);
    }
    waitingService = null;
    if (!destroyed && flow.step === "connections") repaintBody();
  }

  // ---------- Telegram ----------

  function paintTelegram(): void {
    const host = root.querySelector<HTMLElement>("#ob-tg");
    if (!host) return;
    const typed = host.querySelector<HTMLInputElement>("#msg-token")?.value ?? "";
    const focus = focusSelector(host);
    const view = messaging.view();
    host.innerHTML = messagingHTML(view);
    const input = host.querySelector<HTMLInputElement>("#msg-token");
    if (input && typed) input.value = typed;
    if (focus) host.querySelector<HTMLElement>(focus)?.focus({ preventScroll: true });
    if (typeof view.status === "object" && view.status.connected && view.status.allowed.length > 0) setupChanged("telegram", "done");
  }

  // ---------- the practice ----------

  /** The keys of the shortcuts of the window, as the names the keys of the window have. */
  const dictationParts = (): string[] => bindingParts(device().dictation_keys[0] ?? "AltRight");
  const agentParts = (): string[] => bindingParts(device().agent_keys?.[0] ?? "AltRight+Space");

  async function setPractice(on: boolean): Promise<void> {
    if (practising === on) return;
    practising = on;
    try {
      await api.hotkeysPractice(on);
    } catch (err) {
      console.warn("guide: could not switch the key practice", err);
    }
  }

  function endLesson(): void {
    // A dictation or question that is under way is ended with Esc, as the person would: it must not
    // go on into the next lesson, or stay recording when the guide is left.
    if (lesson && (lesson.phase === "listening" || lesson.phase === "processing") && !practising) {
      void api.cancelSession().catch(() => {});
    }
    lessonToken++;
    lesson = null;
    conflict = false;
    clearTimeout(conflictTimer);
    held.clear();
    hook = { dictation: false, agent: false };
    void setPractice(false);
  }

  function beginLesson(): void {
    const id = currentLesson(flow);
    if (!id) return;
    const token = ++lessonToken;
    lesson = startLesson(id, Date.now());
    conflict = false;
    void setPractice(id === "key");
    if (id === "key") {
      void api.keyboardHasAltgr().then(
        (has) => {
          if (token !== lessonToken) return;
          altgr = has && dictationParts().includes("altright");
          if (altgr) repaintBody();
        },
        () => {},
      );
    }
    // What the core already has is old: only what comes after it counts.
    void Promise.allSettled([api.historyList({ limit: 1 }), api.agentHistoryList({ limit: 1 })]).then(([items, turns]) => {
      if (token !== lessonToken || !lesson) return;
      lesson = {
        ...lesson,
        baselineItem: items.status === "fulfilled" ? (items.value.items[0]?.id ?? 0) : lesson.baselineItem,
        baselineTurn: turns.status === "fulfilled" ? (turns.value.turns[0]?.id ?? 0) : lesson.baselineTurn,
      };
    });
  }

  /** Gives the lesson what was heard and draws what changed. */
  function feedLesson(event: PracticeEvent): void {
    if (!lesson || destroyed) return;
    const before = lesson;
    lesson = feed(lesson, event, { mode: device().dictation_mode });
    if (lesson === before) return;
    if (lesson.phase === "ok" && before.phase !== "ok") dispatch({ type: "LESSON_OK" });
    const retryShown = (l: Lesson): boolean => l.phase === "failed" || (l.phase === "idle" && l.miss !== null);
    if (lesson.item !== before.item || lesson.turn !== before.turn || retryShown(lesson) !== retryShown(before)) {
      repaintBody();
      return;
    }
    const slot = root.querySelector<HTMLElement>("[data-ob-status]");
    if (slot) slot.innerHTML = lessonStatus(lessonView(lesson));
  }

  const timeNow = (): number => Date.now();

  /** The newest history item, looked for until the lesson's own turns up. */
  function lookForItem(token: number): void {
    void lookUp(
      async () => (await api.historyList({ limit: 1 })).items[0] ?? null,
      (item) => lesson !== null && isNewItem(lesson, item),
    ).then((item) => {
      if (token === lessonToken && item) feedLesson({ type: "item", item });
    });
  }

  function lookForTurn(token: number): void {
    void lookUp(
      async () => (await api.agentHistoryList({ limit: 1 })).turns[0] ?? null,
      (turn) => lesson !== null && isNewTurn(lesson, turn),
    ).then((turn) => {
      if (token === lessonToken && turn) feedLesson({ type: "turn", turn });
    });
  }

  function onAppEvent(event: MainEvent): void {
    if (destroyed) return;
    switch (event.type) {
      case "Level": {
        setMeter(root.querySelector(".meter"), event.value);
        if (flow.step === "mic") {
          const reading = heardAfter(heardSince, event.value, timeNow());
          heardSince = reading.since;
          if (reading.heard && !heard) {
            heard = true;
            paintMicState();
          }
        }
        break;
      }
      case "Hotkey":
        if (flow.step !== "practice") break;
        hook = hookAfter(hook, event.kind);
        paintCapsNow();
        feedLesson({ type: "main", event, now: timeNow() });
        break;
      case "Dictation":
        if (flow.step !== "practice") break;
        feedLesson({ type: "main", event, now: timeNow() });
        if (event.phase === "pasted" && !event.agent) lookForItem(lessonToken);
        break;
      case "AgentTurn":
        if (flow.step === "practice") lookForTurn(lessonToken);
        break;
      default:
        download.handle(event);
    }
  }

  const stopEvents = app.onEvent(onAppEvent);

  function paintCapsNow(): void {
    paintCaps(root, litKeys(held, hook, dictationParts(), agentParts()));
  }

  /** The keys the window sees: they light the caps at once, and tell whether the question key is heard. */
  function onKey(e: KeyboardEvent): void {
    if (flow.step !== "practice" || !e.code) return;
    const code = canonicalKey(e.code);
    if (e.type === "keydown") held.add(code);
    else held.delete(code);
    paintCapsNow();
    if (e.type === "keydown" && lesson && (lesson.id === "ask" || lesson.id === "act") && allDown(held, agentParts())) {
      feedLesson({ type: "combo", now: timeNow() });
      clearTimeout(conflictTimer);
      conflictTimer = setTimeout(() => {
        if (!lesson || !conflictSuspected(lesson, timeNow())) return;
        conflict = true;
        repaintBody();
      }, CONFLICT_MS + 50);
    }
  }

  async function pickMode(value: string): Promise<void> {
    if (value !== "toggle" && value !== "hold") return;
    try {
      await app.setDevice({ dictation_mode: value as DictationMode });
    } catch (err) {
      toaster.info(describeError(err));
    }
    if (!destroyed && lesson) {
      lesson = startLesson(lesson.id, timeNow(), lesson.baselineItem, lesson.baselineTurn);
      repaintBody();
    }
  }

  function retryNow(): void {
    if (!lesson) return;
    lesson = retryLesson(lesson, timeNow());
    conflict = false;
    repaintBody();
  }

  async function changeKey(field: "dictation" | "agent"): Promise<void> {
    const cell = root.querySelector<HTMLElement>("[data-ob-caps]");
    if (!cell) return;
    // The practice is paused while the key is recorded: the recorder needs the keys for itself.
    await setPractice(false);
    const name: HotkeyField = field === "agent" ? "agent_keys" : "dictation_keys";
    await recording.start(name, cell);
  }

  // ---------- events ----------

  root.addEventListener("click", (e) => {
    if (destroyed) return;
    const target = e.target as Element;

    const choose = target.closest<HTMLElement>(".sel");
    if (choose) {
      if (menuIsOpenFor(choose)) {
        closeMenu();
        return;
      }
      switch (choose.dataset.ob) {
        case "mic":
          openMicrophoneMenu(choose);
          void refreshMicrophones(choose);
          break;
        case "lang":
          openMenu(choose, UI_LANGUAGES, device().ui_language, (value) => void app.setDevice({ ui_language: value as "zh-TW" | "en" | "ja" }).catch((err) => toaster.info(describeError(err))));
          break;
        case "mode":
          openMenu(choose, DICTATION_MODES, device().dictation_mode, (value) => void pickMode(value));
          break;
      }
      return;
    }

    const ability = target.closest<HTMLElement>("[data-ability]");
    if (ability) {
      dispatch({ type: "TICK", id: ability.dataset.ability as AbilityId });
      return;
    }

    if (flow.step === "telegram" && messaging.click(target)) return;

    const button = target.closest<HTMLElement>("[data-ob]");
    switch (button?.dataset.ob) {
      case "next":
        void next();
        break;
      case "back":
        back();
        break;
      case "skip":
        void skip();
        break;
      case "finish-key":
        void finish(true);
        break;
      case "key-paste":
        void pasteKey();
        break;
      case "key-replace":
        replacingKey = true;
        keyError = null;
        paintKeyGroup();
        root.querySelector<HTMLElement>("#ob-key-input")?.focus();
        break;
      case "key-cancel":
        replacingKey = false;
        keyError = null;
        paintKeyGroup();
        break;
      case "model-retry":
        void download.start();
        break;
      case "mic-settings":
        void api.openLink("ms-settings:privacy-microphone").catch((err) => toaster.info(describeError(err)));
        break;
      case "conn-login":
        void connectService(button.dataset.service ?? "");
        break;
      case "conn-cancel":
        void api.integrationsConnectCancel().catch(() => {});
        break;
      case "conn-reload":
        void loadServices();
        break;
      case "lesson-retry":
        retryNow();
        break;
      case "change-key":
        void changeKey(button.dataset.field === "agent" ? "agent" : "dictation");
        break;
    }
}, { signal });

  // The limit is saved when its field is left or Enter is pressed. The key is not: it is sent by
  // Enter or 貼上 only, never by leaving the field (see keyfield.ts).
  root.addEventListener("change", (e) => {
    if (!destroyed && (e.target as HTMLInputElement).id === "ob-limit") void commitLimit();
  }, { signal });
  root.addEventListener("keydown", (e) => {
    if (destroyed) return;
    openMenuOnArrow(e);
    if (flow.step === "telegram" && (e.target as HTMLElement).id === "msg-token") void messaging.key(e);
  }, { signal });
  wireKeyField(root, { inputId: "ob-key-input", pasteSelector: '[data-ob="key-paste"]', submit: (value) => void submitKey(value), signal });

  root.addEventListener("input", (e) => {
    if (destroyed) return;
    const input = e.target as HTMLInputElement;
    if (input.id === "ob-limit") {
      limitText = input.value;
      limitEdited = true;
    } else if (flow.step === "telegram") {
      messaging.input(input);
    }
}, { signal });

  // The microphone is listened to only while its step is on screen and the window is in use; the
  // box of a dictation lesson gets the focus back when the window does, so that a paste lands in it.
  function onFocus(): void {
    windowFocused = true;
    syncMonitor();
    if (flow.step === "practice") root.querySelector<HTMLElement>("#ob-box")?.focus({ preventScroll: true });
  }
  function onBlur(): void {
    windowFocused = false;
    held.clear();
    paintCapsNow();
    syncMonitor();
  }
  window.addEventListener("focus", onFocus);
  window.addEventListener("blur", onBlur);
  window.addEventListener("keydown", onKey, true);
  window.addEventListener("keyup", onKey, true);
  document.addEventListener("visibilitychange", syncMonitor);

  const stopLocale = onLocaleChange(() => {
    if (destroyed) return;
    const focus = focusSelector(root);
    const typed = Array.from(root.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>("input, textarea")).map((input) => [input.id, input.value] as const);
    render(true);
    for (const [id, value] of typed) {
      const input = id ? root.querySelector<HTMLInputElement | HTMLTextAreaElement>(`#${CSS.escape(id)}`) : null;
      if (input) input.value = value;
    }
    if (focus) root.querySelector<HTMLElement>(focus)?.focus({ preventScroll: true });
  });

  render();
  // The speech models come in the background from the start: the microphone step shows how far they are.
  if (mode === "first") void download.start();
  void loadReadiness();
}
