// Settings: a panel over the main window with two parts, 設定 and 關於 (spec §12.3). A change
// takes effect at once; there is no save button. The settings of the brain (limit, model, language, speech-to-text, retention) go to
// the core with `settings.set`; the settings of this computer (hotkeys, microphone, emblem, theme,
// autostart) go to the Rust side with `device_settings_set`.

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
  groupOf,
  languageButtonHTML,
  languageLabel,
  languageName,
  languagePanelHTML,
  normalizeLanguages,
  toggleLanguage,
} from "../languages";
import { KeyRecorder, bindingFromCodes, bindingLabels, keyLabel, validateCodes } from "../keys";
import { MonitorSwitch } from "../monitor";
import { cleanupPriceShort, cleanupPriceText, priceOf, pricingNote, sttPriceShort, sttPriceText } from "../pricing";
import { runOutDay } from "../report";
import { WARN_FROM, spendVisual, spentShare } from "../spend";
import type { AppContext, AudioDevices, BrainSettings, DeviceSettings, Page, Pricing, Status } from "../types";
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
const SUBTITLES: Choice[] = [
  { value: "both", get label() { return t("settings.009"); } },
  { value: "zh", get label() { return t("settings.010"); } },
  { value: "ja", get label() { return t("settings.011"); } },
  { value: "off", get label() { return t("settings.012"); } },
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
  el.className = "modal-scrim";
  el.dataset.page = "settings";
  el.hidden = true;
  el.innerHTML =
    `<div class="modal" role="dialog" aria-modal="true" aria-labelledby="set-title">` +
    t("settings.027") +
    t("settings.028", { p0: ico("gear") }) +
    t("settings.029", { p0: ico("info") }) +
    `</nav>` +
    `<div class="modal-body"></div>` +
    `${iconBtn("x", t("common.close"), ' data-act="close"').replace('class="icon-btn"', 'class="icon-btn modal-x"')}` +
    `</div>`;
  const body = el.querySelector<HTMLElement>(".modal-body")!;
  let pane: "settings" | "about" = "settings";

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
  let recording: Recording | null = null;
  /** The languages the core offers (`languages.list`); null while unknown or when it cannot say. */
  let languageGroups: LanguageGroup[] | null = null;
  /** The group of the language panel whose variants are shown. */
  let openGroup: string | null = null;
  let languagePanel: HTMLElement | null = null;
  let visible = false;
  let loading = false;
  let loads = 0;

  // ---------- drawing ----------

  function microphoneChoices(): Choice[] {
    const found = devices?.devices ?? [];
    const choices: Choice[] = [
      { value: AUTO_MICROPHONE, get label() { return t("onboarding.005"); } },
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
      case "subtitles":
        return {
          choices: SUBTITLES,
          current: d.subtitles,
          pick: (v) => void changeDevice({ subtitles: v as DeviceSettings["subtitles"] }),
        };
      default:
        return null;
    }
  }

  /** The chosen languages, never none. */
  const languages = (): string[] => normalizeLanguages(brain?.languages);

  const sttEngine = (): string => brain?.stt_engine ?? "cloud";
  const sttModel = (): string => brain?.stt_cloud_model ?? STT_MODELS[0].value;
  const cleanupModel = (): string => brain?.cleanup_model ?? MODELS[0].value;
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
    if (pricing === "loading") return { model: PRICES_LOADING(), "stt-model": PRICES_LOADING(), note: "" };
    if (!p) return { model: "", "stt-model": "", note: t("settings.030") };
    return {
      model: cleanupPriceText(priceOf(p.cleanup, cleanupModel())),
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

  function settingsHTML(): string {
    const d = app.device();
    const usesAltRight = d.dictation_keys.some((binding) => binding.split("+").includes("AltRight"));
    const limit = brain?.monthly_limit_usd ?? 10;
    const limitShown = limitDraft ?? String(limit);
    return (
      t("settings.051") +
      (brain === null ? `<p class="lede">${st("err", t("settings.052"))}</p>` : "") +
      section("keyboard", t("history.012"), [
        row(
          t("home.020"),
          t("settings.053"),
          d.dictation_keys.map((binding) => keys(bindingLabels(binding))).join(t("onboarding.009")) +
            btn(t("home.018"), "sm", ' data-act="rec"'),
          { attrs: ' data-row="hotkey"' },
        ),
        ...(altgr && usesAltRight ? [note(st("warn", t("settings.054")))] : []),
        row(t("settings.055"), esc(modeDescription(d.dictation_mode)), sel(DICTATION_MODES, d.dictation_mode, t("settings.055"), ' data-k="mode"')),
        ...(d.paste_last_keys.length
          ? [row(t("home.021"), t("settings.056"), d.paste_last_keys.map((b) => keys(bindingLabels(b))).join(t("onboarding.009")))]
          : []),
        row(t("onboarding.031"), t("settings.057"), keys(["Esc"])),
      ]) +
      section("globe", t("settings.058"), [
        row(t("settings.059"), t("settings.060"), sel(UI_LANGUAGES, d.ui_language, t("settings.059"), ' data-k="ui-language"'), { cls: "row-ui-language" }),
        row(t("settings.061"), `<span class="variant-summary">${esc(AUTO_DETECT_HINT())}</span>`, languageButtonHTML(languageGroups ?? [], languages(), ` data-k="languages" data-act="variants"${brain ? "" : " disabled"}`), { cls: "row-lang" }),
      ]) +
      section("mic", t("settings.062"), [
        row(t("onboarding.021"), t("settings.063"), meter() + sel(microphoneChoices(), d.microphone ?? AUTO_MICROPHONE, t("onboarding.021"), ' data-k="mic"') + iconBtn("refresh", t("action.refreshMicrophones"), ' data-act="mic-refresh"')),
      ]) +
      section("wave", t("settings.064"), modelRows(), priceSpan("note")) +
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
      ], esc(t("settings.usageDifference"))) +
      section("laptop", t("settings.066"), [
        row(t("settings.067"), t("settings.068"), sel(APPEARANCES, d.appearance, t("settings.067"), ' data-k="appearance"')),
        row(t("settings.069"), t("settings.070"), sel(SIZES, String(d.emblem_size), t("settings.069"), ' data-k="size"')),
        row(t("settings.071"), t("settings.072"), sel(SUBTITLES, d.subtitles, t("settings.071"), ' data-k="subtitles"')),
        row(t("settings.073"), t("settings.074"), tg(d.autostart, t("settings.073"), ' data-k="autostart"')),
        row(
          t("settings.075"),
          t("settings.076"),
          tg(brain?.learn_corrections ?? true, t("settings.075"), ` data-k="learn"${brain ? "" : " disabled"}`),
        ),
      ])
    );
  }

  function aboutHTML(): string {
    return (
      t("settings.077") +
      `<div class="about-brand">${brandIcon("about-icon")}<span class="about-name">Raphael</span></div>` +
      `<div class="slist about">` +
      row(t("settings.078"), esc(version || "0.1.0"), "") +
      row(t("settings.079"), t("settings.080"), t("settings.081")) +
      `</div>` +
      t("settings.082")
    );
  }

  function loadingHTML(): string {
    return t("settings.051") +
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
    // A menu that is open stays open when the page is drawn again (a status that came in, say).
    const openK = openAnchorKey();
    const scroll = body.scrollTop;
    body.setAttribute("aria-busy", String(loading && pane === "settings"));
    body.innerHTML = pane === "settings" ? (loading ? loadingHTML() : settingsHTML()) : aboutHTML();
    hydrateBrandIcons(body);
    const keyInput = el.querySelector<HTMLInputElement>("#key-input");
    if (keyInput && keyDraft !== undefined) keyInput.value = keyDraft;
    const limitInput = el.querySelector<HTMLInputElement>("#limit-input");
    if (limitInput && activeLimit !== undefined) limitInput.value = activeLimit;
    body.scrollTop = scroll;
    el.querySelectorAll<HTMLElement>("[data-pane]").forEach((b) => b.setAttribute("aria-current", b.dataset.pane === pane ? "page" : "false"));

    if (focused) el.querySelector<HTMLElement>(`[data-k="${focused}"]`)?.focus();
    else if (focusedId) el.querySelector<HTMLElement>(`#${CSS.escape(focusedId)}`)?.focus();
    if (openK) {
      const anchor = el.querySelector<HTMLElement>(openK === "variants" ? "[data-act='variants']" : `.sel[data-k="${openK}"]`);
      const menu = menuFor(openK);
      if (anchor && (openK === "languages" || openK === "variants")) openLanguages(anchor, true);
      else if (anchor && menu) openMenu(anchor, menu.choices, menu.current, menu.pick, menu.head);
    }
  }

  onLocaleChange(() => {
    el.querySelector(".modal-nav")!.setAttribute("aria-label", t("nav.settings"));
    el.querySelector('[data-pane="settings"] span')!.textContent = t("nav.settings");
    el.querySelector('[data-pane="about"] span')!.textContent = t("common.about");
    const close = el.querySelector('[data-act="close"]')!;
    close.setAttribute("aria-label", t("common.close"));
    close.setAttribute("title", t("common.close"));
    if (visible) render();
  });

  // ---------- the languages ----------

  /**
   * Opens the language panel under its button. `keep`: the same group stays open (drawn again);
   * else 「選擇語言變體」 opens on the first chosen language that has variants, and 語言 folded.
   */
  function openLanguages(anchor: HTMLElement, keep = false): void {
    if (!keep) openGroup = anchor.dataset.act === "variants" ? firstVariantGroup() : null;
    const panel = openPopover(anchor, languagePanelHTML(languageGroups, languages(), openGroup), {
      className: "menu lang-panel",
      role: "dialog",
      get label() { return t("settings.058"); },
      onKey: languageKey,
    });
    languagePanel = panel;
    panel.addEventListener("click", (e) => {
      const item = (e.target as Element).closest<HTMLElement>(".lang-item");
      if (!item) return;
      if (item.dataset.group) {
        openGroup = openGroup === item.dataset.group ? null : item.dataset.group;
        paintLanguages();
      } else if (item.dataset.lang) {
        void pickLanguage(item.dataset.lang);
      }
    });
    (panel.querySelector<HTMLElement>(".lang-item") ?? panel).focus();
    if (languageGroups === null) void loadLanguages();
  }

  /** The group of the first chosen language that has variants (中文 when none is chosen). */
  function firstVariantGroup(): string | null {
    const groups = languageGroups ?? [];
    for (const id of languages()) {
      const g = groupOf(groups, id);
      if (g && g.variants.length > 1) return g.id;
    }
    return groups.find((g) => g.variants.length > 1)?.id ?? null;
  }

  /** In the panel: the right arrow shows a group's variants, the left arrow folds them away again. */
  function languageKey(e: KeyboardEvent): boolean {
    const active = document.activeElement as HTMLElement | null;
    if (!languagePanel || !active || !languagePanel.contains(active)) return false;
    if (e.key === "ArrowRight" && active.dataset.group) {
      openGroup = active.dataset.group;
      paintLanguages();
      languagePanel.querySelector<HTMLElement>(`#${CSS.escape(`language-variants-${encodeURIComponent(openGroup)}`)} .lang-variant`)?.focus();
      return true;
    }
    if (e.key === "ArrowLeft" && openGroup && (active.classList.contains("lang-variant") || active.dataset.group === openGroup)) {
      const group = openGroup;
      openGroup = null;
      paintLanguages();
      languagePanel.querySelector<HTMLElement>(`[data-group="${CSS.escape(group)}"]`)?.focus();
      return true;
    }
    return false;
  }

  /** Shows the chosen languages on the button, and in the panel if it is open (keeping the focus). */
  function paintLanguages(): void {
    const button = el.querySelector<HTMLElement>('[data-k="languages"]');
    if (!button) return;
    const groups = languageGroups ?? [];
    button.setAttribute("aria-label", languageLabel(groups, languages()));
    button.title = languages().map((id) => languageName(groups, id)).join(", ");
    const anchor = menuIsOpenFor(button) ? button : null;
    if (!languagePanel || !anchor) return;
    const active = document.activeElement as HTMLElement | null;
    let again: string | null = null;
    if (active && languagePanel.contains(active)) {
      if (active.dataset.lang) again = `[data-lang="${CSS.escape(active.dataset.lang)}"]`;
      else if (active.dataset.group) again = `[data-group="${CSS.escape(active.dataset.group)}"]`;
    }
    languagePanel.innerHTML = languagePanelHTML(languageGroups, languages(), openGroup);
    placeMenu();
    (again ? languagePanel.querySelector<HTMLElement>(again) : null)?.focus();
  }

  /** Chooses or drops a language; the button shows it at once, and goes back if the core refuses. */
  async function pickLanguage(id: string): Promise<void> {
    const before = languages();
    const result = toggleLanguage(languageGroups ?? [], before, id);
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
    for (const k of ["model", "stt-model"]) {
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
        toaster.info(t("settings.083", { p0: () => bindingLabels(binding).join(" + ") }));
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

  /** The microphone is listened to while the page is on screen and the window is in use. */
  function syncMonitor(): void {
    monitor.set(visible && !document.hidden && windowFocused);
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
  async function refreshMicrophones(anchor: HTMLElement): Promise<void> {
    const mine = ++microphoneReads;
    let fresh: AudioDevices;
    try {
      fresh = await api.audioDevices();
    } catch {
      return; // the list there was stays
    }
    if (mine !== microphoneReads || !visible) return;
    const before = JSON.stringify(microphoneChoices());
    devices = fresh;
    if (JSON.stringify(microphoneChoices()) === before) return;
    const current = app.device().microphone ?? AUTO_MICROPHONE;
    const label = anchor.querySelector("span");
    if (label) label.textContent = microphoneChoices().find((c) => c.value === current)?.label ?? current;
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
      pane = paneButton.dataset.pane as "settings" | "about";
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

    if (target.closest('.tg[data-k="learn"]')) {
      if (brain) void changeBrain("learn_corrections", !(brain.learn_corrections ?? true));
      return;
    }

    switch (target.closest<HTMLElement>("[data-act]")?.dataset.act) {
      case "close":
        app.closeSettings();
        break;
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
  });
  el.addEventListener("input", (e) => {
    const input = e.target as HTMLInputElement;
    if (input.id === "limit-input") limitDraft = input.value;
  });
  // Esc closes the panel (a menu that is open takes its Esc itself: its keys do not reach here), and
  // Tab stays inside it.
  el.addEventListener("keydown", (e) => {
    if (openMenuOnArrow(e)) return;
    if (e.key === "Escape" && !recording && !e.defaultPrevented) {
      e.preventDefault();
      app.closeSettings();
    } else if (e.key === "Tab") {
      const items = Array.from(
        el.querySelectorAll<HTMLElement>("button:not([disabled]), input, a[href], [tabindex='0']"),
      ).filter((x) => x.offsetParent !== null);
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
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
      loading = true;
      pane = "settings";
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
