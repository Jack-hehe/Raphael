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
import {
  DROPDOWN_ROOM,
  MENU_ROOM,
  automaticMicrophoneLabel,
  automaticMicrophoneShort,
  cutToFit,
  microphoneText,
  shortMicrophoneName,
} from "../microphone";
import { type ModelPickerLook, type PickerExtra, modelPickerHTML, modelsFor, priceDescription } from "../model-picker";
import { pricingNote } from "../pricing";
import { runOutDay } from "../report";
import { WARN_FROM, spendVisual, spentShare } from "../spend";
import type { AudioDevices, BrainSettings, DeviceSettings, ModelCatalog, ModelRole, SettingsPane, Status } from "../types";
import { type Choice, type IconName, btn, esc, fieldHTML, keys, meter, note, row, sel, st, tg } from "../ui";

/** The models the core reads when none is chosen (brain/settings.py), shown while it does not answer. */
const DEFAULT_MODELS = { cleanup: "openai/gpt-6-luna", answer: "openai/gpt-6-luna", stt: "elevenlabs/scribe-v2" } as const;
/**
 * The first choice of the speech-to-text picker, above the cloud models: the engine on this computer
 * (core setting `stt_engine` "local"). One picker holds both, so that what turns speech into text is
 * one choice with one price.
 */
export const LOCAL_STT = "local";
const localStt = (): PickerExtra => ({ id: LOCAL_STT, name: t("settings.sttLocal"), price: t("settings.sttLocalPrice") });
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
  /** OpenRouter's catalog of models (`models.catalog`): being read (`loading`), read, or not to be had (`failed`). */
  catalog: ModelCatalog | "loading" | "failed";
  checkingOpenrouter: boolean;
  devices: AudioDevices | null;
  /** The keyboard layout makes Right Alt an AltGr. */
  altgr: boolean;
  /** The app's version, as Tauri gives it; empty while it is not known. */
  version: string;
  /** The key field is shown although there is a key (「變更」). */
  replacingKey: boolean;
  /** 「移除金鑰」 was pressed once and waits for the second press. */
  confirmingKeyRemoval: boolean;
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

// ---------- what is chosen ----------

/** The chosen languages, never none. */
export const languagesOf = (v: SettingsView): string[] => normalizeLanguages(v.brain?.languages);
export const sttEngine = (v: SettingsView): string => v.brain?.stt_engine ?? "cloud";
export const sttModel = (v: SettingsView): string => v.brain?.stt_cloud_model ?? DEFAULT_MODELS.stt;
/** What the speech-to-text menu has chosen: the cloud model, or 本機 when the engine is this computer's. */
export const sttChoice = (v: SettingsView): string => (sttEngine(v) === LOCAL_STT ? LOCAL_STT : sttModel(v));
export const cleanupModel = (v: SettingsView): string => v.brain?.cleanup_model ?? DEFAULT_MODELS.cleanup;
export const agentModel = (v: SettingsView): string => v.brain?.agent_model ?? DEFAULT_MODELS.answer;
export const agentCity = (v: SettingsView): string => v.brain?.agent_city ?? "";
/** The catalog once it is read. */
const catalogOf = (v: SettingsView): ModelCatalog | null => (typeof v.catalog === "object" ? v.catalog : null);

/**
 * The microphones: automatic first, then each one found, and the one chosen before if it is not
 * plugged in now. The list has their whole names, and the automatic choice the short name of the
 * one it uses; the closed dropdown has short names cut to fit it, and the tooltips the whole ones.
 */
export function microphoneChoices(v: SettingsView): Choice[] {
  const { devices } = v;
  const choices: Choice[] = [
    {
      value: AUTO_MICROPHONE,
      get label() { return automaticMicrophoneLabel(devices, MENU_ROOM); },
      get short() { return automaticMicrophoneShort(devices); },
      get title() { return automaticMicrophoneLabel(devices); },
    },
    ...(devices?.devices ?? []).map((d) => ({ value: d.name, label: d.name, short: cutToFit(shortMicrophoneName(d.name), DROPDOWN_ROOM) })),
  ];
  const saved = v.device.microphone;
  if (saved === null || choices.some((c) => c.value === saved)) return choices;
  return [...choices, { value: saved, label: t("onboarding.006", { p0: saved }), short: microphoneText("onboarding.006", saved, DROPDOWN_ROOM) }];
}

/** The bars of the microphone's meter: 3 px wide and about 2 px apart across its dropdown, as the short meter's are. */
const MIC_METER_SEGMENTS = 46;

// ---------- prices ----------

/** The full price of the model chosen for `role`, from the catalog: 「輸入 $0.10 / 輸出 $0.50 每百萬 token」. */
function chosenPrice(catalog: ModelCatalog, role: ModelRole, id: string): string {
  const model = modelsFor(catalog, role).find((m) => m.id === id);
  return model ? priceDescription(model, role) : t("pricing.001");
}

