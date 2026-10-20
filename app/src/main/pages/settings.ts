// Settings: a panel over the main window with five tabs: 一般, 聽寫, 拉斐爾, 用量 and 關於 (spec
// §12.3). A change takes effect at once; there is no save button. The settings of the brain (limit,
// models, language, speech-to-text, retention, how Raphael answers) go to the core with
// `settings.set`; the settings of this computer (hotkeys, microphone, pausing media, emblem, theme, autostart) go
// to the Rust side with `device_settings_set`.

import { isUiLocale, onLocaleChange, t } from "../i18n";

import { getVersion } from "@tauri-apps/api/app";
import * as api from "../api";
import { brandIcon, hydrateBrandIcons } from "../brand";
import "../settings-polish.css";
import { DICTATION_MODES, isDictationMode, modeDescription } from "../dictation";
import {
  describeError,
  formatClock,
  formatUsd,
  keyReasonText,
  parseLimit,
} from "../format";
import { wireKeyField } from "../keyfield";
import {
  AUTO_DETECT_HINT,
  type LanguageGroup,
  chooseLanguageVariant,
  languageButtonHTML,
  languageChipsHTML,
  languageLabel,
  languageName,
  normalizeLanguages,
  toggleTranscriptionLanguage,
  transcriptionButtonHTML,
  transcriptionLabel,
  transcriptionPanelHTML,
  variantPanelHTML,
} from "../languages";
import { KeyRecorder, bindingClash, bindingFromCodes, bindingLabels, keyLabel, validateCodes } from "../keys";
import { MonitorSwitch } from "../monitor";
import { automaticMicrophoneLabel } from "../microphone";
import { PAUSE_MEDIA_KEY, pauseMediaRow, pauseMediaToggle } from "../pause-media";
import { cleanupPriceShort, cleanupPriceText, priceOf, pricingNote, sttPriceShort, sttPriceText } from "../pricing";
import { runOutDay } from "../report";
import { WARN_FROM, spendVisual, spentShare } from "../spend";
import type { AppContext, AudioDevices, BrainSettings, DeviceSettings, Page, Pricing, SettingsPane, Status } from "../types";
import {
  type Choice,
  type IconName,
  btn,
  closeMenu,
  esc,
  fieldHTML,
  ico,
  iconBtn,
  keys,
  menuIsOpenFor,
  meter,
  note,
  openMenu,
  openMenuOnArrow,
  openPopover,
  placeMenu,
  row,
  sel,
  setMeter,
  st,
  tg,
  toaster,
} from "../ui";

interface FocusStop { focus(): void }

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

const MODELS: Choice[] = [
  { value: "openai/gpt-6-luna", get label() { return t("settings.001"); } },
  { value: "anthropic/claude-haiku-5.5", get label() { return t("model.haiku"); } },
  { value: "google/gemini-3.1-flash-lite", get label() { return t("settings.002"); } },
];
const STT_ENGINES: Choice[] = [
  { value: "cloud", get label() { return t("settings.003"); }, get desc() { return t("settings.004"); } },
  { value: "local", get label() { return t("settings.005"); }, get desc() { return t("settings.006"); } },
];
/** The cloud models the core accepts (brain/settings.py STT_CLOUD_MODELS), the default first. */
const STT_MODELS: Choice[] = [
  { value: "elevenlabs/scribe-v2", get label() { return t("settings.007"); } },
  { value: "openai/gpt-transcribe", get label() { return t("model.gptTranscribe"); } },
  { value: "microsoft/mai-transcribe-2", get label() { return t("model.mai"); } },
  { value: "assemblyai/universal-3-6-pro", get label() { return t("model.assembly"); } },
  { value: "qwen/qwen3-asr-1.7b", get label() { return t("model.qwen"); } },
  { value: "google/gemini-3.5-transcribe", get label() { return t("model.geminiTranscribe"); } },
  { value: "openai/whisper-large-v3-turbo", get label() { return t("settings.008"); } },
];
const APPEARANCES: Choice[] = [
  { value: "system", get label() { return t("settings.013"); }, get desc() { return t("settings.014"); } },
  { value: "light", get label() { return t("settings.015"); } },
  { value: "dark", get label() { return t("settings.016"); } },
];
const SIZES: Choice[] = [
  { value: "120", get label() { return t("settings.017"); }, get desc() { return t("settings.018"); } },
  { value: "150", get label() { return t("settings.019"); }, get desc() { return t("settings.020"); } },
  { value: "194", get label() { return t("settings.021"); }, get desc() { return t("settings.022"); } },
];
const AUTO_MICROPHONE = "";
const UI_LANGUAGES: Choice[] = [
  { value: "zh-TW", get label() { return t("settings.023"); } },
  { value: "en", get label() { return t("locale.english"); } },
  { value: "ja", get label() { return t("settings.024"); } },
];

/** What the prices in the cleanup model's menu are. */
const CLEANUP_PRICE_HEAD = () => t("settings.025");
const PRICES_LOADING = () => t("settings.026");

