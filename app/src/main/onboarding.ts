// First run: the main window is a guide of four steps until it has been gone through.
//   0  Raphael appears and says the setup begins.
//   1  The OpenRouter key (tested at once) and the monthly limit; the key can be skipped.
//   2  The microphone, with the meter; the speech models are fetched meanwhile, with progress.
//   3  A box to try a real dictation in. The main window is the app in front, so the paste lands in it.
// 「完成」 saves `onboarded: true`. The pure rules are exported and tested; the window code is below.

import { onLocaleChange, t, translatedMessage } from "./i18n";

import * as api from "./api";
import { type DictationMode, keylineAction, tryBoxText } from "./dictation";
import { describeError, formatSeconds, keyReasonText, parseLimit } from "./format";
import { wireKeyField } from "./keyfield";
import { bindingLabels } from "./keys";
import { MonitorSwitch } from "./monitor";
import { automaticMicrophoneLabel } from "./microphone";
import { ModelDownload, type ModelPhase, formatProgress } from "./models";
import { Stage } from "./stage";
import type { AppContext, AudioDevices, Status } from "./types";
import { keyEntryRow, limitRow, micRow as micRowShared } from "./setup-rows";
import { type Choice, btn, closeMenu, esc, group, keys, menuIsOpenFor, note, openMenu, openMenuOnArrow, row, setMeter, st, toaster } from "./ui";

// ---------- the rules ----------

export type Step = 0 | 1 | 2 | 3;

export const STEP_COUNT = 4;

export interface KeyProgress {
  /** A key was tested and stored (or one was stored before). */
  keySaved: boolean;
  /** 「先跳過」 was chosen. */
  skipKey: boolean;
}

/** Where 「下一步」 leads. The key step stays where it is until there is a key or a skip. */
export function next(step: Step, opts: KeyProgress): Step | "done" {
  if (step === 3) return "done";
  if (step === 1 && !opts.keySaved && !opts.skipKey) return 1;
  return (step + 1) as Step;
}

export function prev(step: Step): Step {
  return Math.max(0, step - 1) as Step;
}

/** Whether 「下一步」 does anything on `step`. */
export function canAdvance(step: Step, opts: KeyProgress): boolean {
  return next(step, opts) !== step;
}

/** 「第 1 步，共 4 步」 */
export const stepCaption = (step: Step): string => t("onboarding.001", { p0: step + 1, p1: STEP_COUNT });

const PRIMARY_LABELS = () => [t("onboarding.002"), t("onboarding.003"), t("onboarding.003"), t("onboarding.004")] as const;

/** What the main button says. */
export const primaryLabel = (step: Step): string => PRIMARY_LABELS()[step];

export type LimitAction = { kind: "keep" } | { kind: "save"; value: number } | { kind: "invalid" };

/** What the text in the limit field means for the limit that is set now. */
export function limitAction(text: string, current: number): LimitAction {
  const value = parseLimit(text);
  if (value === null) return { kind: "invalid" };
  return value === current ? { kind: "keep" } : { kind: "save", value };
}

const AUTO_MICROPHONE = "";

/** The microphones to choose from: the automatic choice, those found, and the saved one even if it is gone. */
export function micChoices(devices: AudioDevices | null, saved: string | null): Choice[] {
  const choices: Choice[] = [
    { value: AUTO_MICROPHONE, get label() { return automaticMicrophoneLabel(devices); } },
    ...(devices?.devices ?? []).map((d) => ({ value: d.name, label: d.name })),
  ];
  if (saved !== null && !choices.some((c) => c.value === saved)) choices.push({ value: saved, get label() { return t("mic.disconnected", { p0: saved }); } });
  return choices;
}

/** How long the last dictation took, if it was made at or after `sinceMs` (that is, in the try box). */
export function tryTiming(last: Status["last"], sinceMs: number): string | null {
  if (!last || last.created_at_ms < sinceMs) return null;
  return t("onboarding.007", { p0: () => formatSeconds(last.stt_ms + last.llm_ms) });
}

/** How soon after a dictation the history is looked at again, if it was not in it yet. */
export const TIMING_RETRY_MS = 1000;

/**
 * The line about how long the dictation in the try box took. The history row may not be written
 * yet when the box sees the paste, so a lookup that finds nothing is made once more after
 * `TIMING_RETRY_MS`. `wait` is how the time passes (a test gives its own).
 */
