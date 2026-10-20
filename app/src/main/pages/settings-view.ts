// What the settings panel draws (settings.ts holds the state and the events): the tabs, and each
// tab as HTML made from a snapshot of what the panel knows. Nothing here reads the page or calls
// the app, so the structure of every tab and the owner's rules for it can be tested without a
// browser.
//
// The owner's rules for every row: a choice among options is a dropdown (`sel`, never the options
// in a row), an action is a button with a shape (`btn`, never a word that can be clicked), and a
// line under a row's name only where the setting would otherwise be set wrong. Each tab opens with
// its name and one line on what it is for, then its rows in a few named groups.

import { t } from "../i18n";

import { brandIcon } from "../brand";
import { DICTATION_MODES } from "../dictation";
import { formatClock, formatUsd } from "../format";
import { type LanguageGroup, languageSelectHTML, normalizeLanguages } from "../languages";
import { bindingLabels } from "../keys";
import { automaticMicrophoneLabel, automaticMicrophoneShort, shortMicrophoneName } from "../microphone";
import { cleanupPriceShort, cleanupPriceText, priceOf, pricingNote, sttPriceShort, sttPriceText } from "../pricing";
import { runOutDay } from "../report";
import { WARN_FROM, spendVisual, spentShare } from "../spend";
import type { AudioDevices, BrainSettings, DeviceSettings, Pricing, SettingsPane, Status } from "../types";
import { type Choice, type IconName, btn, esc, fieldHTML, keys, levelBar, note, row, sel, st, tg } from "../ui";

export const MODELS: Choice[] = [
  { value: "openai/gpt-6-luna", get label() { return t("settings.001"); } },
  { value: "anthropic/claude-haiku-5.5", get label() { return t("model.haiku"); } },
  { value: "google/gemini-3.1-flash-lite", get label() { return t("settings.002"); } },
];
/** The cloud models the core accepts (brain/settings.py STT_CLOUD_MODELS), the default first. */
export const STT_MODELS: Choice[] = [
  { value: "elevenlabs/scribe-v2", get label() { return t("settings.007"); } },
  { value: "openai/gpt-transcribe", get label() { return t("model.gptTranscribe"); } },
  { value: "microsoft/mai-transcribe-2", get label() { return t("model.mai"); } },
  { value: "assemblyai/universal-3-6-pro", get label() { return t("model.assembly"); } },
  { value: "qwen/qwen3-asr-1.7b", get label() { return t("model.qwen"); } },
  { value: "google/gemini-3.5-transcribe", get label() { return t("model.geminiTranscribe"); } },
  { value: "openai/whisper-large-v3-turbo", get label() { return t("settings.008"); } },
];
/**
 * The last choice of the speech-to-text menu: the engine on this computer instead of a cloud model
 * (core setting `stt_engine` "local"). One menu holds both, so that what turns speech into text is
 * one choice with one price.
 */
export const LOCAL_STT = "local";
const LOCAL_STT_CHOICE: Choice = { value: LOCAL_STT, get label() { return t("settings.sttLocal"); } };
/** In the owner's order: light, dark, or as Windows is. */
export const APPEARANCES: Choice[] = [
  { value: "light", get label() { return t("settings.015"); } },
  { value: "dark", get label() { return t("settings.016"); } },
  { value: "system", get label() { return t("settings.013"); } },
];
export const SIZES: Choice[] = [
  { value: "120", get label() { return t("settings.017"); }, get desc() { return t("settings.018"); } },
  { value: "150", get label() { return t("settings.019"); }, get desc() { return t("settings.020"); } },
  { value: "194", get label() { return t("settings.021"); }, get desc() { return t("settings.022"); } },
];
export const AUTO_MICROPHONE = "";
export const UI_LANGUAGES: Choice[] = [
  { value: "zh-TW", get label() { return t("settings.023"); } },
  { value: "en", get label() { return t("locale.english"); } },
  { value: "ja", get label() { return t("settings.024"); } },
];

/** What the prices in the cleanup model's menu are. */
export const CLEANUP_PRICE_HEAD = () => t("settings.025");
const PRICES_LOADING = () => t("settings.026");