/** The tabs on the left of the panel, in order. */
const PANES: { id: SettingsPane; icon: IconName; title: () => string }[] = [
  { id: "general", icon: "gear", title: () => t("settings.paneGeneral") },
  { id: "dictation", icon: "mic", title: () => t("settings.paneDictation") },
  { id: "agent", icon: "sparkle", title: () => t("settings.paneAgent") },
  { id: "usage", icon: "wallet", title: () => t("settings.paneUsage") },
  { id: "about", icon: "info", title: () => t("common.about") },
];

/** The name of a tab, in the language of the window. */
const paneTitle = (id: SettingsPane): string => PANES.find((p) => p.id === id)!.title();

/** The shortcuts that can be recorded here, and the row of each. */
type HotkeyField = "dictation_keys" | "agent_keys";
const HOTKEY_ROWS: Record<HotkeyField, string> = { dictation_keys: "hotkey", agent_keys: "agent-hotkey" };

interface Recording {
  recorder: KeyRecorder;
  /** Which shortcut is being recorded. */
  field: HotkeyField;
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
  el.className = "modal-scrim";
  el.dataset.page = "settings";
  el.hidden = true;
  el.innerHTML =
    `<div class="modal" role="dialog" aria-modal="true" aria-labelledby="set-title">` +
    `<nav class="modal-nav" aria-label="${esc(t("nav.settings"))}">` +
    PANES.map((p) => `<button type="button" class="nav" data-pane="${p.id}" aria-current="false">${ico(p.icon)}<span>${esc(p.title())}</span></button>`).join("") +
    `</nav>` +
    `<div class="modal-body"></div>` +
    `${iconBtn("x", t("common.close"), ' data-act="close"').replace('class="icon-btn"', 'class="icon-btn modal-x"')}` +
    `</div>`;
  const body = el.querySelector<HTMLElement>(".modal-body")!;
  let pane: SettingsPane = "general";

  let brain: BrainSettings | null = null;
  let hasKey = false;
  let openrouter: Status["openrouter"] | null = null;
  let spend: Status["spend"] | null = null;
  /** The prices of the models: being read (`loading`), read, or not to be had (`failed`). */
  let pricing: Pricing | "loading" | "failed" = "loading";
  let checkingOpenrouter = false;
  let devices: AudioDevices | null = null;
  let altgr = false;
  let version = "";
  /** The key field is shown although there is a key (「更換」). */
  let replacingKey = false;
  let keyBusy = false;
  let keyError: (() => string) | null = null;
  /** What was typed in the limit field while it is not a limit, and what is wrong with it. */
  let limitDraft: string | null = null;
  let limitError: (() => string) | null = null;
  /** The same for the weather city. */
  let cityDraft: string | null = null;
  let cityError: (() => string) | null = null;
  let recording: Recording | null = null;
  /** The languages the core offers (`languages.list`); null while unknown or when it cannot say. */
  let languageGroups: LanguageGroup[] | null = null;
  let languagePanelKind: "languages" | "variants" = "languages";
  let languagePanel: HTMLElement | null = null;
  let visible = false;
  let loading = false;
  let loads = 0;

  // ---------- drawing ----------

  function microphoneChoices(): Choice[] {
    const found = devices?.devices ?? [];
    const choices: Choice[] = [
      { value: AUTO_MICROPHONE, get label() { return automaticMicrophoneLabel(devices); } },
      ...found.map((d) => ({ value: d.name, label: d.name })),
    ];
    const saved = app.device().microphone;
    return saved === null ? choices : withCurrent(choices, saved, (name) => t("onboarding.006", { p0: name }));
  }

  /** The menu of each `sel` (by its `data-k`): what it offers, what is chosen and what choosing does. */
  function menuFor(k: string): { choices: Choice[]; current: string; pick: (value: string) => void; head?: string } | null {
    const d = app.device();
    switch (k) {
      case "mic":
        return {
          choices: microphoneChoices(),
          current: d.microphone ?? AUTO_MICROPHONE,
          pick: (v) => void changeDevice({ microphone: v === AUTO_MICROPHONE ? null : v }),
        };
      case "ui-language":
        return {
          choices: UI_LANGUAGES,
          current: d.ui_language,
          pick: (v) => { if (isUiLocale(v)) void changeDevice({ ui_language: v }); },
        };
      case "mode":
        return {
          choices: DICTATION_MODES,
          current: d.dictation_mode,
          pick: (v) => {
            if (isDictationMode(v) && v !== app.device().dictation_mode) void changeDevice({ dictation_mode: v });
          },
        };
      case "appearance":
        return {
          choices: APPEARANCES,
          current: d.appearance,
          pick: (v) => {
            const value = v as DeviceSettings["appearance"];
            if (APPEARANCES.some((c) => c.value === value) && value !== app.device().appearance) void changeDevice({ appearance: value });
          },
        };
      case "size":
        return {
          choices: SIZES,
          current: String(d.emblem_size),
          pick: (v) => {
            if (SIZES.some((c) => c.value === v) && v !== String(app.device().emblem_size)) {
              void changeDevice({ emblem_size: Number(v) as DeviceSettings["emblem_size"] });
            }
          },
        };
      case "stt":
        return { choices: STT_ENGINES, current: sttEngine(), pick: (v) => void changeBrain("stt_engine", v) };
      case "stt-model": {
        const current = sttModel();
        return {
          choices: withPrices(withCurrent(STT_MODELS, current), (id) => sttPriceShort(priceOf(prices()?.stt, id))),
          current,
          pick: (v) => void changeBrain("stt_cloud_model", v),
        };
      }
      case "model": {
        const current = cleanupModel();
        return {
          choices: withPrices(withCurrent(MODELS, current), (id) => cleanupPriceShort(priceOf(prices()?.cleanup, id))),
          current,
          pick: (v) => void changeBrain("cleanup_model", v),
          head: prices() ? CLEANUP_PRICE_HEAD() : undefined,
        };
      }
      case "agent-model": {
        const current = agentModel();
        return {
          choices: withPrices(withCurrent(MODELS, current), (id) => cleanupPriceShort(priceOf(prices()?.cleanup, id))),
          current,
          pick: (v) => void changeBrain("agent_model", v),
          head: prices() ? CLEANUP_PRICE_HEAD() : undefined,
        };
      }
      default:
        return null;
    }
  }