export async function timingWithRetry(
  fetchLast: () => Promise<Status["last"]>,
  sinceMs: number,
  wait: (ms: number) => Promise<void> = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
): Promise<string | null> {
  for (let attempt = 0; attempt < 2; attempt++) {
    if (attempt > 0) await wait(TIMING_RETRY_MS);
    try {
      const text = tryTiming(await fetchLast(), sinceMs);
      if (text) return text;
    } catch {
      // the core did not answer; the second look may be luckier
    }
  }
  return null;
}

export interface Advance {
  /** Where 「下一步」 (or 先跳過) leads; null stays on the step. */
  to: Step | "done" | null;
  /** Whether the key was skipped, now. A skip counts only when the step is really left. */
  skipKey: boolean;
}

/**
 * What 「下一步」 does, or 先跳過 with `skip`. On the key step a limit typed but not yet saved is
 * saved first (`commitLimit`, true if the limit is fine); a bad limit keeps the guide where it is,
 * and then a skip is not counted: it would otherwise unlock 「下一步」 behind the person's back.
 */
export async function advanceFrom(
  step: Step,
  opts: KeyProgress,
  skip: boolean,
  commitLimit: () => Promise<boolean>,
): Promise<Advance> {
  if (step === 1 && !(await commitLimit())) return { to: null, skipKey: opts.skipKey };
  const skipKey = opts.skipKey || (step === 1 && skip);
  const to = next(step, { keySaved: opts.keySaved, skipKey });
  return { to: to === step ? null : to, skipKey };
}

/** The dictation keys in words: 「右 Alt」, or 「右 Alt 或 F9」 with two. */
export function dictationKeysText(bindings: string[]): string {
  return bindings.map((binding) => bindingLabels(binding).join(" + ")).join(t("onboarding.008"));
}

// ---------- the pieces of the steps ----------

/** The try box asks how long the dictation took this long after the last change in the box... */
const TRY_TIMING_DELAY_MS = 300;
/** ...and the model bar is drawn at most this often. */
const MODEL_PAINT_MS = 120;

const keyline = (bindings: string[], mode: DictationMode): string =>
  `<div class="keyline"><div>${bindings.map((binding) => keys(bindingLabels(binding))).join(t("common.or"))}${esc(keylineAction(mode))}</div>` +
  t("onboarding.010", { p0: keys(["Esc"]) });

/** The model row's control: a bar while fetching, a pulsing dot while unpacking. */
function modelControl(phase: ModelPhase): string {
  switch (phase.kind) {
    case "idle":
    case "checking":
      return st("idle", t("common.checking"));
    case "downloading": {
      if (phase.total <= 0) return st("idle", t("onboarding.012"));
      const percent = Math.min(100, Math.round((phase.done / phase.total) * 100));
      return (
        t("onboarding.013", { p0: percent }) +
        `<i style="width:${((phase.done / phase.total) * 100).toFixed(1)}%"></i></span>` +
        `<span class="val num">${esc(formatProgress(phase.done, phase.total))}</span>`
      );
    }
    case "installing":
      return t("onboarding.014");
    case "ready":
      return st("ok", t("onboarding.004"));
    case "failed":
      return st("err", t("onboarding.015")) + btn(t("onboarding.016"), "sm", ' data-ob="model-retry"');
  }
}

/** The model line under the try box: why dictation will not work yet. */
function modelLine(phase: ModelPhase): string {
  switch (phase.kind) {
    case "idle":
    case "checking":
      return st("idle", t("onboarding.017"));
    case "downloading": {
      const percent = phase.total > 0 ? `（${Math.min(100, Math.round((phase.done / phase.total) * 100))}%）` : "";
      return st("idle", t("onboarding.018", { p0: percent }));
    }
    case "installing":
      return t("onboarding.019");
    case "ready":
      return "";
    case "failed":
      return st("err", t("onboarding.020", { p0: () => phase.reason })) + btn(t("onboarding.016"), "sm", ' data-ob="model-retry"');
  }
}

/** A selector for the element that has focus inside `host`, to give it focus again after a redraw. */
function focusSelector(host: HTMLElement): string {
  const active = document.activeElement;
  if (!(active instanceof HTMLElement) || !host.contains(active)) return "";
  if (active.id) return `#${active.id}`;
  return active.dataset.ob ? `[data-ob="${active.dataset.ob}"]` : "";
}

// ---------- the window ----------

/**
 * Draws the guide into `root` and runs it. `app` gives the device settings and the events of the
 * core (the microphone level, the model download). When 「完成」 has saved `onboarded: true`, the
 * guide takes itself down and calls `onDone`.
 */