/**
 * The tabs on the left of the panel, in order, each with the line under its name that says what it
 * is for. A tab to come (the integrations) is one more entry here and one more case in `paneHTML`.
 */
export const PANES: { id: SettingsPane; icon: IconName; title: () => string; purpose: (d: DeviceSettings) => string }[] = [
  { id: "general", icon: "gear", title: () => t("settings.paneGeneral"), purpose: () => t("settings.purposeGeneral") },
  { id: "dictation", icon: "mic", title: () => t("settings.paneDictation"), purpose: () => t("settings.purposeDictation") },
  { id: "agent", icon: "sparkle", title: () => t("settings.paneAgent"), purpose: agentPurpose },
  { id: "usage", icon: "wallet", title: () => t("settings.paneUsage"), purpose: () => t("settings.purposeUsage") },
  { id: "about", icon: "info", title: () => t("common.about"), purpose: () => t("settings.purposeAbout") },
];

/** 「按 右 Alt + Space 問拉斐爾」: the line names the keys that are set, so that it stays true when they change. */
function agentPurpose(d: DeviceSettings): string {
  // an app from before the question key has none yet
  const binding = d.agent_keys?.[0];
  return binding ? t("settings.purposeAgent", { p0: bindingLabels(binding).join(" + ") }) : t("settings.purposeAgentNoKeys");
}

/** The name of a tab, in the language of the window. */
export const paneTitle = (id: SettingsPane): string => PANES.find((p) => p.id === id)!.title();

/** The group of rows that OpenRouter's name heads: a brand, the same in every language. */
const OPENROUTER = "OpenRouter";

/** The shortcuts that can be recorded here, and the row of each. */
export type HotkeyField = "dictation_keys" | "agent_keys";
export const HOTKEY_ROWS: Record<HotkeyField, string> = { dictation_keys: "hotkey", agent_keys: "agent-hotkey" };

/** Everything a tab is drawn from: what the panel has read, and what is being typed or done. */
export interface SettingsView {
  pane: SettingsPane;
  device: DeviceSettings;
  /** The brain's settings; null while the core does not answer. */
  brain: BrainSettings | null;
  hasKey: boolean;
  openrouter: Status["openrouter"] | null;
  spend: Status["spend"] | null;
  /** The prices of the models: being read (`loading`), read, or not to be had (`failed`). */
  pricing: Pricing | "loading" | "failed";
  checkingOpenrouter: boolean;
  devices: AudioDevices | null;
  /** The keyboard layout makes Right Alt an AltGr. */
  altgr: boolean;
  version: string;
  /** The key field is shown although there is a key (「變更」). */
  replacingKey: boolean;
  keyBusy: boolean;
  keyError: (() => string) | null;
  /** What was typed in the limit field while it is not a limit, and what is wrong with it. */
  limitDraft: string | null;
  limitError: (() => string) | null;
  /** The same for the weather city. */
  cityDraft: string | null;
  cityError: (() => string) | null;
  /** The languages the core offers (`languages.list`); null while unknown or when it cannot say. */
  languageGroups: LanguageGroup[] | null;
  /** The time the forecasts and the age of the prices are told from. */
  now: number;
}

/** The choices of a menu: those given, and `current` too if it is not one of them. */
export function withCurrent(choices: Choice[], current: string, label: (value: string) => string = (v) => v): Choice[] {
  return choices.some((c) => c.value === current) ? choices : [...choices, { value: current, label: label(current) }];
}

// ---------- what is chosen ----------

/** The chosen languages, never none. */
export const languagesOf = (v: SettingsView): string[] => normalizeLanguages(v.brain?.languages);
export const sttEngine = (v: SettingsView): string => v.brain?.stt_engine ?? "cloud";
export const sttModel = (v: SettingsView): string => v.brain?.stt_cloud_model ?? STT_MODELS[0].value;
/** What the speech-to-text menu has chosen: the cloud model, or 本機 when the engine is this computer's. */
export const sttChoice = (v: SettingsView): string => (sttEngine(v) === LOCAL_STT ? LOCAL_STT : sttModel(v));
export const cleanupModel = (v: SettingsView): string => v.brain?.cleanup_model ?? MODELS[0].value;
export const agentModel = (v: SettingsView): string => v.brain?.agent_model ?? MODELS[0].value;
export const agentCity = (v: SettingsView): string => v.brain?.agent_city ?? "";
/** The prices once they are read. */
const prices = (v: SettingsView): Pricing | null => (typeof v.pricing === "object" ? v.pricing : null);