  /** The chosen languages, never none. */
  const languages = (): string[] => normalizeLanguages(brain?.languages);

  const sttEngine = (): string => brain?.stt_engine ?? "cloud";
  const sttModel = (): string => brain?.stt_cloud_model ?? STT_MODELS[0].value;
  const cleanupModel = (): string => brain?.cleanup_model ?? MODELS[0].value;
  const agentModel = (): string => brain?.agent_model ?? MODELS[0].value;
  const agentCity = (): string => brain?.agent_city ?? "";
  /** The prices once they are read. */
  const prices = (): Pricing | null => (typeof pricing === "object" ? pricing : null);

  /** The choices with their prices as details, while there are prices to show. */
  function withPrices(choices: Choice[], price: (id: string) => string): Choice[] {
    if (pricing === "failed") return choices;
    return choices.map((c) => ({ ...c, detail: pricing === "loading" ? PRICES_LOADING() : price(c.value) }));
  }

  /** The texts that depend on the prices, by the `data-price` of the element that shows them. */
  function priceTexts(): Record<string, string> {
    const p = prices();
    if (pricing === "loading") return { model: PRICES_LOADING(), "agent-model": PRICES_LOADING(), "stt-model": PRICES_LOADING(), note: "" };
    if (!p) return { model: "", "agent-model": "", "stt-model": "", note: t("settings.030") };
    return {
      model: cleanupPriceText(priceOf(p.cleanup, cleanupModel())),
      "agent-model": cleanupPriceText(priceOf(p.cleanup, agentModel())),
      "stt-model": sttPriceText(priceOf(p.stt, sttModel())),
      note: pricingNote(p, Date.now()),
    };
  }

  /** Shows the prices where they go, without drawing the page again (a key being typed stays). */
  function paintPrices(): void {
    const texts = priceTexts();
    el.querySelectorAll<HTMLElement>("[data-price]").forEach((node) => {
      node.textContent = texts[node.dataset.price ?? ""] ?? "";
    });
  }

  const priceSpan = (k: string): string => `<span data-price="${k}">${esc(priceTexts()[k] ?? "")}</span>`;

  /** 「語音轉文字」, the cloud model when it is the cloud, and the cleanup model. */
  function modelRows(): string[] {
    const rows = [
      row(
        t("settings.031"),
        sttEngine() === "cloud" ? t("settings.032") : t("settings.033"),
        sel(STT_ENGINES, sttEngine(), t("settings.031"), ` data-k="stt"${brain ? "" : " disabled"}`),
      ),
    ];
    if (sttEngine() === "cloud") {
      rows.push(
        row(
          t("settings.034"),
          priceSpan("stt-model"),
          sel(withCurrent(STT_MODELS, sttModel()), sttModel(), t("settings.034"), ` data-k="stt-model"${brain ? "" : " disabled"}`),
        ),
      );
    }
    rows.push(
      row(
        t("settings.035"),
        priceSpan("model"),
        sel(withCurrent(MODELS, cleanupModel()), cleanupModel(), t("settings.035"), ` data-k="model"${brain ? "" : " disabled"}`),
      ),
    );
    return rows;
  }

  function keyRow(): string {
    if (hasKey && !replacingKey) {
      const detail =
        openrouter?.state === "error"
          ? st("err", openrouter.error === "auth" ? t("settings.036") : t("settings.037"))
          : t("settings.038");
      return row(
        t("onboarding.029"),
        detail,
        `<span class="val num">••••••••</span>${btn(t("onboarding.027"), "sm", ' data-act="key-replace" data-k="key"')}`,
      );
    }
    return row(
      t("onboarding.029"),
      t("settings.039"),
      fieldHTML("sk-or-v1-…", "", { w: 200, get label() { return t("onboarding.029"); }, id: "key-input", type: "password", invalid: keyError !== null }) +
        btn(keyBusy ? t("onboarding.011") : t("onboarding.030"), "pri sm", ` data-act="key-paste"${keyBusy ? " disabled" : ""}`) +
        (hasKey ? btn(t("onboarding.031"), "sm", ` data-act="key-cancel"${keyBusy ? " disabled" : ""}`) : ""),
    );
  }

