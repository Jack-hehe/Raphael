// The main window: home overview first, conversations, automation, team and dictation, this
// month's spend and the way to the settings at the bottom, and the page that is chosen. The
// settings open as a panel over the window. The pages are in pages/; this file holds what they
// share. On the 拉斐爾 page the sidebar is a rail of its icons alone (52 px), to leave room for the
// members' column: `data-rail` on <html> says so to the style sheet, and each icon is named in its
// tooltip then.

import { onLocaleChange, setLocale, t } from "./i18n";
import { getCurrentWindow } from "@tauri-apps/api/window";

import { loadGlyphTexture } from "../overlay/emblem";
import * as api from "./api";
import { avatarHTML, loadAvatar, onAvatarChange } from "./avatar";
import { createAutomationsPage } from "./pages/automations";
import { createTeamPage } from "./pages/team";
import { createHome } from "./pages/home";
import { configureRooms } from "./group-chats";
import { createAgent } from "./pages/agent";
import { createDictionary } from "./pages/dictionary";
import { createHistory } from "./pages/history";
import { createDictationOverview } from "./pages/dictation-overview";
import { createDictation } from "./pages/dictation";
import { createSettings } from "./pages/settings";
import { createNavigationPage, registerNavigationPages, resolvePage, START_PAGE } from "./navigation-pages";
import { applySkin } from "./owner-settings";
import { DeviceState } from "./device";
import { resumeModels } from "./models";
import { mountOnboarding } from "./onboarding";
import { Navigation, type Place, samePage } from "./nav-history";
import { installNavInput } from "./nav-input";
import { labelNavItem, navItemHTML, navHomeHTML } from "./nav-items";
import { installWindowHotkeys } from "./window-hotkeys";
import { installFocusKeys } from "./focus-keys";
import { followTray } from "./tray-open";
import { installWindowChrome, labelWindowControls, showMaximized } from "./window-chrome";
import type { AgentDraft, AppContext, DeviceSettings, MainEvent, MainPageId, Page, PageId, SettingsPane } from "./types";
import { spendMini } from "./spend";
import { Undoable } from "./undo";
import { Cleared } from "./cleared";
import { type IconName, esc, ico, leave, toaster } from "./ui";
import "./styles.css";

/** The glyph ring texture (app/public/brand/glyph-ring.png); if it fails to load the emblem draws a plain ring. */
const GLYPH_URL = "/brand/glyph-ring.png";

/** What the window uses until the real settings arrive (the same as device_settings.rs). */
const DEFAULT_DEVICE: DeviceSettings = {
  dictation_keys: ["AltRight"],
  dictation_mode: "toggle",
  paste_last_keys: ["AltRight+V"],
  agent_keys: ["AltRight+Space"],
  microphone: null,
  emblem_size: 150,
  subtitles: "on",
  appearance: "system",
  ui_language: "zh-TW",
  autostart: true,
  onboarded: false,
  computer_apps: [],
};

/** A page that comes on screen again within this time of the last time is not loaded again. */
const RESHOW_MS = 1000;

/** Rail destinations; Raphael uses his face rather than a line icon. */
const NAV: { id: MainPageId; name: string; icon: IconName | "face" }[] = [
  { id: "home", get name() { return t("nav.overview"); }, icon: "home" },
  { id: "agent", get name() { return t("nav.agent"); }, icon: "face" },
  { id: "automations", get name() { return t("nav.automations"); }, icon: "repeat" },
  { id: "team", get name() { return t("nav.team"); }, icon: "grid" },
  { id: "dictation", get name() { return t("nav.dictation"); }, icon: "mic" },
];

// ---------- the theme ----------

const prefersDark = matchMedia("(prefers-color-scheme: dark)");
let skin = "default";
/** The device settings, kept right when reads and writes overlap. */
const device = new DeviceState(
  DEFAULT_DEVICE,
  { get: api.deviceSettingsGet, set: api.deviceSettingsSet, removeComputerApp: api.deviceComputerAppRemove },
  () => {
    applyTheme();
    setLocale(device.get().ui_language);
  },
);

/** Light or dark, as `appearance` says; `system` follows the system. */
function applyTheme(): void {
  const { appearance } = device.get();
  applySkin(skin, appearance, prefersDark.matches);
}
prefersDark.addEventListener("change", applyTheme);

// ---------- start ----------