/** The texts that depend on the prices, by the `data-price` of the element that shows them. */
export function priceTexts(v: SettingsView): Record<string, string> {
  const catalog = catalogOf(v);
  const local = sttChoice(v) === LOCAL_STT ? t("settings.sttLocalPrice") : null;
  if (v.catalog === "loading") return { model: PRICES_LOADING(), "agent-model": PRICES_LOADING(), "stt-model": local ?? PRICES_LOADING(), note: "" };
  if (!catalog) return { model: "", "agent-model": "", "stt-model": local ?? "", note: t("settings.030") };
  return {
    model: chosenPrice(catalog, "cleanup", cleanupModel(v)),
    "agent-model": chosenPrice(catalog, "answer", agentModel(v)),
    "stt-model": local ?? chosenPrice(catalog, "stt", sttModel(v)),
    note: pricingNote(catalog, v.now),
  };
}

const priceSpan = (v: SettingsView, k: string, cls = ""): string =>
  `<span${cls ? ` class="${cls}"` : ""} data-price="${k}">${esc(priceTexts(v)[k] ?? "")}</span>`;

// ---------- the three model rows ----------
// Each row stands alone: its name with the price of what is chosen under it, in full words, and the
// control, the model picker (model-picker.ts) of every OpenRouter model its role can use. The page
// draws the picker's button here; settings.ts makes it work. `k` names the control and its price.

/** The three model rows, in order: what each is for, the `k` of its control and its price, and its name. */
export const MODEL_ROWS: readonly { role: ModelRole; k: string; label: () => string }[] = [
  { role: "stt", k: "stt-model", label: () => t("settings.031") },
  { role: "cleanup", k: "model", label: () => t("settings.035") },
  { role: "answer", k: "agent-model", label: () => t("settings.agentModel") },
];

/**
 * What the picker of `role` is drawn from. Its button leaves the price out, which the row says
 * under its name, and it cannot be used while the core does not answer.
 */
export function modelPickerLook(v: SettingsView, role: ModelRole): ModelPickerLook {
  const { k, label } = MODEL_ROWS.find((r) => r.role === role)!;
  return {
    role,
    key: k,
    label: label(),
    current: role === "stt" ? sttChoice(v) : role === "cleanup" ? cleanupModel(v) : agentModel(v),
    catalog: catalogOf(v),
    loading: v.catalog === "loading",
    extras: role === "stt" ? [localStt()] : [],
    showPrice: false,
    disabled: v.brain === null,
  };
}

/** A model row: its name and the price of what is chosen, then its picker. */
const modelRow = (v: SettingsView, { role, k, label }: (typeof MODEL_ROWS)[number]): string =>
  row(label(), priceSpan(v, k, "price num"), modelPickerHTML(modelPickerLook(v, role)), { cls: "row-model", attrs: ` data-row="${k}"` });

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
        btn(t("home.018"), "", ' data-act="key-replace" data-k="key"') +
        btn(v.confirmingKeyRemoval ? t("settings.keyRemoveConfirm") : t("settings.keyRemove"), "", ' data-act="key-remove"'),
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

/** The warning under a shortcut whose keys hold Right Alt, on a keyboard layout that makes that key AltGr. */
const altgrNote = (v: SettingsView, bindings: readonly string[]): string[] =>
  v.altgr && bindings.some((binding) => binding.split("+").includes("AltRight")) ? [note(st("warn", t("settings.054")))] : [];

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
  return (
    headingHTML(v) +
    coreNote(v) +
    section(t("settings.groupStart"), [
      shortcutRow(t("home.020"), d.dictation_keys, { act: "rec", field: "dictation_keys" }),
      ...altgrNote(v, d.dictation_keys),
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
      // The meter under the dropdown, as wide as it, moves with the microphone's loudness while
      // this tab is open.
      row(
        t("onboarding.021"),
        "",
        `<span class="mic-ctl">${sel(microphoneChoices(v), d.microphone ?? AUTO_MICROPHONE, t("onboarding.021"), ' data-k="mic"')}${meter(MIC_METER_SEGMENTS)}</span>`,
        { cls: "row-mic" },
      ),
    ])
  );
}

/** 拉斐爾: the key that asks her and the city of the weather. The model that answers is with the other models. */
function agentHTML(v: SettingsView): string {
  const cityShown = v.cityDraft ?? agentCity(v);
  // an app from before the question key has none yet
  const agentKeys = v.device.agent_keys ?? [];
  return (
    headingHTML(v) +
    coreNote(v) +
    section(t("settings.groupStart"), [
      shortcutRow(t("settings.agentKeys"), agentKeys, { act: "rec-agent", field: "agent_keys" }),
      ...altgrNote(v, agentKeys),
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
    section(t("settings.groupModels"), MODEL_ROWS.map((r) => modelRow(v, r)), priceSpan(v, "note"))
  );
}

/** 關於: the version, the license and the fan-project statement. */
function aboutHTML(v: SettingsView): string {
  return (
    headingHTML(v) +
    `<div class="about-brand">${brandIcon("about-icon")}<span class="about-name">Raphael</span></div>` +
    `<div class="slist about">` +
    row(t("settings.078"), "", `<span class="val num">${esc(v.version)}</span>`) +
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