export function mountOnboarding(root: HTMLElement, onDone: () => void, app: AppContext): void {
  let step: Step = 0;
  let keySaved = false;
  let skipKey = false;
  /** The name OpenRouter gives the key; null for a key that was stored before this run. */
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
  let devices: AudioDevices | null = null;
  let stage: Stage | null = null;
  let advancing = false;
  let finishing = false;
  let destroyed = false;
  let tryOpenedAt = 0;
  let timingMessage: (() => string) | null = null;
  let tryTimer: ReturnType<typeof setTimeout> | undefined;
  let modelTimer: ReturnType<typeof setTimeout> | undefined;
  let windowFocused = true;
  let microphoneTimer: ReturnType<typeof setInterval> | undefined;

  const monitor = new MonitorSwitch(api.audioMonitor);
  const download = new ModelDownload({ status: api.modelsStatus, ensure: api.modelsEnsure }, onModelChange);

  // ---------- drawing ----------

  const dictationBindings = (): string[] => app.device().dictation_keys;

  function micRow(): string {
    const saved = app.device().microphone;
    return micRowShared({
      choices: micChoices(devices, saved),
      current: saved ?? AUTO_MICROPHONE,
      selAttrs: ' data-ob="mic"',
      meter: { place: "before" },
      row: { attrs: ' id="ob-mic-row"' },
    });
  }

  function keyRow(): string {
    if (keySaved && !replacingKey) {
      return row(
        t("common.apiKey"),
        keyName === null ? t("onboarding.023") : t("onboarding.024", { p0: esc(keyName) }),
        `<span class="val num">••••••••</span>${st("ok", keyName === null ? t("onboarding.025") : t("onboarding.026"))}${btn(t("onboarding.027"), "sm", ' data-ob="key-replace"')}`,
      );
    }
    return keyEntryRow({
      desc: t("onboarding.028"),
      fieldWidth: 220,
      inputId: "ob-key-input",
      hook: (name) => ` data-ob="${name}"`,
      busy: keyBusy,
      invalid: keyError !== null,
      canCancel: keySaved,
    });
  }

  function keyGroup(): string {
    return (
      group(
        [
          keyRow(),
          ...(keyError ? [note(st("err", keyError()), ' role="alert"')] : []),
          limitRow({ text: limitText, fieldWidth: 96, id: "ob-limit", invalid: limitError !== null }),
          ...(limitError ? [note(st("err", limitError()), ' role="alert"')] : []),
        ],
        t("onboarding.034"),
      ) +
      (keySaved ? "" : t("onboarding.035"))
    );
  }

  function bodyHTML(): string {
    switch (step) {
      case 0:
        return (
          `<div class="onboarding-brand"><span>Raphael</span></div>` +
          `<div class="stage onboarding-stage"><canvas class="emb" aria-hidden="true"></canvas><div class="subs" aria-live="polite"></div></div>` +
          t("onboarding.036") +
          t("onboarding.037")
        );
      case 1:
        return (
          t("onboarding.038") +
          t("onboarding.039") +
          `<div id="ob-keygroup">${keyGroup()}</div>`
        );
      case 2:
        return (
          t("onboarding.040") +
          group([
            micRow(),
            row(t("onboarding.041"), t("onboarding.042"), `<span class="ctl-model" data-model="ctl"></span>`),
            `<div class="note" data-model="note" hidden></div>`,
          ])
        );
      case 3:
        return (
          t("onboarding.043") +
          `<p>${esc(tryBoxText(app.device().dictation_mode, dictationKeysText(dictationBindings())))}</p>` +
          t("onboarding.044") +
          `<div class="ob-tryline"><span class="val" id="ob-try-t" role="status"></span></div>` +
          `<div class="ob-modelline" data-model="line" hidden></div>` +
          keyline(dictationBindings(), app.device().dictation_mode)
        );
    }
  }

  function paintFoot(): void {
    const foot = root.querySelector<HTMLElement>(".ob-foot");
    if (!foot) return;
    const focus = focusSelector(foot);
    const opts = { keySaved, skipKey };
    foot.innerHTML =
      `<span class="ob-count">${stepCaption(step)}</span>` +
      (step > 0 ? btn(t("onboarding.045"), "", ' data-ob="back"') : "") +
      btn(primaryLabel(step), "pri", ` data-ob="next"${canAdvance(step, opts) && !finishing ? "" : " disabled"}`);
    if (focus) foot.querySelector<HTMLElement>(focus)?.focus({ preventScroll: true });
  }

  function render(): void {
    stage?.dispose();
    stage = null;
    closeMenu();
    const dots = Array.from({ length: STEP_COUNT }, (_, i) => `<i class="${i < step ? "done" : i === step ? "now" : ""}"></i>`).join("");
    root.innerHTML =
      `<div class="ob-steps" aria-hidden="true">${dots}</div>` +
      `<div class="ob-body"><div class="ob-col">${bodyHTML()}</div></div>` +
      `<div class="ob-foot"></div>`;
    paintFoot();
    enterStep();
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

  function paintMicRow(): void {
    const current = root.querySelector<HTMLElement>("#ob-mic-row");
    if (!current) return;
    const focus = focusSelector(current);
    current.outerHTML = micRow();
    if (focus) root.querySelector<HTMLElement>(`#ob-mic-row ${focus}`)?.focus();
  }

  function paintModel(): void {
    const phase = download.phase;
    root.querySelectorAll<HTMLElement>("[data-model]").forEach((node) => {
      switch (node.dataset.model) {
        case "ctl":
          node.innerHTML = modelControl(phase);
          break;
        case "note":
          node.hidden = phase.kind !== "failed";
          node.innerHTML = phase.kind === "failed" ? st("err", phase.reason) : "";
          break;
        case "line":
          node.hidden = phase.kind === "ready";
          node.innerHTML = modelLine(phase);
          break;
      }
    });
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

  // ---------- going through the steps ----------

  function syncMonitor(): void {
    const want = !destroyed && step === 2 && !document.hidden && windowFocused;
    monitor.set(want);
    if (want && microphoneTimer === undefined) {
      microphoneTimer = setInterval(() => {
        const anchor = root.querySelector<HTMLElement>('[data-ob="mic"]');
        if (anchor) void refreshMicrophones(anchor, false);
      }, 750);
    } else if (!want) {
      clearInterval(microphoneTimer);
      microphoneTimer = undefined;
      microphoneReads++;
    }
  }

  function enterStep(): void {
    syncMonitor();
    switch (step) {
      case 0: {
        const canvas = root.querySelector<HTMLCanvasElement>("canvas.emb");
        const subs = root.querySelector<HTMLElement>(".subs");
        if (canvas && subs) {
          stage = new Stage(canvas, subs, () => {});
          stage.play();
          stage.show({ text: t("report.firstRun") });
        }
        break;
      }
      case 1:
        void loadKeyStep();
        break;
      case 2:
        void download.start();
        void loadDevices();
        paintModel();
        break;
      case 3:
        tryOpenedAt = Date.now();
        void download.start();
        paintModel();
        root.querySelector<HTMLTextAreaElement>("#ob-try")?.focus();
        return;
    }
    root.querySelector<HTMLElement>("h2")?.focus();
  }

  function go(to: Step): void {
    step = to;
    // A replacement that was being typed is given up when the step is left, not kept for later.
    replacingKey = false;
    keyError = null;
    render();
  }

  async function advance(skip = false): Promise<void> {
    if (advancing || finishing) return;
    advancing = true;
    try {
      const result = await advanceFrom(step, { keySaved, skipKey }, skip, commitLimit);
      skipKey = result.skipKey;
      if (result.to === null) {
        paintFoot();
        if (limitError) root.querySelector<HTMLElement>("#ob-limit")?.focus();
        return;
      }
      if (result.to === "done") await finish();
      else go(result.to);
    } finally {
      advancing = false;
    }
  }

  async function finish(): Promise<void> {
    finishing = true;
    paintFoot();
    try {
      await app.setDevice({ onboarded: true });
    } catch (err) {
      toaster.info(describeError(err));
      finishing = false;
      paintFoot();
      return;
    }
    destroy();
    onDone();
  }

  function destroy(): void {
    destroyed = true;
    syncMonitor();
    stage?.dispose();
    stage = null;
    closeMenu();
    clearTimeout(tryTimer);
    clearTimeout(modelTimer);
    stopEvents();
    stopLocale();
    window.removeEventListener("focus", onFocus);
    window.removeEventListener("blur", onBlur);
    document.removeEventListener("visibilitychange", syncMonitor);
    root.replaceChildren();
  }

  // ---------- step 1: the key and the limit ----------

  /** Reads whether a key is stored and what the limit is. Tried again next time if the core did not answer. */
  async function loadKeyStep(): Promise<void> {
    if (keyLoaded) return;
    const [stored, settings] = await Promise.allSettled([api.secretsStatus(), api.settingsGet()]);
    if (destroyed) return;
    if (stored.status === "fulfilled" && stored.value.has_key) keySaved = true;
    if (settings.status === "fulfilled") {
      limitCurrent = settings.value.settings.monthly_limit_usd;
      if (!limitEdited) limitText = String(limitCurrent);
    }
    keyLoaded = stored.status === "fulfilled" && settings.status === "fulfilled";
    if (step === 1) paintKeyGroup();
    paintFoot();
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
        keySaved = true;
        keyName = result.label;
        replacingKey = false;
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
    paintKeyGroup();
    paintFoot();
    if (keySaved && !replacingKey && keyFocused && !destroyed && step === 1) {
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
      toaster.info(t("onboarding.048"));
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
    if (!destroyed && step === 1) paintKeyGroup();
    return limitError === null;
  }

  // ---------- step 2: the microphone ----------

  async function loadDevices(): Promise<void> {
    try {
      devices = await api.audioDevices();
    } catch {
      devices = null;
    }
    if (!destroyed && step === 2) paintMicRow();
  }

  /** Which reading of the microphones is the latest, so that an older, slower one is dropped. */
  let microphoneReads = 0;

  function openMicrophoneMenu(anchor: HTMLElement): void {
    const current = app.device().microphone;
    openMenu(anchor, micChoices(devices, current), current ?? AUTO_MICROPHONE, (value) => void pickMicrophone(value));
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
    if (destroyed || step !== 2 || mine !== microphoneReads) return;
    const saved = app.device().microphone;
    const before = JSON.stringify(micChoices(devices, saved));
    devices = fresh;
    if (JSON.stringify(micChoices(devices, saved)) === before) return;
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
    if (!destroyed && step === 2) paintMicRow();
  }

  // ---------- step 3: the try box ----------

  async function showTiming(): Promise<void> {
    const text = await timingWithRetry(async () => (await api.statusGet()).last, tryOpenedAt);
    const line = root.querySelector<HTMLElement>("#ob-try-t");
    if (line && text && !destroyed) {
      timingMessage = translatedMessage(text);
      line.textContent = timingMessage();
    }
  }

  // ---------- events ----------

  root.addEventListener("click", (e) => {
    const target = e.target as Element;

    const choose = target.closest<HTMLElement>(".sel");
    if (choose) {
      if (menuIsOpenFor(choose)) {
        closeMenu();
      } else {
        openMicrophoneMenu(choose);
        void refreshMicrophones(choose);
      }
      return;
    }

    switch (target.closest<HTMLElement>("[data-ob]")?.dataset.ob) {
      case "next":
        void advance();
        break;
      case "back":
        go(prev(step));
        break;
      case "skip":
        void advance(true);
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
    }
  });

  // The limit is saved when its field is left or Enter is pressed. The key is not: it is sent by
  // Enter or 貼上 only, never by leaving the field (see keyfield.ts).
  root.addEventListener("change", (e) => {
    if ((e.target as HTMLInputElement).id === "ob-limit") void commitLimit();
  });
  root.addEventListener("keydown", openMenuOnArrow);
  wireKeyField(root, { inputId: "ob-key-input", pasteSelector: '[data-ob="key-paste"]', submit: (value) => void submitKey(value) });

  root.addEventListener("input", (e) => {
    const input = e.target as HTMLInputElement;
    if (input.id === "ob-limit") {
      limitText = input.value;
      limitEdited = true;
    } else if (input.id === "ob-try") {
      clearTimeout(tryTimer);
      tryTimer = setTimeout(() => void showTiming(), TRY_TIMING_DELAY_MS);
    }
  });

  const stopEvents = app.onEvent((event) => {
    if (event.type === "Level") setMeter(root.querySelector(".meter"), event.value);
    else download.handle(event);
  });

  // The microphone is listened to only while step 2 is on screen and the window is in use; the
  // box of step 3 gets focus back when the window does, so that a paste lands in it.
  function onFocus(): void {
    windowFocused = true;
    syncMonitor();
    if (step === 3) root.querySelector<HTMLElement>("#ob-try")?.focus();
  }
  function onBlur(): void {
    windowFocused = false;
    syncMonitor();
  }
  window.addEventListener("focus", onFocus);
  window.addEventListener("blur", onBlur);
  document.addEventListener("visibilitychange", syncMonitor);

  const stopLocale = onLocaleChange(() => {
    if (destroyed) return;
    const focus = focusSelector(root);
    const typed = Array.from(root.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>("input, textarea"))
      .map((input) => [input.id, input.value] as const);
    const since = tryOpenedAt;
    render();
    tryOpenedAt = since;
    for (const [id, value] of typed) {
      const input = root.querySelector<HTMLInputElement | HTMLTextAreaElement>(`#${CSS.escape(id)}`);
      if (input) input.value = value;
    }
    const timing = root.querySelector<HTMLElement>("#ob-try-t");
    if (timing && timingMessage) timing.textContent = timingMessage();
    if (focus) root.querySelector<HTMLElement>(focus)?.focus({ preventScroll: true });
  });

  render();
}
