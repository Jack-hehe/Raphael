// First run (spec §12.4): the main window is a guide of four steps until it has been gone through.
//   0  Raphael appears and says the setup begins.
//   1  The OpenRouter key (tested at once) and the monthly limit; the key can be skipped.
//   2  The microphone, with the meter; the speech models are fetched meanwhile, with progress.
//   3  A box to try a real dictation in. The main window is the app in front, so the paste lands in it.
// 「完成」 saves `onboarded: true`. The pure rules are exported and tested; the window code is below.

import * as api from "./api";
import { type DictationMode, keylineAction, tryBoxText } from "./dictation";
import { describeError, formatSeconds, keyReasonText, parseLimit } from "./format";
import { wireKeyField } from "./keyfield";
import { bindingLabels } from "./keys";
import { MonitorSwitch } from "./monitor";
import { ModelDownload, type ModelPhase, formatProgress } from "./models";
import { Stage } from "./stage";
import type { AppContext, AudioDevices, Status } from "./types";
import { type Choice, btn, closeMenu, esc, fieldHTML, group, keys, menuIsOpenFor, meter, note, openMenu, row, sel, setMeter, st, toaster } from "./ui";

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
export const stepCaption = (step: Step): string => `第 ${step + 1} 步，共 ${STEP_COUNT} 步`;

const PRIMARY_LABELS = ["開始", "下一步", "下一步", "完成"] as const;

/** What the main button says. */
export const primaryLabel = (step: Step): string => PRIMARY_LABELS[step];

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
    { value: AUTO_MICROPHONE, label: "自動偵測" },
    ...(devices?.devices ?? []).map((d) => ({ value: d.name, label: d.name })),
  ];
  if (saved !== null && !choices.some((c) => c.value === saved)) choices.push({ value: saved, label: `${saved}（未連接）` });
  return choices;
}

/** How long the last dictation took, if it was made at or after `sinceMs` (that is, in the try box). */
export function tryTiming(last: Status["last"], sinceMs: number): string | null {
  if (!last || last.created_at_ms < sinceMs) return null;
  return `辨識與整理花了 ${formatSeconds(last.stt_ms + last.llm_ms)}`;
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
  return bindings.map((binding) => bindingLabels(binding).join(" + ")).join(" 或 ");
}

// ---------- the pieces of the steps ----------

const REPORT = {
  zh: "《告。初次啟動。開始初始設定。》",
  ja: "《告。初回起動を確認。初期設定を開始します。》",
} as const;

/** The try box asks how long the dictation took this long after the last change in the box... */
const TRY_TIMING_DELAY_MS = 300;
/** ...and the model bar is drawn at most this often. */
const MODEL_PAINT_MS = 120;

const keyline = (bindings: string[], mode: DictationMode): string =>
  `<div class="keyline"><div>${bindings.map((binding) => keys(bindingLabels(binding))).join('<span class="val">或</span>')}${esc(keylineAction(mode))}</div>` +
  `<div>${keys(["Esc"])}取消</div></div>`;

/** The model row's control: a bar while fetching, a pulsing dot while unpacking. */
function modelControl(phase: ModelPhase): string {
  switch (phase.kind) {
    case "idle":
    case "checking":
      return st("idle", "檢查中…");
    case "downloading": {
      if (phase.total <= 0) return st("idle", "準備下載…");
      const percent = Math.min(100, Math.round((phase.done / phase.total) * 100));
      return (
        `<span class="bar ob-bar" role="progressbar" aria-label="語音辨識模型下載進度" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${percent}">` +
        `<i style="width:${((phase.done / phase.total) * 100).toFixed(1)}%"></i></span>` +
        `<span class="val num">${esc(formatProgress(phase.done, phase.total))}</span>`
      );
    }
    case "installing":
      return `<span class="st"><span class="dot busy"></span>安裝中…</span>`;
    case "ready":
      return st("ok", "完成");
    case "failed":
      return st("err", "下載失敗") + btn("重試", "sm", ' data-ob="model-retry"');
  }
}

