// The main window: a sidebar with home, history and dictionary, this month's spend and the way to
// the settings at the bottom, and the page that is chosen. The settings open as a panel over the
// window. The pages are in pages/; this file holds what they share (spec §12).

import { onLocaleChange, setLocale, t } from "./i18n";

import { loadGlyphTexture } from "../overlay/emblem";
import * as api from "./api";
import { createDictionary } from "./pages/dictionary";
import { createHistory } from "./pages/history";
import { createHome } from "./pages/home";
import { createSettings } from "./pages/settings";
import { DeviceState } from "./device";
import { resumeModels } from "./models";
import { mountOnboarding } from "./onboarding";
import { installWindowHotkeys } from "./window-hotkeys";
import type { AppContext, DeviceSettings, MainEvent, Page, PageId } from "./types";
import { spendMini } from "./spend";
import { Undoable } from "./undo";
import { type IconName, ico, toaster } from "./ui";
import "./styles.css";

/** The glyph ring texture. It is private (copied there by scripts/copy-private.mjs); without it the emblem draws a plain ring. */
const GLYPH_URL = "/private/glyph-ring.png";

/** What the window uses until the real settings arrive (the same as device_settings.rs). */
const DEFAULT_DEVICE: DeviceSettings = {
  dictation_keys: ["AltRight"],
  dictation_mode: "toggle",
  paste_last_keys: ["AltRight+V"],
  microphone: null,
  emblem_size: 150,
  subtitles: "both",
  appearance: "system",
  ui_language: "zh-TW",
  autostart: true,
  onboarded: false,
};

/** A page that comes on screen again within this time of the last time is not loaded again. */
const RESHOW_MS = 1000;

const NAV: { id: Exclude<PageId, "settings">; name: string; icon: IconName }[] = [
  { id: "home", get name() { return t("main.001"); }, icon: "home" },
  { id: "history", get name() { return t("main.002"); }, icon: "clock" },
  { id: "dictionary", get name() { return t("main.003"); }, icon: "book" },
];

// ---------- the theme ----------

const prefersDark = matchMedia("(prefers-color-scheme: dark)");
/** The device settings, kept right when reads and writes overlap. */
const device = new DeviceState(
  DEFAULT_DEVICE,
  { get: api.deviceSettingsGet, set: api.deviceSettingsSet },
  () => {
    applyTheme();
    setLocale(device.get().ui_language);
  },
);

/** Light or dark, as `appearance` says; `system` follows the system. */
function applyTheme(): void {
  const { appearance } = device.get();
  document.documentElement.dataset.theme = appearance === "system" ? (prefersDark.matches ? "dark" : "light") : appearance;
}
prefersDark.addEventListener("change", applyTheme);

// ---------- start ----------