  /** How the latest contact with OpenRouter went, with a way to ask again. Not shown without a key. */
  function connectionRow(): string[] {
    if (!hasKey || openrouter === null || openrouter.state === "no_key") return [];
    let status: string;
    let detail = "";
    switch (openrouter.state) {
      case "ok":
        status = st("ok", t("settings.040"));
        if (openrouter.latency_ms !== null) detail = t("settings.041", { p0: openrouter.latency_ms });
        break;
      case "error":
        status = st("err", openrouter.error === "auth" ? t("format.012") : t("settings.042"));
        if (openrouter.checked_at_ms !== null) detail = t("settings.043", { p0: formatClock(openrouter.checked_at_ms) });
        break;
      default:
        status = st("idle", t("settings.044"));
    }
    return [
      row(
        t("settings.045"),
        esc(detail),
        status + btn(checkingOpenrouter ? t("onboarding.011") : t("settings.046"), "quiet", ` data-act="or-check"${checkingOpenrouter ? " disabled" : ""}`),
      ),
    ];
  }

  /** This month's spend against the limit, and when it will run out at this pace. */
  function spendRow(): string[] {
    const attrs = ' data-row="raphael-spend"';
    if (!spend) return [row(t("settings.050"), "", esc(t("settings.usageUnavailable")), { attrs })];
    const { month_usd: month, limit_usd: limit } = spend;
    const used = spentShare(month, limit);
    let forecast = "";
    if (month >= limit) forecast = t("settings.047");
    else if (used >= WARN_FROM) {
      const { m, d } = runOutDay(month, limit, Date.now());
      forecast = t("settings.048", { p0: m, p1: d });
    } else if (spend.forecast_usd > 0) forecast = t("settings.049", { p0: formatUsd(spend.forecast_usd) });
    return [row(t("settings.050"), month >= limit ? st("warn", forecast) : esc(forecast), spendVisual(month, limit), { attrs })];
  }

  /** Key-wide usage is separate from Raphael's local spending and its editable limit. */
  function usageRow(): string {
    const usage = hasKey ? openrouter?.usage : null;
    const monthly = usage?.monthly;
    const available = monthly != null && Number.isFinite(monthly);
    const state = !hasKey ? "no-key" : available ? (openrouter?.state === "error" ? "stale" : "ready") : checkingOpenrouter ? "loading" : "unavailable";
    const details: string[] = [];
    if (usage?.limit != null && Number.isFinite(usage.limit)) {
      details.push(`<span data-usage="limit">${esc(t("settings.usageLimit", { p0: formatUsd(usage.limit) }))}</span>`);
    }
    if (usage?.limit_remaining != null && Number.isFinite(usage.limit_remaining)) {
      details.push(`<span data-usage="remaining">${esc(t("settings.usageRemaining", { p0: formatUsd(usage.limit_remaining) }))}</span>`);
    }
    if (state === "stale") details.push(esc(t("settings.usageCached")));
    const value = available ? formatUsd(monthly) : !hasKey ? t("settings.usageNoKey") : checkingOpenrouter ? t("common.loading") :
      openrouter?.state === "unknown" ? t("settings.usageUnchecked") : t("settings.usageUnavailable");
    return row(
      t("settings.usageMonthly"),
      details.join(" · "),
      `<span class="${available ? "val num" : "val"}" data-usage="monthly">${esc(value)}</span>`,
      { attrs: ` data-row="openrouter-usage" data-usage-state="${state}"` },
    );
  }

  /** A section of the settings: an icon and a title, then its rows. */
  const section = (icon: IconName, title: string, rows: string[], foot = ""): string =>
    `<section class="sgroup"><h2>${ico(icon)}${esc(title)}</h2><div class="slist">${rows.join("")}</div>` +
    (foot ? `<p class="foot">${foot}</p>` : "") +
    `</section>`;

  /** The button whose menu or panel is open, by what the page calls it. */
  function openAnchorKey(): string | undefined {
    const anchor = Array.from(el.querySelectorAll<HTMLElement>(".sel, [data-act='variants']")).find(menuIsOpenFor);
    return anchor ? (anchor.dataset.k ?? "variants") : undefined;
  }

  /** The first line of every tab: its name, which has the focus when the panel opens. */
  const heading = (): string => `<h1 id="set-title" tabindex="-1">${esc(paneTitle(pane))}</h1>`;
  /** What the tabs that need the core say when it does not answer. */
  const coreNote = (): string => (brain === null ? `<p class="lede">${st("err", t("settings.052"))}</p>` : "");

  /** 一般: the language of the window, and how Raphael looks and starts on this computer. */
  function generalHTML(): string {
    const d = app.device();
    return (
      heading() +
      section("globe", t("settings.058"), [
        row(t("settings.059"), t("settings.060"), sel(UI_LANGUAGES, d.ui_language, t("settings.059"), ' data-k="ui-language"'), { cls: "row-ui-language" }),
      ]) +
      section("laptop", t("settings.sectionAppearance"), [
        row(t("settings.067"), t("settings.068"), sel(APPEARANCES, d.appearance, t("settings.067"), ' data-k="appearance"')),
        row(t("settings.069"), t("settings.070"), sel(SIZES, String(d.emblem_size), t("settings.069"), ' data-k="size"')),
        row(t("settings.071"), t("settings.072"), tg(d.subtitles !== "off", t("settings.071"), ' data-k="subtitles"')),
        row(t("settings.073"), t("settings.074"), tg(d.autostart, t("settings.073"), ' data-k="autostart"')),
      ])
    );
  }