function mountWindowChrome(): void {
  const chrome = document.getElementById("window-chrome")!;
  const translate = (): void =>
    labelWindowControls(chrome, {
      controls: t("window.controls"),
      minimize: t("window.minimize"),
      maximize: t("window.maximize"),
      close: t("window.close"),
    });
  translate();
  onLocaleChange(translate);
  try {
    installWindowChrome(document, getCurrentWindow(), (maximized) => {
      document.documentElement.dataset.windowMaximized = String(maximized);
      showMaximized(chrome, maximized);
    });
  } catch (error) {
    console.warn("main: window controls are unavailable outside Tauri", error);
  }
}

async function start(): Promise<void> {
  applyTheme();
  // Focus rings only while Tab moves the focus (focus-keys.ts); a click or the caret shows the rest.
  installFocusKeys(document);
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
    showDay(date) {
      history.setDay(date);
      go("history");
    },
    device: () => device.get(),
    refreshDevice: () => device.refresh(),
    setDevice: (patch) => device.set(patch),
    removeComputerApp: (exe) => device.removeComputerApp(exe),
    onEvent(handler) {
      listeners.add(handler);
      return () => void listeners.delete(handler);
    },
    undo: new Undoable(toaster),
    cleared: new Cleared(),
    closeSettings: () => closeSettings(),
    rerunGuide: () => startGuide("rerun"),
  };

  const history = createHistory(ctx);
  const dictionary = createDictionary(ctx);
  const overview = createDictationOverview(ctx);
  const dictation = createDictation(ctx, { overview, history, dictionary }, tab => navigation.visit(() => { dictation.setTab(tab); main.scrollTop = 0; }));
  const agent = createAgent(ctx);
  const settings = createSettings(ctx);
  configureRooms(ctx);
  registerNavigationPages({ automations: createAutomationsPage, team: createTeamPage });
  const pages: Record<MainPageId | "settings", Page> = { home: createHome(ctx), dictation, agent, settings, automations: createNavigationPage("automations", ctx), team: createNavigationPage("team", ctx) };

  const nav = document.getElementById("nav")!;
  const main = document.getElementById("main")!;
  const guide = document.getElementById("ob")!;
  const face = (): string => avatarHTML({ size: 18, cls: "nav-face" });
  const button = (id: PageId, name: string, icon: IconName | "face"): string => navItemHTML(id, name, icon === "face" ? face() : ico(icon));
  nav.innerHTML =
    navHomeHTML() +
    NAV.map((item) => button(item.id, item.name, item.icon)).join("") +
    `<div class="side-fill"></div>` +
    `<button type="button" class="usage-box" data-go="settings" data-pane="usage" hidden></button>` +
    `<div class="side-foot">${button("settings", t("nav.settings"), "gear")}</div>`;
  const usageBox = nav.querySelector<HTMLElement>(".usage-box")!;
  const agentItem = nav.querySelector<HTMLElement>('.nav[data-go="agent"]')!;
  // 拉斐爾 shows Raphael's face, the person's picture once there is one.
  onAvatarChange(() => agentItem.querySelector(".av")?.replaceWith(document.createRange().createContextualFragment(face())));
  void loadAvatar(api.avatarGet);
  /** A gold dot on 拉斐爾 while one of his YES/NO questions waits for its answer. */
  let waitingQuestion: string | null = null;
  const markQuestion = (id: string | null): void => {
    waitingQuestion = id;
    agentItem.classList.toggle("is-asking", id !== null);
    agentItem.querySelector(".nav-dot")?.remove();
    if (id !== null) agentItem.insertAdjacentHTML("beforeend", `<span class="nav-dot" role="img" aria-label="${esc(t("agent.asking"))}"></span>`);
  };
  void api.agentQuestionOpen().then((open) => markQuestion(open?.id ?? null), () => {});
  function translateNavigation(): void {
    nav.setAttribute("aria-label", t("nav.pages"));
    const homeButton = nav.querySelector<HTMLElement>(".brand")!;
    homeButton.setAttribute("aria-label", t("nav.home"));
    homeButton.title = t("nav.home");
    markQuestion(waitingQuestion);
    for (const item of NAV) labelNavItem(nav, item.id, item.name);
    labelNavItem(nav, "settings", t("nav.settings"));
    nameRailItems();
  }

  /** Whether the sidebar is the rail of icons: while the 拉斐爾 page is the one on screen. */
  let rail = false;
  /** In the rail each icon says its page in its tooltip; in the whole sidebar the name is beside it. */
  function nameRailItems(): void {
    nav.querySelectorAll<HTMLElement>(".nav").forEach((item) => {
      const name = item.querySelector(".nav-t")?.textContent ?? "";
      if (rail && name) item.title = name;
      else item.removeAttribute("title");
    });
  }
  function setRail(on: boolean): void {
    rail = on;
    document.documentElement.dataset.rail = String(on);
    nameRailItems();
  }
  translateNavigation();
  onLocaleChange(() => {
    translateNavigation();
    void refreshUsage();
  });
  nav.addEventListener("click", (e) => {
    const target = (e.target as Element).closest<HTMLElement>("[data-go]");
    if (target) go(target.dataset.go as PageId, target.dataset.pane as SettingsPane | undefined);
  });
  async function refreshOwner(): Promise<void> {
    try {
      const { settings: value } = await api.settingsGet();
      skin = value.skin ?? "default";
      applyTheme();
    } catch { /* The window remains usable while the core restarts. */ }
  }
  ctx.onEvent(event => { if (event.type === "SettingsChanged") void refreshOwner(); });
  void refreshOwner();
  for (const page of Object.values(pages)) if (page !== settings) main.append(page.el);
  // The panel goes before the window controls, which come last in the document.
  document.getElementById("window-chrome")!.before(settings.el);
  // Keep the overview version outside the scroll container's containing block.
  document.querySelector(".wbody")!.append(overview.el.querySelector(".home-version")!);

  let current: MainPageId | null = null;
  let settingsOpen = false;
  /** What had the focus before the settings opened: it gets it back when they close. */
  let opener: HTMLElement | null = null;
  let shownAt = 0;
  /** What the app last heard of whether the 拉斐爾 page is what the window shows (`agentPageShown`). */
  let agentPageTold = false;
  /**
   * Tells the app whether the 拉斐爾 page is what the window shows: the page is the current one,
   * with no settings over it and no guide in its place. While it is and the window is in front, a
   * question to Raphael is shown here alone, and the overlay at the bottom right stays empty.
   */
  function tellAgentPage(): void {
    const shown = current === "agent" && !settingsOpen && guide.hidden !== false;
    if (shown === agentPageTold) return;
    agentPageTold = shown;
    void api.agentPageShown(shown).catch(() => {});
  }

  /** Where the window is: the page, the settings panel if it is open over it and the day the history shows. Null before the first page. */
  function where(): Place | null {
    if (!current) return null;
    const place: Place = { page: current };
    if (settingsOpen) place.pane = settings.pane();
    if (current === "dictation") place.tab = dictation.tab();
    const day = current === "dictation" && dictation.tab() === "history" ? history.day() : null;
    if (day !== null) place.day = day;
    return place;
  }

  /**
   * What the mouse's side buttons and Alt+Left / Alt+Right walk through. A change the person makes
   * is a `go`, which records it; a step shows a place again and records nothing.
   */
  const navigation = new Navigation({
    inGuide: () => !guide.hidden,
    where,
    show(place) {
      const here = where();
      // Back from the settings panel only closes it: the page under it is on screen already.
      if (!here || !samePage(here, place)) {
        if (place.page === "dictation" && place.tab) dictation.setTab(place.tab, false);
        if (place.tab === "history" || place.page === "history") history.setDay(place.day ?? null);
        showPage(place.page);
      }
      if (place.pane !== undefined) openSettings(place.pane);
      else closeSettings();
    },
  });
  installNavInput(window, (step) => navigation.step(step));

  /**
   * Goes to a page, or opens the settings panel over the one on screen (at the tab `arg` names, if
   * any). The 拉斐爾 page is handed a draft for its composer the same way.
   */
  function go(id: PageId, arg?: SettingsPane | AgentDraft): void {
    navigation.visit(() => {
      if (id === "settings") openSettings(arg?.startsWith("draft:") ? undefined : (arg as SettingsPane | undefined));
      else showPage(id, arg?.startsWith("draft:") ? arg : undefined);
    });
  }

  /**
   * Shows a page in place of the one on screen. A page that comes on screen fades in and rises a
   * little (styles.css, on its becoming visible); the same page shown again stands still.
   */
  function showPage(target: Exclude<PageId, "settings">, arg?: SettingsPane | AgentDraft): void {
    const { page: id, tab } = resolvePage(target);
    if (settingsOpen) closeSettings(false);
    if (current && current !== id) pages[current].hide();
    if (tab) dictation.setTab(tab, false);
    current = id;
    setRail(id === "agent");
    for (const page of Object.values(pages)) if (page !== settings) page.el.hidden = page.id !== id;
    nav.querySelectorAll<HTMLElement>(".nav[data-go]").forEach((item) =>
      item.setAttribute("aria-current", item.dataset.go === id ? "page" : "false"),
    );
    main.scrollTop = 0;
    shownAt = performance.now();
    void pages[id].show(arg);
    tellAgentPage();
    void refreshUsage();
  }

  /** Stops the settings' way out (`closeSettings`) when they are opened again before it is over. */
  let stopLeaving: (() => void) | null = null;

  function openSettings(pane?: SettingsPane): void {
    if (settingsOpen) return;
    settingsOpen = true;
    stopLeaving?.();
    stopLeaving = null;
    document.documentElement.dataset.windowModal = "true";
    opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    settings.el.hidden = false;
    document.querySelector<HTMLElement>(".wbody")!.inert = true;
    void settings.show(pane);
    tellAgentPage();
  }

  /**
   * Closes the settings at once for everything but the eye: the page under them works again, and the
   * panel shrinks and fades away (140 ms) before it is hidden, letting clicks through meanwhile.
   */
  function closeSettings(restoreFocus = true): void {
    if (!settingsOpen) return;
    settingsOpen = false;
    delete document.documentElement.dataset.windowModal;
    settings.hide();
    stopLeaving = leave(settings.el, 140, () => {
      settings.el.hidden = true;
      stopLeaving = null;
    });
    document.querySelector<HTMLElement>(".wbody")!.inert = false;
    if (restoreFocus) opener?.focus();
    opener = null;
    tellAgentPage();
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

  ctx.onEvent((event) => {
    if (event.type === "AgentQuestion") markQuestion(event.id);
    else if (event.type === "AgentQuestionClosed" && event.id === waitingQuestion) markQuestion(null);
  });

  // The tray's menu asks for a page. Rust sends this before it shows the window.
  ctx.onEvent((event) => followTray(event, { inGuide: () => !guide.hidden, go, addWord: () => dictionary.addWord() }));

  api.subscribeMainEvents((event) => {
    if (event.type === "DictationPasted" && document.activeElement instanceof HTMLTextAreaElement) {
      document.activeElement.dispatchEvent(new CustomEvent("dictation-pasted", { detail: event.text }));
    }
    listeners.forEach((handler) => handler(event));
  }).catch((err) => {
    console.warn("main: no events from the app", err);
  });

  /**
   * The guide takes the place of the pages: on a first run until it is done, and from the settings
   * (`rerun`) until it is done or left. A rerun never clears `onboarded`.
   */
  function startGuide(mode: "first" | "rerun"): void {
    if (!guide.hidden) return;
    closeSettings(false);
    document.documentElement.dataset.windowLayout = "onboarding";
    document.querySelector<HTMLElement>(".wbody")!.hidden = true;
    guide.hidden = false;
    tellAgentPage();
    mountOnboarding(
      guide,
      {
        mode,
        onDone: (result) => {
          guide.hidden = true;
          showPages();
          tellAgentPage();
          if (mode === "first" || !current) go(START_PAGE);
          if (result.toSettings) go("settings", "usage");
          // The speech models that the guide did not wait for come on in the background.
          void resumeModels({ status: api.modelsStatus, ensure: api.modelsEnsure }, (message) => console.warn("main:", message));
        },
      },
      ctx,
    );
  }

  // Until the first-run guide is done it takes the place of the pages. (Settings that could not be
  // read are no reason to start a guide for someone who has been through it.)
  if (deviceRead && !device.get().onboarded) {
    startGuide("first");
  } else {
    showPages();
    go(START_PAGE);
    // A guide that was left before the speech models were all there: fetch what is missing.
    void resumeModels({ status: api.modelsStatus, ensure: api.modelsEnsure }, (message) => console.warn("main:", message));
  }
}

/**
 * The pages are hidden in the HTML until `start` knows whether this is a first run: the window of
 * a first run opens while the page is still being built, and would show an empty sidebar.
 */
function showPages(): void {
  document.documentElement.dataset.windowLayout = "main";
  document.querySelector<HTMLElement>(".wbody")!.hidden = false;
}

mountWindowChrome();
start().catch((err) => {
  console.error("main: could not start", err);
  // Whatever went wrong, the window must not stay blank.
  document.getElementById("ob")!.hidden = true;
  showPages();
});