/**
 * The microphones: automatic first, then each one found, and the one chosen before if it is not
 * plugged in now. The list has their whole names; the closed dropdown, the short ones.
 */
export function microphoneChoices(v: SettingsView): Choice[] {
  const { devices } = v;
  const choices: Choice[] = [
    { value: AUTO_MICROPHONE, get label() { return automaticMicrophoneLabel(devices); }, get short() { return automaticMicrophoneShort(devices); } },
    ...(devices?.devices ?? []).map((d) => ({ value: d.name, label: d.name, short: shortMicrophoneName(d.name) })),
  ];
  const saved = v.device.microphone;
  if (saved === null || choices.some((c) => c.value === saved)) return choices;
  return [...choices, { value: saved, label: t("onboarding.006", { p0: saved }), short: t("onboarding.006", { p0: shortMicrophoneName(saved) }) }];
}

// ---------- prices ----------

/** The choices with their prices as details, while there are prices to show. */
function withPrices(v: SettingsView, choices: Choice[], price: (id: string) => string): Choice[] {
  if (v.pricing === "failed") return choices;
  return choices.map((c) => ({ ...c, detail: v.pricing === "loading" ? PRICES_LOADING() : price(c.value) }));
}

/** The texts that depend on the prices, by the `data-price` of the element that shows them. */
export function priceTexts(v: SettingsView): Record<string, string> {
  const p = prices(v);
  const local = sttChoice(v) === LOCAL_STT ? t("settings.sttLocalPrice") : null;
  if (v.pricing === "loading") return { model: PRICES_LOADING(), "agent-model": PRICES_LOADING(), "stt-model": local ?? PRICES_LOADING(), note: "" };
  if (!p) return { model: "", "agent-model": "", "stt-model": local ?? "", note: t("settings.030") };
  return {
    model: cleanupPriceText(priceOf(p.cleanup, cleanupModel(v))),
    "agent-model": cleanupPriceText(priceOf(p.cleanup, agentModel(v))),
    "stt-model": local ?? sttPriceText(priceOf(p.stt, sttModel(v))),
    note: pricingNote(p, v.now),
  };
}

const priceSpan = (v: SettingsView, k: string, cls = ""): string =>
  `<span${cls ? ` class="${cls}"` : ""} data-price="${k}">${esc(priceTexts(v)[k] ?? "")}</span>`;

// ---------- the three model rows ----------
// Each row stands alone: its name with the price of what is chosen under it, and the control, a
// dropdown of the models the core accepts. Its menu (the models with each one's price) is the
// function after it. A picker of every OpenRouter model can take the place of one row's control in
// those two functions without touching the others. `k` names the control, its price and its menu.

/** A row of the models: its name and the price of what is chosen, then its control. */
const modelRow = (v: SettingsView, label: string, k: string, control: string): string =>
  row(label, priceSpan(v, k, "price num"), control, { cls: "row-model", attrs: ` data-row="${k}"` });

/** A dropdown of the models, which cannot be changed while the core does not answer. */
const modelSel = (v: SettingsView, choices: Choice[], current: string, label: string, k: string): string =>
  sel(choices, current, label, ` data-k="${k}"${v.brain ? "" : " disabled"}`);

/** 語音轉文字: a cloud model, or this computer. */
export function sttModelRow(v: SettingsView): string {
  const label = t("settings.031");
  return modelRow(v, label, "stt-model", modelSel(v, [...withCurrent(STT_MODELS, sttModel(v)), LOCAL_STT_CHOICE], sttChoice(v), label, "stt-model"));
}

/** Its menu: the cloud models with their prices, then this computer, which costs nothing whatever the prices say. */
export function sttModelChoices(v: SettingsView): Choice[] {
  const cloud = withPrices(v, withCurrent(STT_MODELS, sttModel(v)), (id) => sttPriceShort(priceOf(prices(v)?.stt, id)));
  return [...cloud, v.pricing === "failed" ? LOCAL_STT_CHOICE : { ...LOCAL_STT_CHOICE, detail: t("settings.sttLocalPrice") }];
}