  /** 聽寫: the keys, the languages, the microphone and the models that turn speech into text. */
  function dictationHTML(): string {
    const d = app.device();
    const usesAltRight = d.dictation_keys.some((binding) => binding.split("+").includes("AltRight"));
    return (
      heading() +
      coreNote() +
      section("keyboard", t("history.012"), [
        row(
          t("home.020"),
          t("settings.053"),
          d.dictation_keys.map((binding) => keys(bindingLabels(binding))).join(t("onboarding.009")) +
            btn(t("home.018"), "sm", ' data-act="rec"'),
          { attrs: ` data-row="${HOTKEY_ROWS.dictation_keys}"` },
        ),
        ...(altgr && usesAltRight ? [note(st("warn", t("settings.054")))] : []),
        row(t("settings.055"), esc(modeDescription(d.dictation_mode)), sel(DICTATION_MODES, d.dictation_mode, t("settings.055"), ' data-k="mode"')),
        ...(d.paste_last_keys.length
          ? [row(t("home.021"), t("settings.056"), d.paste_last_keys.map((b) => keys(bindingLabels(b))).join(t("onboarding.009")))]
          : []),
        row(t("onboarding.031"), t("settings.057"), keys(["Esc"])),
      ]) +
      section("globe", t("settings.058"), [
        row(t("transcription.title"), esc(AUTO_DETECT_HINT()), transcriptionButtonHTML(languageGroups ?? [], languages(), ` data-k="languages" data-act="transcription-languages"${brain ? "" : " disabled"}`), { cls: "row-lang row-transcription" }),
        row(t("settings.061"), esc(t("variants.description")), languageButtonHTML(languageGroups ?? [], languages(), ` data-k="variants" data-act="variants"${brain ? "" : " disabled"}`), { cls: "row-lang row-variants" }),
      ]) +
      section("mic", t("settings.062"), [
        row(t("onboarding.021"), t("settings.063"), meter() + sel(microphoneChoices(), d.microphone ?? AUTO_MICROPHONE, t("onboarding.021"), ' data-k="mic"') + iconBtn("refresh", t("action.refreshMicrophones"), ' data-act="mic-refresh"')),
        pauseMediaRow(d.pause_media),
      ]) +
      section("wave", t("settings.064"), modelRows(), priceSpan("note")) +
      section("book", t("settings.sectionDictionary"), [
        row(
          t("settings.075"),
          t("settings.076"),
          tg(brain?.learn_corrections ?? true, t("settings.075"), ` data-k="learn"${brain ? "" : " disabled"}`),
        ),
      ])
    );
  }

  /** 拉斐爾: the key that asks her, the model that answers and the weather city. Jev always works out what a request is (core agent/routing.py): there is no choice of router. */
  function agentHTML(): string {
    const d = app.device();
    // an app from before the question key has none yet
    const agentKeys = d.agent_keys ?? [];
    const cityShown = cityDraft ?? agentCity();
    return (
      heading() +
      coreNote() +
      section("sparkle", t("settings.sectionAsk"), [
        row(
          t("settings.agentKeys"),
          t("settings.agentKeysDesc"),
          (agentKeys.length > 0 ? agentKeys.map((binding) => keys(bindingLabels(binding))).join(t("onboarding.009")) : `<span class="val">${esc(t("home.noKeys"))}</span>`) +
            btn(t("home.018"), "sm", ' data-act="rec-agent"'),
          { attrs: ` data-row="${HOTKEY_ROWS.agent_keys}"` },
        ),
        row(
          t("settings.agentModel"),
          priceSpan("agent-model"),
          sel(withCurrent(MODELS, agentModel()), agentModel(), t("settings.agentModel"), ` data-k="agent-model"${brain ? "" : " disabled"}`),
        ),
        row(
          t("settings.agentCity"),
          t("settings.agentCityDesc"),
          fieldHTML(t("settings.agentCityHint"), cityShown, { w: 170, get label() { return t("settings.agentCity"); }, id: "agent-city", invalid: cityError !== null, disabled: brain === null }),
        ),
        ...(cityError ? [note(st("err", cityError()), ' role="alert"')] : []),
      ])
    );
  }

  /** 用量: the key, the connection, and what this month has cost against the limit. */
  function usageHTML(): string {
    const limit = brain?.monthly_limit_usd ?? 10;
    const limitShown = limitDraft ?? String(limit);
    return (
      heading() +
      coreNote() +
      section("wallet", t("settings.065"), [
        keyRow(),
        ...(keyError ? [note(st("err", keyError()), ' role="alert"')] : []),
        ...connectionRow(),
        ...spendRow(),
        usageRow(),
        row(
          t("onboarding.032"),
          t("onboarding.033"),
          fieldHTML("", limitShown, { w: 96, pre: "US$", get label() { return t("onboarding.032"); }, num: true, id: "limit-input", invalid: limitError !== null, disabled: brain === null }),
        ),
        ...(limitError ? [note(st("err", limitError()), ' role="alert"')] : []),
      ], esc(t("settings.usageDifference")))
    );
  }

