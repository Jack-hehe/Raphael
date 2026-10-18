// Settings: one page, only what matters (spec §12.3). A change takes effect at once; there is no
// save button. The settings of the brain (limit, model, language, retention) go to the core with
// `settings.set`; the settings of this computer (hotkeys, microphone, emblem, theme, autostart) go
// to the Rust side with `device_settings_set`.

import { getVersion } from "@tauri-apps/api/app";
import * as api from "../api";
import { DICTATION_MODES, isDictationMode, modeDescription } from "../dictation";
import { KEEP_FOREVER_DAYS, describeError, formatRetention, keyReasonText, parseLimit } from "../format";
import { wireKeyField } from "../keyfield";
import { KeyRecorder, bindingFromCodes, bindingLabels, keyLabel, validateCodes } from "../keys";
import { MonitorSwitch } from "../monitor";
import type { AppContext, AudioDevices, BrainSettings, DeviceSettings, Page, Status } from "../types";
import {
  type Choice,
  btn,
  closeMenu,
  esc,
  fieldHTML,
  group,
  keys,
  menuIsOpenFor,
  meter,
  note,
  openMenu,
  row,
  seg,
  sel,
  setMeter,
  st,
  tg,
  toaster,
} from "../ui";

const LANGUAGES: Choice[] = [
  { value: "auto", label: "自動" },
  { value: "zh", label: "中文" },
  { value: "ja", label: "日文" },
  { value: "en", label: "英文" },
];
const MODELS: Choice[] = [
  { value: "google/gemini-3.1-flash-lite", label: "Gemini 3.1 Flash Lite" },
  { value: "openai/gpt-6-luna", label: "GPT-6 Luna" },
  { value: "anthropic/claude-haiku-5.5", label: "Claude Haiku 5.5" },
];
const SUBTITLES: Choice[] = [
  { value: "both", label: "中文＋日文" },
  { value: "zh", label: "只有中文" },
  { value: "ja", label: "只有日文" },
  { value: "off", label: "關閉" },
];
const APPEARANCES: Choice[] = [
  { value: "system", label: "跟系統" },
  { value: "light", label: "淺色" },
  { value: "dark", label: "深色" },
];
const SIZES: Choice[] = [
  { value: "120", label: "小" },
  { value: "150", label: "中" },
  { value: "194", label: "跟動畫一樣大" },
];
/** At least a day (the core only checks that it is a number); "keep" is a hundred years. */
const RETENTIONS: Choice[] = [1, 7, 30, 365, KEEP_FOREVER_DAYS].map((days) => ({
  value: String(days),
  label: formatRetention(days),
}));

const AUTO_MICROPHONE = "";

interface Recording {
  recorder: KeyRecorder;
  /** The cell with the keys, which shows the keys while they are held. */
  cell: HTMLElement;
  /** The combination is complete and being saved: further keys are not part of it. */
  finishing: boolean;
}

/** The choices of a menu: those given, and `current` too if it is not one of them. */
function withCurrent(choices: Choice[], current: string, label: (value: string) => string = (v) => v): Choice[] {
  return choices.some((c) => c.value === current) ? choices : [...choices, { value: current, label: label(current) }];
}