/** 整理文字: the model that cleans up what was said. */
export function cleanupModelRow(v: SettingsView): string {
  const label = t("settings.035");
  return modelRow(v, label, "model", modelSel(v, withCurrent(MODELS, cleanupModel(v)), cleanupModel(v), label, "model"));
}

export function cleanupModelChoices(v: SettingsView): Choice[] {
  return withPrices(v, withCurrent(MODELS, cleanupModel(v)), (id) => cleanupPriceShort(priceOf(prices(v)?.cleanup, id)));
}

/** 拉斐爾的回答: the model that words Raphael's answers (and decides what a request is when Jev cannot). */
export function answerModelRow(v: SettingsView): string {
  const label = t("settings.agentModel");
  return modelRow(v, label, "agent-model", modelSel(v, withCurrent(MODELS, agentModel(v)), agentModel(v), label, "agent-model"));
}

export function answerModelChoices(v: SettingsView): Choice[] {
  return withPrices(v, withCurrent(MODELS, agentModel(v)), (id) => cleanupPriceShort(priceOf(prices(v)?.cleanup, id)));
}

/** The small print above the choices of the cleanup and answer menus, once there are prices. */
export const tokenPriceHead = (v: SettingsView): string | undefined => (prices(v) ? CLEANUP_PRICE_HEAD() : undefined);

// ---------- the OpenRouter rows ----------

/** How the latest contact with OpenRouter went: a dot and a word, and the latency or the time of the check. */
function keyStatus(v: SettingsView): string {
  const { openrouter } = v;
  if (openrouter === null) return "";
  const more = (text: string): string => `<span class="status-more">${esc(text)}</span>`;
  switch (openrouter.state) {
    case "ok":
      return st("ok", t("settings.040")) + (openrouter.latency_ms !== null ? more(t("settings.041", { p0: openrouter.latency_ms })) : "");
    case "error":
      return (
        st("err", openrouter.error === "auth" ? t("settings.036") : t("settings.042")) +
        (openrouter.checked_at_ms !== null ? more(t("settings.043", { p0: formatClock(openrouter.checked_at_ms) })) : "")
      );
    default:
      return st("idle", t("settings.044"));
  }
}

/** The key: with one, how OpenRouter took it and the buttons to check again or change it; without, the field to paste one. */
function keyRow(v: SettingsView): string {
  const attrs = ' data-row="openrouter-key"';
  if (v.hasKey && !v.replacingKey) {
    return row(
      t("onboarding.022"),
      keyStatus(v),
      btn(v.checkingOpenrouter ? t("onboarding.011") : t("settings.046"), "", ` data-act="or-check"${v.checkingOpenrouter ? " disabled" : ""}`) +
        btn(t("home.018"), "", ' data-act="key-replace" data-k="key"'),
      { attrs, cls: "row-key" },
    );
  }
  return row(
    t("onboarding.022"),
    t("settings.039"),
    fieldHTML("sk-or-v1-…", "", { w: 200, get label() { return t("onboarding.029"); }, id: "key-input", type: "password", invalid: v.keyError !== null }) +
      btn(v.keyBusy ? t("onboarding.011") : t("onboarding.030"), "pri", ` data-act="key-paste"${v.keyBusy ? " disabled" : ""}`) +
      (v.hasKey ? btn(t("onboarding.031"), "", ` data-act="key-cancel"${v.keyBusy ? " disabled" : ""}`) : ""),
    { attrs, cls: "row-key" },
  );
}

/** This month's spend against the limit, and when it will run out at this pace. */
function spendRow(v: SettingsView): string {
  const attrs = ' data-row="raphael-spend"';
  const { spend } = v;
  if (!spend) return row(t("settings.050"), "", esc(t("settings.usageUnavailable")), { attrs });
  const { month_usd: month, limit_usd: limit } = spend;
  const used = spentShare(month, limit);
  let forecast = "";
  if (month >= limit) forecast = t("settings.047");
  else if (used >= WARN_FROM) {
    const { m, d } = runOutDay(month, limit, v.now);
    forecast = t("settings.048", { p0: m, p1: d });
  } else if (spend.forecast_usd > 0) forecast = t("settings.049", { p0: formatUsd(spend.forecast_usd) });
  return row(t("settings.050"), month >= limit ? st("warn", forecast) : esc(forecast), spendVisual(month, limit), { attrs });
}