  /** The tab that is open, as HTML. */
  function paneHTML(): string {
    switch (pane) {
      case "general":
        return generalHTML();
      case "dictation":
        return dictationHTML();
      case "agent":
        return agentHTML();
      case "usage":
        return usageHTML();
      case "about":
        return aboutHTML();
    }
  }

  function aboutHTML(): string {
    return (
      heading() +
      `<div class="about-brand">${brandIcon("about-icon")}<span class="about-name">Raphael</span></div>` +
      `<div class="slist about">` +
      row(t("settings.078"), esc(version || "0.1.0"), "") +
      row(t("settings.079"), t("settings.080"), t("settings.081")) +
      `</div>` +
      t("settings.082")
    );
  }

  function loadingHTML(): string {
    return heading() +
      `<p class="settings-loading" role="status">${esc(t("settings.loading"))}</p>` +
      `<div class="settings-skeleton" aria-hidden="true">${Array.from({ length: 3 }, () =>
        `<div class="skeleton-section"><i class="skeleton-heading"></i><div class="skeleton-row"><i></i><i></i></div><div class="skeleton-row"><i></i><i></i></div></div>`).join("")}</div>`;
  }

  function render(): void {
    const active = document.activeElement as HTMLElement | null;
    const focused = active?.dataset?.k;
    const focusedId = active?.id;
    const keyDraft = el.querySelector<HTMLInputElement>("#key-input")?.value;
    const activeLimit = focusedId === "limit-input" ? el.querySelector<HTMLInputElement>("#limit-input")?.value : undefined;
    const activeCity = focusedId === "agent-city" ? el.querySelector<HTMLInputElement>("#agent-city")?.value : undefined;
    // A menu that is open stays open when the page is drawn again (a status that came in, say).
    const openK = openAnchorKey();
    const scroll = body.scrollTop;
    body.setAttribute("aria-busy", String(loading && pane !== "about"));
    body.innerHTML = pane !== "about" && loading ? loadingHTML() : paneHTML();
    hydrateBrandIcons(body);
    const keyInput = el.querySelector<HTMLInputElement>("#key-input");
    if (keyInput && keyDraft !== undefined) keyInput.value = keyDraft;
    const limitInput = el.querySelector<HTMLInputElement>("#limit-input");
    if (limitInput && activeLimit !== undefined) limitInput.value = activeLimit;
    const cityInput = el.querySelector<HTMLInputElement>("#agent-city");
    if (cityInput && activeCity !== undefined) cityInput.value = activeCity;
    body.scrollTop = scroll;
    el.querySelectorAll<HTMLElement>("[data-pane]").forEach((b) => b.setAttribute("aria-current", b.dataset.pane === pane ? "page" : "false"));

    if (focused) el.querySelector<HTMLElement>(`[data-k="${focused}"]`)?.focus();
    else if (focusedId) el.querySelector<HTMLElement>(`#${CSS.escape(focusedId)}`)?.focus();
    if (openK) {
      const anchor = el.querySelector<HTMLElement>(openK === "variants" ? "[data-act='variants']" : `.sel[data-k="${openK}"]`);
      const menu = menuFor(openK);
      if (anchor && (openK === "languages" || openK === "variants")) openLanguages(anchor);
      else if (anchor && menu) openMenu(anchor, menu.choices, menu.current, menu.pick, menu.head);
    }
  }

  onLocaleChange(() => {
    el.querySelector(".modal-nav")!.setAttribute("aria-label", t("nav.settings"));
    for (const p of PANES) el.querySelector(`[data-pane="${p.id}"] span`)!.textContent = p.title();
    const close = el.querySelector('[data-act="close"]')!;
    close.setAttribute("aria-label", t("common.close"));
    close.setAttribute("title", t("common.close"));
    if (visible) render();
  });

  // ---------- the languages ----------

  function languagePickerHTML(): string {
    return languagePanelKind === "variants"
      ? variantPanelHTML(languageGroups, languages())
      : transcriptionPanelHTML(languageGroups, languages());
  }