/** The model line under the try box: why dictation will not work yet. */
function modelLine(phase: ModelPhase): string {
  switch (phase.kind) {
    case "idle":
    case "checking":
      return st("idle", "正在檢查語音辨識模型…");
    case "downloading": {
      const percent = phase.total > 0 ? `（${Math.min(100, Math.round((phase.done / phase.total) * 100))}%）` : "";
      return st("idle", `語音辨識模型還在下載${percent}，好了才能試`);
    }
    case "installing":
      return `<span class="st"><span class="dot busy"></span>語音辨識模型安裝中…，好了才能試</span>`;
    case "ready":
      return "";
    case "failed":
      return st("err", `語音辨識模型沒有裝好：${phase.reason}`) + btn("重試", "sm", ' data-ob="model-retry"');
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
  let keyError: string | null = null;
  let keyLoaded = false;
  let limitCurrent = 10;
  let limitText = "10";
  let limitEdited = false;
  let limitError: string | null = null;
  let limitJob: Promise<boolean> = Promise.resolve(true);
  let devices: AudioDevices | null = null;
  let stage: Stage | null = null;
  let advancing = false;
  let finishing = false;
  let destroyed = false;
  let tryOpenedAt = 0;
  let tryTimer: ReturnType<typeof setTimeout> | undefined;
  let modelTimer: ReturnType<typeof setTimeout> | undefined;
  let windowFocused = true;

  const monitor = new MonitorSwitch(api.audioMonitor);
  const download = new ModelDownload({ status: api.modelsStatus, ensure: api.modelsEnsure }, onModelChange);

  // ---------- drawing ----------

  const dictationBindings = (): string[] => app.device().dictation_keys;

  function micRow(): string {
    const saved = app.device().microphone;
    return row(
      "麥克風",
      "",
      meter() + sel(micChoices(devices, saved), saved ?? AUTO_MICROPHONE, "麥克風", ' data-ob="mic"'),
      { attrs: ' id="ob-mic-row"' },
    );
  }

  function keyRow(): string {
    if (keySaved && !replacingKey) {
      return row(
        "金鑰",
        keyName === null ? "已存在 Windows 認證管理員" : `名稱「${esc(keyName)}」`,
        `<span class="val num">••••••••</span>${st("ok", keyName === null ? "已設定" : "有效")}${btn("更換", "sm", ' data-ob="key-replace"')}`,
      );
    }
    return row(
      "金鑰",
      "貼上後按 Enter 或「貼上」，馬上測試",
      fieldHTML("sk-or-v1-…", "", { w: 220, label: "OpenRouter 金鑰", id: "ob-key-input", type: "password", invalid: keyError !== null }) +
        btn(keyBusy ? "檢查中…" : "貼上", "pri", ` data-ob="key-paste"${keyBusy ? " disabled" : ""}`) +
        (keySaved ? btn("取消", "", ` data-ob="key-cancel"${keyBusy ? " disabled" : ""}`) : ""),
    );
  }

  function keyGroup(): string {
    return (
      group(
        "",
        [
          keyRow(),
          ...(keyError ? [note(st("err", keyError), ' role="alert"')] : []),
          row(
            "每月上限",
            "到了就只聽寫、不整理",
            fieldHTML("", limitText, { w: 96, pre: "US$", label: "每月上限", num: true, id: "ob-limit", invalid: limitError !== null }),
          ),
          ...(limitError ? [note(st("err", limitError), ' role="alert"')] : []),
        ],
        "還沒有金鑰？到 openrouter.ai 建立一把。建議也在 OpenRouter 替金鑰設上限，當作最後一道防線。",
      ) +
      (keySaved ? "" : `<p class="ob-skip"><button type="button" class="link" data-ob="skip">先跳過，聽寫只貼上未整理的文字</button></p>`)
    );
  }

  function bodyHTML(): string {
    switch (step) {
      case 0:
        return (
          `<div class="stage"><canvas class="emb" aria-hidden="true"></canvas><div class="subs" aria-live="polite"></div></div>` +
          `<h2 tabindex="-1">開始設定</h2>` +
          `<p>大約 2 分鐘：連上 OpenRouter、選麥克風，再試一次聽寫。選麥克風的時候，語音辨識模型會同時在背景下載。</p>`
        );
      case 1:
        return (
          `<h2 tabindex="-1">連上 OpenRouter</h2>` +
          `<p>拉斐爾用 OpenRouter 整理聽寫。金鑰只存在這台電腦的 Windows 認證管理員。</p>` +
          `<div id="ob-keygroup">${keyGroup()}</div>`
        );
      case 2:
        return (
          `<h2 tabindex="-1">選擇麥克風</h2><p>對著麥克風說幾句話，音量條會跟著跳。</p>` +
          group("", [
            micRow(),
            row("語音辨識模型", "只下載一次，之後完全在這台電腦上執行", `<span class="ctl-model" data-model="ctl"></span>`),
            `<div class="note" data-model="note" hidden></div>`,
          ])
        );
      case 3:
        return (
          `<h2 tabindex="-1">試一次聽寫</h2>` +
          `<p>${esc(tryBoxText(app.device().dictation_mode, dictationKeysText(dictationBindings())))}</p>` +
          `<textarea class="trybox" id="ob-try" rows="4" placeholder="在這裡試試看。" aria-label="試用聽寫的框" spellcheck="false"></textarea>` +
          `<div class="ob-tryline"><span class="val" id="ob-try-t" role="status"></span></div>` +
          `<div class="ob-modelline" data-model="line" hidden></div>` +
          keyline(dictationBindings(), app.device().dictation_mode)
        );
    }
  }

  function paintFoot(): void {
    const foot = root.querySelector<HTMLElement>(".ob-foot");
    if (!foot) return;
    const opts = { keySaved, skipKey };
    foot.innerHTML =
      `<span class="ob-count">${stepCaption(step)}</span>` +
      (step > 0 ? btn("上一步", "", ' data-ob="back"') : "") +
      btn(primaryLabel(step), "pri", ` data-ob="next"${canAdvance(step, opts) && !finishing ? "" : " disabled"}`);
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
    monitor.set(!destroyed && step === 2 && !document.hidden && windowFocused);
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
          stage.show({ zh: REPORT.zh, ja: REPORT.ja });
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
        toaster.info("金鑰有效，已存進 Windows 認證管理員");
      } else {
        keyError = keyReasonText(result.reason);
      }
    } catch (err) {
      keyError = describeError(err);
    }
    keyBusy = false;
    paintKeyGroup();
    paintFoot();
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
      toaster.info("請在欄位裡按 Ctrl+V 貼上金鑰");
      return;
    }
    if (copied.trim()) {
      void submitKey(copied);
    } else {
      input?.focus();
      toaster.info("剪貼簿裡沒有文字，請先複製金鑰");
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
      limitError = "請輸入 0 以上的數字";
    } else {
      limitError = null;
      if (action.kind === "save") {
        try {
          limitCurrent = (await api.settingsSet("monthly_limit_usd", action.value)).settings.monthly_limit_usd;
          limitText = String(limitCurrent);
        } catch (err) {
          limitError = describeError(err);
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
    if (line && text && !destroyed) line.textContent = text;
  }

  // ---------- events ----------

  root.addEventListener("click", (e) => {
    const target = e.target as Element;

    const choose = target.closest<HTMLElement>(".sel");
    if (choose) {
      if (menuIsOpenFor(choose)) closeMenu();
      else openMenu(choose, micChoices(devices, app.device().microphone), app.device().microphone ?? AUTO_MICROPHONE, (value) => void pickMicrophone(value));
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

  render();
}
