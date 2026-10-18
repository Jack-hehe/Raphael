// The main window: a sidebar with home, history and dictionary, settings at the bottom, and the
// page that is chosen. The pages are in pages/; this file holds what they share (spec §12).

import { loadGlyphTexture } from "../overlay/emblem";
import * as api from "./api";
import { createDictionary } from "./pages/dictionary";
import { createHistory } from "./pages/history";
import { createHome } from "./pages/home";
import { createSettings } from "./pages/settings";
import { DeviceState } from "./device";
import { resumeModels } from "./models";
import { mountOnboarding } from "./onboarding";
import type { AppContext, DeviceSettings, MainEvent, Page, PageId } from "./types";
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
  autostart: true,
  onboarded: false,
};

/** A page that comes on screen again within this time of the last time is not loaded again. */
const RESHOW_MS = 1000;

const NAV: { id: PageId; name: string; icon: IconName }[] = [
  { id: "home", name: "首頁", icon: "home" },
  { id: "history", name: "歷史紀錄", icon: "clock" },
  { id: "dictionary", name: "字典", icon: "book" },
];

// ---------- the theme ----------

const prefersDark = matchMedia("(prefers-color-scheme: dark)");
/** The device settings, kept right when reads and writes overlap. */
const device = new DeviceState(
  DEFAULT_DEVICE,
  { get: api.deviceSettingsGet, set: api.deviceSettingsSet },
  () => applyTheme(),
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
  };

  const pages: Record<PageId, Page> = {
    home: createHome(ctx),
    history: createHistory(ctx),
    dictionary: createDictionary(ctx),
    settings: createSettings(ctx),
  };

  const nav = document.getElementById("nav")!;
  const main = document.getElementById("main")!;
  const button = (id: PageId, name: string, icon: IconName, extra = ""): string =>
    `<button type="button" class="nav${extra}" data-go="${id}" aria-current="false">${ico(icon)}<span>${name}</span></button>`;
  nav.innerHTML =
    NAV.map((item) => button(item.id, item.name, item.icon)).join("") + button("settings", "設定", "sliders", " nav-bottom");
  nav.addEventListener("click", (e) => {
    const target = (e.target as Element).closest<HTMLElement>("[data-go]");
    if (target) go(target.dataset.go as PageId);
  });
  for (const page of Object.values(pages)) main.append(page.el);

  let current: PageId | null = null;
  let shownAt = 0;

  function go(id: PageId): void {
    if (current && current !== id) pages[current].hide();
    current = id;
    for (const page of Object.values(pages)) page.el.hidden = page.id !== id;
    nav.querySelectorAll<HTMLElement>(".nav").forEach((item) => item.setAttribute("aria-current", item.dataset.go === id ? "page" : "false"));
    main.scrollTop = 0;
    shownAt = performance.now();
    void pages[id].show();
  }

  // Back from the tray: what a page shows may be old. (The settings page keeps what is being typed.)
  const comeBack = (): void => {
    if (!current || current === "settings" || performance.now() - shownAt < RESHOW_MS) return;
    shownAt = performance.now();
    void pages[current].show();
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