  /** Each control opens its own picker; variants only edit already selected languages. */
  function openLanguages(anchor: HTMLElement): void {
    languagePanelKind = anchor.dataset.k === "variants" ? "variants" : "languages";
    const panel = openPopover(anchor, languagePickerHTML(), {
      className: `menu lang-panel ${languagePanelKind === "variants" ? "variants-picker" : "transcription-picker"}`,
      role: "dialog",
      label: languagePanelKind === "variants" ? t("settings.061") : t("transcription.title"),
      onKey: languageKey,
    });
    languagePanel = panel;
    panel.addEventListener("click", (e) => {
      const item = (e.target as Element).closest<HTMLElement>(".lang-item");
      if (!item) return;
      if (item.dataset.language) void pickLanguage(item.dataset.language, false);
      else if (item.dataset.variant) void pickLanguage(item.dataset.variant, true);
    });
    (panel.querySelector<HTMLElement>('[aria-checked="true"], .lang-item') ?? panel).focus();
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

  /** Shows the chosen languages on the button, and in the panel if it is open (keeping the focus). */
  function paintLanguages(): void {
    const button = el.querySelector<HTMLElement>('[data-k="languages"]');
    const variants = el.querySelector<HTMLElement>('[data-k="variants"]');
    if (!button || !variants) return;
    const groups = languageGroups ?? [];
    button.innerHTML = languageChipsHTML(groups, languages());
    button.setAttribute("aria-label", transcriptionLabel(groups, languages()));
    variants.setAttribute("aria-label", languageLabel(groups, languages()));
    variants.title = languages().map((id) => languageName(groups, id)).join(", ");
    const anchor = [button, variants].find(menuIsOpenFor);
    if (!languagePanel || !anchor) return;
    const active = document.activeElement as HTMLElement | null;
    let again: string | null = null;
    if (active && languagePanel.contains(active)) {
      if (active.dataset.variant) again = `[data-variant="${CSS.escape(active.dataset.variant)}"]`;
      else if (active.dataset.language) again = `[data-language="${CSS.escape(active.dataset.language)}"]`;
    }
    languagePanel.innerHTML = languagePickerHTML();
    placeMenu();
    (again ? languagePanel.querySelector<HTMLElement>(again) : null)?.focus();
  }

  /** Chooses or drops a language; the button shows it at once, and goes back if the core refuses. */
  async function pickLanguage(id: string, variant: boolean): Promise<void> {
    const before = languages();
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

  /** Reads the list of languages (again, when the panel is opened without one). */
  async function loadLanguages(): Promise<void> {
    try {
      languageGroups = (await api.languagesList()).groups;
    } catch {
      return; // the panel says the list cannot be had
    }
    if (visible) paintLanguages();
  }

  // ---------- changes ----------

  async function changeBrain(key: keyof BrainSettings, value: string | number | boolean): Promise<void> {
    try {
      brain = (await api.settingsSet(key, value)).settings;
      if (spend) spend = { ...spend, limit_usd: brain.monthly_limit_usd };
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

  /** Reads the prices. The page is shown at once with 「價格載入中…」; the prices go in when they come. */
  async function loadPrices(): Promise<void> {
    const mine = loads;
    let next: Pricing | "failed";
    try {
      next = await api.pricingGet();
    } catch {
      // keep prices read earlier; else say they cannot be had
      next = prices() ?? "failed";
    }
    if (mine !== loads || !visible) return;
    pricing = next;
    paintPrices();
    // a price menu that is open shows the prices too
    for (const k of ["model", "agent-model", "stt-model"]) {
      const anchor = el.querySelector<HTMLElement>(`.sel[data-k="${k}"]`);
      const menu = menuFor(k);
      if (anchor && menu && menuIsOpenFor(anchor)) openMenu(anchor, menu.choices, menu.current, menu.pick, menu.head);
    }
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
        toaster.info(t("onboarding.046"));
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
      toaster.info(t("onboarding.047"));
    }
  }

  function commitLimit(input: HTMLInputElement): void {
    const limit = parseLimit(input.value);
    if (limit === null) {
      limitDraft = input.value;
      limitError = () => t("onboarding.049");
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
    if (city === agentCity()) {
      render();
      return;
    }
    void changeBrain("agent_city", city);
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
    const field = recording.field;
    if (problem) {
      toaster.info(problem);
    } else {
      try {
        const binding = bindingFromCodes(codes);
        const taken = shortcutTakenBy(field, binding);
        if (taken) {
          toaster.info(t("settings.keyTaken", { p0: taken }));
        } else {
          await app.setDevice({ [field]: [binding] });
          toaster.info(t("settings.083", { p0: () => bindingLabels(binding).join(" + ") }));
        }
      } catch (err) {
        toaster.info(describeError(err));
      }
    }
    await endRecording();
  }

  /** The name of the other shortcut that already has `binding`, if one has: no two actions share their keys. */
  function shortcutTakenBy(field: HotkeyField, binding: string): string | null {
    const d = app.device();
    const others: Array<readonly [string, readonly string[]]> = [];
    if (field !== "dictation_keys") others.push([t("home.020"), d.dictation_keys]);
    if (field !== "agent_keys") others.push([t("settings.agentKeys"), d.agent_keys ?? []]);
    others.push([t("home.021"), d.paste_last_keys]);
    return bindingClash(binding, others);
  }

  async function startRecording(field: HotkeyField): Promise<void> {
    if (recording) return;
    const cell = el.querySelector<HTMLElement>(`[data-row="${HOTKEY_ROWS[field]}"] .ctl`);
    if (!cell) return;
    try {
      await api.hotkeysPause(true);
    } catch (err) {
      toaster.info(describeError(err));
      return;
    }
    recording = { recorder: new KeyRecorder(), field, cell, finishing: false };
    cell.innerHTML = t("settings.084");
    document.addEventListener("keydown", onKeyDown, true);
    document.addEventListener("keyup", onKeyUp, true);
    window.addEventListener("blur", cancelRecording);
  }

  // ---------- the microphone meter ----------

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
        const anchor = el.querySelector<HTMLElement>('[data-k="mic"]');
        if (anchor) void refreshMicrophones(anchor, false);
      }, 750);
    } else if (!want) {
      clearInterval(microphoneTimer);
      microphoneTimer = undefined;
      microphoneReads++;
    }
  }