export function createSettings(app: AppContext): Page {
  const el = document.createElement("div");
  el.className = "page";
  el.dataset.page = "settings";
  el.hidden = true;

  let brain: BrainSettings | null = null;
  let hasKey = false;
  let openrouter: Status["openrouter"] | null = null;
  let devices: AudioDevices | null = null;
  let altgr = false;
  let version = "";
  /** The key field is shown although there is a key (「更換」). */
  let replacingKey = false;
  let keyBusy = false;
  let keyError: string | null = null;
  /** What was typed in the limit field while it is not a limit, and what is wrong with it. */
  let limitDraft: string | null = null;
  let limitError: string | null = null;
  let recording: Recording | null = null;
  let visible = false;
  let loads = 0;

  // ---------- drawing ----------

  function microphoneChoices(): Choice[] {
    const found = devices?.devices ?? [];
    const choices: Choice[] = [
      { value: AUTO_MICROPHONE, label: "自動偵測" },
      ...found.map((d) => ({ value: d.name, label: d.name })),
    ];
    const saved = app.device().microphone;
    return saved === null ? choices : withCurrent(choices, saved, (name) => `${name}（未連接）`);
  }

  /** The menu of each `sel` (by its `data-k`): what it offers, what is chosen and what choosing does. */
  function menuFor(k: string): { choices: Choice[]; current: string; pick: (value: string) => void } | null {
    const d = app.device();
    switch (k) {
      case "mic":
        return {
          choices: microphoneChoices(),
          current: d.microphone ?? AUTO_MICROPHONE,
          pick: (v) => void changeDevice({ microphone: v === AUTO_MICROPHONE ? null : v }),
        };
      case "language":
        return { choices: LANGUAGES, current: brain?.language ?? "auto", pick: (v) => void changeBrain("language", v) };
      case "model": {
        const current = brain?.cleanup_model ?? MODELS[0].value;
        return { choices: withCurrent(MODELS, current), current, pick: (v) => void changeBrain("cleanup_model", v) };
      }
      case "subtitles":
        return {
          choices: SUBTITLES,
          current: d.subtitles,
          pick: (v) => void changeDevice({ subtitles: v as DeviceSettings["subtitles"] }),
        };
      case "appearance":
        return {
          choices: APPEARANCES,
          current: d.appearance,
          pick: (v) => void changeDevice({ appearance: v as DeviceSettings["appearance"] }),
        };
      case "retention": {
        const current = String(brain?.history_retention_days ?? 30);
        return {
          choices: withCurrent(RETENTIONS, current, (days) => formatRetention(Number(days))),
          current,
          pick: (v) => void changeBrain("history_retention_days", Number(v)),
        };
      }
      default:
        return null;
    }
  }

  function keyRow(): string {
    if (hasKey && !replacingKey) {
      const detail =
        openrouter?.state === "error"
          ? st("err", openrouter.error === "auth" ? "金鑰無效，請更換" : "目前連不上 OpenRouter")
          : "存在 Windows 認證管理員";
      return row(
        "金鑰",
        detail,
        `<span class="val num">••••••••</span>${btn("更換", "sm", ' data-act="key-replace" data-k="key"')}`,
      );
    }
    return row(
      "金鑰",
      "到 openrouter.ai 建立一把，貼在這裡後按 Enter 測試",
      fieldHTML("sk-or-v1-…", "", { w: 200, label: "OpenRouter 金鑰", id: "key-input", type: "password", invalid: keyError !== null }) +
        btn(keyBusy ? "檢查中…" : "貼上", "pri sm", ` data-act="key-paste"${keyBusy ? " disabled" : ""}`) +
        (hasKey ? btn("取消", "sm", ` data-act="key-cancel"${keyBusy ? " disabled" : ""}`) : ""),
    );
  }

  function render(): void {
    const focused = (document.activeElement as HTMLElement | null)?.dataset?.k;
    const d = app.device();
    const usesAltRight = d.dictation_keys.some((binding) => binding.split("+").includes("AltRight"));
    const limit = brain?.monthly_limit_usd ?? 10;
    const limitShown = limitDraft ?? String(limit);

    el.innerHTML =
      `<h1>設定</h1>` +
      (brain === null ? `<p class="lede">${st("err", "拉斐爾的核心沒有回應，有些設定暫時無法使用")}</p>` : "") +
      group("聽寫", [
        row(
          "快捷鍵",
          "聽寫到一半按 Esc 可以取消",
          d.dictation_keys.map((binding) => keys(bindingLabels(binding))).join('<span class="val">或</span>') +
            btn("變更", "quiet", ' data-act="rec"'),
          { attrs: ' data-row="hotkey"' },
        ),
        ...(altgr && usesAltRight ? [note(st("warn", "你的鍵盤配置把右 Alt 當 AltGr 用，建議換一個快捷鍵。"))] : []),
        row("聽寫方式", esc(modeDescription(d.dictation_mode)), seg(DICTATION_MODES, d.dictation_mode, "聽寫方式", ' data-k="mode"')),
        row("麥克風", "", meter() + sel(microphoneChoices(), d.microphone ?? AUTO_MICROPHONE, "麥克風", ' data-k="mic"')),
        row("語言", "中文一律輸出繁體（台灣用語）", sel(LANGUAGES, brain?.language ?? "auto", "語言", ' data-k="language"')),
      ]) +
      group("OpenRouter", [
        keyRow(),
        ...(keyError ? [note(st("err", keyError))] : []),
        row(
          "每月上限",
          "到了就只聽寫、不整理",
          fieldHTML("", limitShown, { w: 96, pre: "US$", label: "每月上限", num: true, id: "limit-input", invalid: limitError !== null }),
        ),
        ...(limitError ? [note(st("err", limitError))] : []),
        row(
          "整理用的模型",
          "",
          sel(withCurrent(MODELS, brain?.cleanup_model ?? MODELS[0].value), brain?.cleanup_model ?? MODELS[0].value, "整理用的模型", ' data-k="model"'),
        ),
      ]) +
      group("拉斐爾", [
        row("圖案大小", "", seg(SIZES, String(d.emblem_size), "圖案大小", ' data-k="size"')),
        row("字幕", "", sel(SUBTITLES, d.subtitles, "字幕", ' data-k="subtitles"')),
      ]) +
      group("一般", [
        row("外觀", "", sel(APPEARANCES, d.appearance, "外觀", ' data-k="appearance"')),
        row("開機時自動啟動", "", tg(d.autostart, "開機時自動啟動", ' data-k="autostart"')),
        row(
          "歷史紀錄保留",
          "",
          sel(
            withCurrent(RETENTIONS, String(brain?.history_retention_days ?? 30), (days) => formatRetention(Number(days))),
            String(brain?.history_retention_days ?? 30),
            "歷史紀錄保留",
            ' data-k="retention"',
          ),
        ),
      ]) +
      `<p class="foot">拉斐爾 ${esc(version)}。粉絲自製，跟《關於我轉生變成史萊姆這檔事》的原作與動畫製作方無關；聲音模型和從動畫取樣的素材只限個人使用。 ` +
      `<a href="#" data-act="license">開源授權</a></p>`;

    if (focused) el.querySelector<HTMLElement>(`[data-k="${focused}"]`)?.focus();
  }

  // ---------- changes ----------

  async function changeBrain(key: keyof BrainSettings, value: string | number): Promise<void> {
    try {
      brain = (await api.settingsSet(key, value)).settings;
    } catch (err) {
      toaster.info(describeError(err));
    }
    render();
  }

  async function changeDevice(patch: Partial<DeviceSettings>): Promise<void> {
    try {
      await app.setDevice(patch);
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
        toaster.info("金鑰有效，已存進 Windows 認證管理員");
        openrouter = await api.statusGet().then((s) => s.openrouter, () => null);
      } else {
        keyError = keyReasonText(result.reason);
      }
    } catch (err) {
      keyError = describeError(err);
    }
    keyBusy = false;
    render();
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
      toaster.info("請在欄位裡按 Ctrl+V 貼上金鑰");
    }
  }

  function commitLimit(input: HTMLInputElement): void {
    const limit = parseLimit(input.value);
    if (limit === null) {
      limitDraft = input.value;
      limitError = "請輸入 0 以上的數字";
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

  // ---------- recording a hotkey ----------

  function onKeyDown(e: KeyboardEvent): void {
    if (!recording || !e.code || e.code === "Unidentified") return;
    e.preventDefault();
    e.stopPropagation();
    if (recording.finishing) return;
    const result = recording.recorder.keyDown(e.code, { altGraph: e.getModifierState("AltGraph") });
    if (result.type === "cancel") void endRecording();
    else if (result.type === "progress") recording.cell.innerHTML = keys(result.codes.map(keyLabel));
  }

  function onKeyUp(e: KeyboardEvent): void {
    if (!recording) return;
    e.preventDefault();
    e.stopPropagation();
    if (recording.finishing) return;
    const result = recording.recorder.keyUp(e.code);
    if (result?.type === "done") void finishRecording(result.codes);
  }

  /** Stops listening and lets the hotkeys work again. */
  async function endRecording(): Promise<void> {
    if (!recording) return;
    recording = null;
    document.removeEventListener("keydown", onKeyDown, true);
    document.removeEventListener("keyup", onKeyUp, true);
    window.removeEventListener("blur", cancelRecording);
    try {
      await api.hotkeysPause(false);
    } catch (err) {
      toaster.info(describeError(err));
    }
    if (visible) render();
  }

  /** The window lost focus: the key-up events would not arrive, so the recording is dropped. */
  function cancelRecording(): void {
    void endRecording();
  }

  async function finishRecording(codes: string[]): Promise<void> {
    if (!recording || recording.finishing) return;
    recording.finishing = true;
    const problem = validateCodes(codes);
    if (problem) {
      toaster.info(problem);
    } else {
      try {
        const binding = bindingFromCodes(codes);
        await app.setDevice({ dictation_keys: [binding] });
        toaster.info(`快捷鍵改成 ${bindingLabels(binding).join(" + ")}`);
      } catch (err) {
        toaster.info(describeError(err));
      }
    }
    await endRecording();
  }

  async function startRecording(): Promise<void> {
    if (recording) return;
    const cell = el.querySelector<HTMLElement>('[data-row="hotkey"] .ctl');
    if (!cell) return;
    try {
      await api.hotkeysPause(true);
    } catch (err) {
      toaster.info(describeError(err));
      return;
    }
    recording = { recorder: new KeyRecorder(), cell, finishing: false };
    cell.innerHTML = `<span class="rec-hint">按下新的按鍵（Esc 取消）</span>`;
    document.addEventListener("keydown", onKeyDown, true);
    document.addEventListener("keyup", onKeyUp, true);
    window.addEventListener("blur", cancelRecording);
  }

  // ---------- the microphone meter ----------

  // After a core restart the app switches the new core's monitoring back on by itself.
  const monitor = new MonitorSwitch(api.audioMonitor);
  /** The window is in use. It is taken to be, until it says otherwise: the page was just opened by a person. */
  let windowFocused = true;

  /** The microphone is listened to while the page is on screen and the window is in use. */
  function syncMonitor(): void {
    monitor.set(visible && !document.hidden && windowFocused);
  }

  function onLevel(value: number): void {
    setMeter(el.querySelector(".meter"), value);
  }

  // ---------- events ----------

  el.addEventListener("click", (e) => {
    const target = e.target as Element;

    const choose = target.closest<HTMLElement>(".sel");
    if (choose) {
      if (menuIsOpenFor(choose)) {
        closeMenu();
        return;
      }
      const menu = menuFor(choose.dataset.k ?? "");
      if (menu) openMenu(choose, menu.choices, menu.current, menu.pick);
      return;
    }

    const size = target.closest<HTMLElement>('.seg[data-k="size"] button');
    if (size) {
      void changeDevice({ emblem_size: Number(size.dataset.v) as DeviceSettings["emblem_size"] });
      return;
    }

    const mode = target.closest<HTMLElement>('.seg[data-k="mode"] button');
    if (mode) {
      if (isDictationMode(mode.dataset.v) && mode.dataset.v !== app.device().dictation_mode) {
        void changeDevice({ dictation_mode: mode.dataset.v });
      }
      return;
    }

    if (target.closest('.tg[data-k="autostart"]')) {
      void changeDevice({ autostart: !app.device().autostart });
      return;
    }

    switch (target.closest<HTMLElement>("[data-act]")?.dataset.act) {
      case "rec":
        void startRecording();
        break;
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
        break;
      case "key-paste":
        void pasteKey();
        break;
      case "license":
        e.preventDefault();
        toaster.info("專案的開源授權還沒有決定。");
        break;
    }
  });

  // `change` comes when a field loses focus or Enter is pressed: no save button needed. The key is
  // the exception: it is sent by Enter or 貼上 only, never by leaving the field (keyfield.ts), so
  // that 取消 after typing a replacement does not store it.
  el.addEventListener("change", (e) => {
    const input = e.target as HTMLInputElement;
    if (input.id === "limit-input") commitLimit(input);
  });
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
    async show() {
      visible = true;
      const mine = ++loads;
      const [brainResult, keyResult, statusResult, devicesResult, altgrResult, versionResult] = await Promise.allSettled([
        api.settingsGet(),
        api.secretsStatus(),
        api.statusGet(),
        api.audioDevices(),
        api.keyboardHasAltgr(),
        getVersion(),
        // Read again: the settings may have changed since the window started. `refreshDevice` puts
        // the answer in `app.device()` itself, or drops it if a newer read or write overtook it.
        app.refreshDevice(),
      ]);
      if (mine !== loads || !visible) return;
      brain = brainResult.status === "fulfilled" ? brainResult.value.settings : null;
      hasKey = keyResult.status === "fulfilled" && keyResult.value.has_key;
      openrouter = statusResult.status === "fulfilled" ? statusResult.value.openrouter : null;
      devices = devicesResult.status === "fulfilled" ? devicesResult.value : null;
      altgr = altgrResult.status === "fulfilled" && altgrResult.value;
      version = versionResult.status === "fulfilled" ? versionResult.value : "0.1.0";
      replacingKey = false;
      keyError = null;
      limitDraft = null;
      limitError = null;
      render();

      stopEvents?.();
      const stopLevels = app.onEvent((event) => event.type === "Level" && onLevel(event.value));
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
    },
    hide() {
      visible = false;
      loads++;
      closeMenu();
      void endRecording();
      stopEvents?.();
      stopEvents = null;
      syncMonitor();
    },
  };
}