async function start(): Promise<void> {
  applyTheme();
  let deviceRead = false;
  try {
    await device.refresh();
    deviceRead = true;
  } catch (err) {
    console.error("main: could not read the device settings", err);
  }
  installWindowHotkeys(() => device.get());
  void loadGlyphTexture(GLYPH_URL);

  // The browser's own menu (reload, back, ...) has no place in an app. Text fields keep theirs.
  document.addEventListener("contextmenu", (e) => {
    if (!(e.target instanceof HTMLInputElement)) e.preventDefault();
  });

  const listeners = new Set<(event: MainEvent) => void>();
  const ctx: AppContext = {
    go,
    device: () => device.get(),
    refreshDevice: () => device.refresh(),
    setDevice: (patch) => device.set(patch),
    onEvent(handler) {
      listeners.add(handler);
      return () => void listeners.delete(handler);
    },
    undo: new Undoable(toaster),
    closeSettings: () => closeSettings(),
  };

  const pages: Record<PageId, Page> = {
    home: createHome(ctx),
    history: createHistory(ctx),
    dictionary: createDictionary(ctx),
    settings: createSettings(ctx),
  };

  const nav = document.getElementById("nav")!;
  const main = document.getElementById("main")!;
  const button = (id: PageId, name: string, icon: IconName): string =>
    `<button type="button" class="nav" data-go="${id}" aria-current="false">${ico(icon)}<span>${name}</span></button>`;
  nav.innerHTML =
    NAV.map((item) => button(item.id, item.name, item.icon)).join("") +
    `<div class="side-fill"></div>` +
    `<button type="button" class="usage-box" data-go="settings" hidden></button>` +
    t("main.004", { p0: ico("gear") });
  const usageBox = nav.querySelector<HTMLElement>(".usage-box")!;
  function translateNavigation(): void {
    nav.setAttribute("aria-label", t("nav.pages"));
    for (const item of NAV) nav.querySelector<HTMLElement>(`[data-go="${item.id}"] span`)!.textContent = item.name;
    nav.querySelector<HTMLElement>(".nav-bottom span")!.textContent = t("nav.settings");
  }
  translateNavigation();
  onLocaleChange(() => {
    translateNavigation();
    void refreshUsage();
  });
  nav.addEventListener("click", (e) => {
    const target = (e.target as Element).closest<HTMLElement>("[data-go]");
    if (target) go(target.dataset.go as PageId);
  });
  const settings = pages.settings;
  for (const page of Object.values(pages)) if (page !== settings) main.append(page.el);
  document.body.append(settings.el);

  let current: Exclude<PageId, "settings"> | null = null;
  let settingsOpen = false;
  /** What had the focus before the settings opened: it gets it back when they close. */
  let opener: HTMLElement | null = null;
  let shownAt = 0;

  function go(id: PageId): void {
    if (id === "settings") {
      openSettings();
      return;
    }
    if (settingsOpen) closeSettings(false);
    if (current && current !== id) pages[current].hide();
    current = id;
    for (const page of Object.values(pages)) if (page !== settings) page.el.hidden = page.id !== id;
    nav.querySelectorAll<HTMLElement>(".nav[data-go]").forEach((item) =>
      item.setAttribute("aria-current", item.dataset.go === id ? "page" : "false"),
    );
    main.scrollTop = 0;
    shownAt = performance.now();
    void pages[id].show();
    void refreshUsage();
  }

  function openSettings(): void {
    if (settingsOpen) return;
    settingsOpen = true;
    opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    settings.el.hidden = false;
    document.querySelector<HTMLElement>(".wbody")!.inert = true;
    void settings.show();
  }

  function closeSettings(restoreFocus = true): void {
    if (!settingsOpen) return;
    settingsOpen = false;
    settings.hide();
    settings.el.hidden = true;
    document.querySelector<HTMLElement>(".wbody")!.inert = false;
    if (restoreFocus) opener?.focus();
    opener = null;
    void refreshUsage();
  }

  /** This month's spend in the sidebar; hidden when the core does not answer. */
  async function refreshUsage(): Promise<void> {
    try {
      const { spend } = await api.statusGet();
      usageBox.innerHTML = t("main.005", { p0: spendMini(spend.month_usd, spend.limit_usd) });
      usageBox.setAttribute("aria-label", t("main.006", { p0: usageBox.querySelector(".usage-num")?.textContent ?? "" }));
      usageBox.hidden = false;
    } catch {
      usageBox.hidden = true;
    }
  }

  // Back from the tray: what a page shows may be old. (The settings keep what is being typed.)
  const comeBack = (): void => {
    if (!current || settingsOpen || performance.now() - shownAt < RESHOW_MS) return;
    shownAt = performance.now();
    void pages[current].show();
    void refreshUsage();
  };
  window.addEventListener("focus", comeBack);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) comeBack();
  });

  api.subscribeMainEvents((event) => listeners.forEach((handler) => handler(event))).catch((err) => {
    console.warn("main: no events from the core", err);
  });

  // Until the first-run guide is done it takes the place of the pages. (Settings that could not be
  // read are no reason to start a guide for someone who has been through it.)
  if (deviceRead && !device.get().onboarded) {
    const guide = document.getElementById("ob")!;
    guide.hidden = false;
    mountOnboarding(
      guide,
      () => {
        guide.hidden = true;
        showPages();
        go("home");
      },
      ctx,
    );
  } else {
    showPages();
    go("home");
    // A guide that was left before the speech models were all there: fetch what is missing.
    void resumeModels({ status: api.modelsStatus, ensure: api.modelsEnsure }, (message) => console.warn("main:", message));
  }
}

/**
 * The pages are hidden in the HTML until `start` knows whether this is a first run: the window of
 * a first run opens while the page is still being built, and would show an empty sidebar.
 */
function showPages(): void {
  document.querySelector<HTMLElement>(".wbody")!.hidden = false;
}

start().catch((err) => {
  console.error("main: could not start", err);
  // Whatever went wrong, the window must not stay blank.
  document.getElementById("ob")!.hidden = true;
  showPages();
});