  function onLevel(value: number): void {
    setMeter(el.querySelector(".meter"), value);
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
    const before = JSON.stringify(microphoneChoices());
    devices = fresh;
    if (JSON.stringify(microphoneChoices()) === before) return;
    const current = app.device().microphone ?? AUTO_MICROPHONE;
    const label = anchor.querySelector("span");
    const shown = microphoneChoices().find((c) => c.value === current)?.label ?? current;
    if (label) label.textContent = shown;
    anchor.title = shown;
    const menu = menuFor("mic");
    if (menu && menuIsOpenFor(anchor)) openMenu(anchor, menu.choices, menu.current, menu.pick);
  }

  // ---------- events ----------

  el.addEventListener("click", (e) => {
    const target = e.target as Element;
    if (target === el) {
      app.closeSettings(); // a click on the dimmed window around the panel
      return;
    }
    const paneButton = target.closest<HTMLElement>("[data-pane]");
    if (paneButton) {
      closeMenu();
      pane = paneButton.dataset.pane as SettingsPane;
      syncMonitor();
      body.scrollTop = 0;
      render();
      return;
    }
    const variants = target.closest<HTMLElement>("[data-act='variants']");
    if (variants) {
      if (menuIsOpenFor(variants)) closeMenu();
      else openLanguages(variants);
      return;
    }

    const choose = target.closest<HTMLElement>(".sel");
    if (choose) {
      if (menuIsOpenFor(choose)) {
        closeMenu();
        return;
      }
      if (choose.dataset.k === "languages") {
        openLanguages(choose);
        return;
      }
      const menu = menuFor(choose.dataset.k ?? "");
      if (menu) openMenu(choose, menu.choices, menu.current, menu.pick, menu.head);
      if (choose.dataset.k === "mic") void refreshMicrophones(choose);
      return;
    }

    if (target.closest('.tg[data-k="autostart"]')) {
      void changeDevice({ autostart: !app.device().autostart });
      return;
    }

    if (target.closest(`.tg[data-k="${PAUSE_MEDIA_KEY}"]`)) {
      void changeDevice(pauseMediaToggle(app.device()));
      return;
    }

    if (target.closest('.tg[data-k="subtitles"]')) {
      void changeDevice({ subtitles: app.device().subtitles === "off" ? "on" : "off" });
      return;
    }

    if (target.closest('.tg[data-k="learn"]')) {
      if (brain) void changeBrain("learn_corrections", !(brain.learn_corrections ?? true));
      return;
    }

    switch (target.closest<HTMLElement>("[data-act]")?.dataset.act) {
      case "close":
        app.closeSettings();
        break;
      case "rec":
        void startRecording("dictation_keys");
        break;
      case "rec-agent":
        void startRecording("agent_keys");
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
      case "or-check":
        void checkOpenrouter();
        break;
      case "mic-refresh": {
        const anchor = el.querySelector<HTMLElement>('[data-k="mic"]');
        if (anchor) void refreshMicrophones(anchor);
        break;
      }
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
    const input = e.target as HTMLInputElement;
    if (input.id === "limit-input") commitLimit(input);
    else if (input.id === "agent-city") commitCity(input);
  });
  el.addEventListener("input", (e) => {
    const input = e.target as HTMLInputElement;
    if (input.id === "limit-input") limitDraft = input.value;
    else if (input.id === "agent-city") cityDraft = input.value;
  });
  // Menus consume their own Escape; the panel and native window controls share one Tab loop.
  const settingsKey = (e: KeyboardEvent): void => {
    const chrome = document.getElementById("window-chrome");
    if (!visible || !(e.target instanceof Node) || (!el.contains(e.target) && !chrome?.contains(e.target))) return;
    if (openMenuOnArrow(e)) return;
    if (e.key === "Escape" && !recording && !e.defaultPrevented) {
      e.preventDefault();
      app.closeSettings();
    } else if (e.key === "Tab") {
      const selector = "button:not([disabled]), input:not([disabled]), a[href], [tabindex='0']";
      const items = [
        ...Array.from(el.querySelectorAll<HTMLElement>(selector)),
        ...Array.from(chrome?.querySelectorAll<HTMLElement>(selector) ?? []),
      ].filter((item) => item.offsetParent !== null);
      trapSettingsFocus(e, items, document.activeElement as HTMLElement | null, el.querySelector<HTMLElement>("#set-title"));
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
    async show(open?: SettingsPane) {
      visible = true;
      document.addEventListener("keydown", settingsKey);
      const mine = ++loads;
      loading = true;
      // the first tab, unless the way in knows better (the report that says there is no key goes to the key)
      pane = PANES.some((p) => p.id === open) ? open! : "general";
      body.scrollTop = 0;
      closeMenu();
      render();
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
      version = versionResult.status === "fulfilled" ? versionResult.value : "0.1.0";
      if (languagesResult.status === "fulfilled") languageGroups = languagesResult.value.groups;
      replacingKey = false;
      keyError = null;
      limitDraft = null;
      limitError = null;
      cityDraft = null;
      cityError = null;
      loading = false;
      render();
      if (!el.contains(document.activeElement)) el.querySelector<HTMLElement>("#set-title")?.focus();
      void loadPrices();

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
      document.removeEventListener("keydown", settingsKey);
      loading = false;
      loads++;
      closeMenu();
      void endRecording();
      stopEvents?.();
      stopEvents = null;
      syncMonitor();
    },
  };
}