/** Key-wide usage is separate from Raphael's local spending and its editable limit. */
function usageRow(v: SettingsView): string {
  const { hasKey, openrouter, checkingOpenrouter } = v;
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

// ---------- the parts of every tab ----------

/** A group of settings: a short name, then its rows on one card. */
const section = (title: string, rows: string[], foot = ""): string =>
  `<section class="sgroup"><h2>${esc(title)}</h2><div class="slist">${rows.join("")}</div>` +
  (foot ? `<p class="foot">${foot}</p>` : "") +
  `</section>`;

/**
 * A shortcut: its keys as caps and, for one that can be changed here, a 「變更」 button that records
 * new ones (`change` names the button for the click handler and the focus).
 */
function shortcutRow(name: string, bindings: readonly string[], change?: { act: string; field: HotkeyField }): string {
  const caps = bindings.length > 0
    ? bindings.map((binding) => keys(bindingLabels(binding))).join(t("onboarding.009"))
    : `<span class="val">${esc(t("home.noKeys"))}</span>`;
  return row(
    name,
    "",
    `<span class="caps">${caps}</span>` + (change ? btn(t("home.018"), "", ` data-act="${change.act}" data-k="${change.act}"`) : ""),
    { cls: "row-shortcut", attrs: change ? ` data-row="${HOTKEY_ROWS[change.field]}"` : "" },
  );
}

/** The first lines of every tab: its name, which has the focus when the panel opens, and what the tab is for. */
export const headingHTML = (v: SettingsView): string =>
  `<h1 id="set-title" tabindex="-1">${esc(paneTitle(v.pane))}</h1>` +
  `<p class="purpose">${esc(PANES.find((p) => p.id === v.pane)!.purpose(v.device))}</p>`;

/** What the tabs that need the core say when it does not answer. */
const coreNote = (v: SettingsView): string => (v.brain === null ? `<div class="alert core-note">${st("err", t("settings.052"))}</div>` : "");

// ---------- the tabs ----------

/** 一般: the language of the window, and how Raphael looks and starts on this computer. */
function generalHTML(v: SettingsView): string {
  const d = v.device;
  return (
    headingHTML(v) +
    section(t("settings.groupAppearance"), [
      row(t("settings.059"), "", sel(UI_LANGUAGES, d.ui_language, t("settings.059"), ' data-k="ui-language"')),
      row(t("settings.067"), "", sel(APPEARANCES, d.appearance, t("settings.067"), ' data-k="appearance"')),
      row(t("settings.069"), "", sel(SIZES, String(d.emblem_size), t("settings.069"), ' data-k="size"')),
      row(t("settings.071"), t("settings.072"), tg(d.subtitles !== "off", t("settings.071"), ' data-k="subtitles"')),
    ]) +
    section(t("settings.groupStartup"), [
      row(t("settings.073"), "", tg(d.autostart, t("settings.073"), ' data-k="autostart"')),
    ])
  );
}

/** 聽寫: how a dictation starts (the keys and the mode), and how Raphael listens (the languages and the microphone). */
function dictationHTML(v: SettingsView): string {
  const d = v.device;
  const usesAltRight = d.dictation_keys.some((binding) => binding.split("+").includes("AltRight"));
  return (
    headingHTML(v) +
    coreNote(v) +
    section(t("settings.groupStart"), [
      shortcutRow(t("home.020"), d.dictation_keys, { act: "rec", field: "dictation_keys" }),
      ...(v.altgr && usesAltRight ? [note(st("warn", t("settings.054")))] : []),
      ...(d.paste_last_keys.length ? [shortcutRow(t("home.021"), d.paste_last_keys)] : []),
      shortcutRow(t("onboarding.031"), ["Escape"]),
      row(t("settings.055"), "", sel(DICTATION_MODES, d.dictation_mode, t("settings.055"), ' data-k="mode"')),
    ]) +
    section(t("settings.groupListen"), [
      row(
        t("transcription.title"),
        esc(t("settings.spokenLanguagesDesc")),
        languageSelectHTML(v.languageGroups ?? [], languagesOf(v), ` data-k="languages"${v.brain ? "" : " disabled"}`),
        { cls: "row-lang" },
      ),
      // The bar under the dropdown moves with the microphone's loudness while this tab is open.
      row(
        t("onboarding.021"),
        "",
        `<span class="mic-ctl">${sel(microphoneChoices(v), d.microphone ?? AUTO_MICROPHONE, t("onboarding.021"), ' data-k="mic"')}${levelBar()}</span>`,
        { cls: "row-mic" },
      ),
    ])
  );
}

/** 拉斐爾: the key that asks her and the city of the weather. The model that answers is with the other models. */
function agentHTML(v: SettingsView): string {
  const cityShown = v.cityDraft ?? agentCity(v);
  return (
    headingHTML(v) +
    coreNote(v) +
    section(t("settings.groupStart"), [
      // an app from before the question key has none yet
      shortcutRow(t("settings.agentKeys"), v.device.agent_keys ?? [], { act: "rec-agent", field: "agent_keys" }),
    ]) +
    section(t("settings.groupAnswers"), [
      row(
        t("settings.agentCity"),
        t("settings.agentCityDesc"),
        fieldHTML(t("settings.agentCityHint"), cityShown, { w: 200, get label() { return t("settings.agentCity"); }, id: "agent-city", invalid: v.cityError !== null, disabled: v.brain === null }),
      ),
      ...(v.cityError ? [note(st("err", v.cityError()), ' role="alert"')] : []),
    ])
  );
}

/** 模型與用量: the OpenRouter key and what this month has cost against the limit, then the models and their prices. */
function usageHTML(v: SettingsView): string {
  const limitShown = v.limitDraft ?? String(v.brain?.monthly_limit_usd ?? 10);
  return (
    headingHTML(v) +
    coreNote(v) +
    section(OPENROUTER, [
      keyRow(v),
      ...(v.keyError ? [note(st("err", v.keyError()), ' role="alert"')] : []),
      spendRow(v),
      row(
        t("onboarding.032"),
        t("onboarding.033"),
        fieldHTML("", limitShown, { w: 110, pre: "US$", get label() { return t("onboarding.032"); }, num: true, id: "limit-input", invalid: v.limitError !== null, disabled: v.brain === null }),
      ),
      ...(v.limitError ? [note(st("err", v.limitError()), ' role="alert"')] : []),
      usageRow(v),
    ], esc(t("settings.usageDifference"))) +
    section(t("settings.groupModels"), [sttModelRow(v), cleanupModelRow(v), answerModelRow(v)], priceSpan(v, "note"))
  );
}

/** 關於: the version, the license and the fan-project statement. */
function aboutHTML(v: SettingsView): string {
  return (
    headingHTML(v) +
    `<div class="about-brand">${brandIcon("about-icon")}<span class="about-name">Raphael</span></div>` +
    `<div class="slist about">` +
    row(t("settings.078"), "", `<span class="val num">${esc(v.version || "0.1.0")}</span>`) +
    row(t("settings.079"), t("settings.080"), btn(t("settings.081"), "", ' data-act="license"')) +
    `</div>` +
    t("settings.082")
  );
}

/** The tab that is open, as HTML. */
export function paneHTML(v: SettingsView): string {
  switch (v.pane) {
    case "general":
      return generalHTML(v);
    case "dictation":
      return dictationHTML(v);
    case "agent":
      return agentHTML(v);
    case "usage":
      return usageHTML(v);
    case "about":
      return aboutHTML(v);
  }
}

/** A tab whose settings are still being read: its name and purpose, and the shape of what is to come. */
export function loadingHTML(v: SettingsView): string {
  return headingHTML(v) +
    `<p class="settings-loading" role="status">${esc(t("settings.loading"))}</p>` +
    `<div class="settings-skeleton" aria-hidden="true">${Array.from({ length: 3 }, () =>
      `<div class="skeleton-section"><i class="skeleton-heading"></i><div class="skeleton-card"><div class="skeleton-row"><i></i><i></i></div><div class="skeleton-row"><i></i><i></i></div></div></div>`).join("")}</div>`;
}
