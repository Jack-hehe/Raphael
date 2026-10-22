// Raphael's own settings, on the 拉斐爾 page: the key that asks her, the city of the weather and
// Telegram, in one sheet that slides in from the right over the conversations. It opens from the
// sliders button at the top of the page, keeps the focus inside while it is open, and closes with
// Escape, its ✕ or a click beside it, giving the focus back to the button. The rows are an icon, a
// word and the control; the only sentences are errors.

import { onLocaleChange, t } from "./i18n";

import * as api from "./api";
import { describeError } from "./format";
import { type HotkeyRecording, createHotkeyRecording } from "./hotkey-recording";
import { bindingLabels } from "./keys";
import { type Messaging, type MessagingView, createMessaging, messagingHTML } from "./messaging";
import type { AppContext, DeviceSettings } from "./types";
import { esc, fieldHTML, ico, keys, note, st, toaster } from "./ui";

/** Everything the sheet's rows are drawn from. */
export interface SheetView {
  device: DeviceSettings;
  /** The keyboard layout makes Right Alt an AltGr key. */
  altgr: boolean;
  /** The weather city; null while it is not known (the core did not answer): the field waits. */
  city: string | null;
  /** What is typed in the city field while it is not saved. */
  cityDraft: string | null;
  cityError: boolean;
  messaging: MessagingView;
}

/** An icon-only, bordered button: what it does is its label and its tooltip. */
export const iconButton = (icon: Parameters<typeof ico>[0], label: string, attrs = "", cls = ""): string =>
  `<button type="button" class="btn icon${cls ? " " + cls : ""}" aria-label="${esc(label)}" title="${esc(label)}"${attrs}>${ico(icon)}</button>`;

/** The frame: a title for the dialog to be named by, the close button and the body that is drawn. */
export function sheetFrameHTML(): string {
  return (
    `<div class="ra-scrim" data-act="sheet-close"></div>` +
    `<aside class="ra-sheet" role="dialog" aria-modal="true" aria-label="${esc(t("agent.settings"))}" data-open="false">` +
    `<header class="ra-sheet-head"><h2 id="ra-sheet-title" tabindex="-1">${ico("sliders")}<span>${esc(t("agent.settingsShort"))}</span></h2>` +
    iconButton("x", t("common.close"), ' data-act="sheet-close"') +
    `</header><div class="ra-sheet-body"></div></aside>`
  );
}

/** The rows: the key that asks Raphael, the weather city, then Telegram. */
export function sheetBodyHTML(v: SheetView): string {
  const binding = v.device.agent_keys?.[0];
  const caps = binding ? keys(bindingLabels(binding)) : `<span class="ra-none" aria-label="${esc(t("home.noKeys"))}">—</span>`;
  const altgr = v.altgr && (v.device.agent_keys ?? []).some((b) => b.split("+").includes("AltRight"));
  const city = v.cityDraft ?? v.city ?? "";
  return (
    `<section class="ra-group" aria-label="${esc(t("agent.title"))}">` +
    `<div class="ra-row" data-row="agent-hotkey"><span class="ra-ic" aria-hidden="true">${ico("keyboard")}</span>` +
    `<span class="ra-t">${esc(t("settings.agentKeys"))}</span><span class="ra-grow"></span>` +
    `<span class="caps">${caps}</span>${iconButton("pencil", t("agent.hotkeyChange"), ' data-act="rec-agent"')}</div>` +
    (altgr ? note(st("warn", t("settings.054"))) : "") +
    `<div class="ra-row"><span class="ra-ic" aria-hidden="true">${ico("cloud")}</span>` +
    `<span class="ra-t">${esc(t("agent.city"))}</span><span class="ra-grow"></span>` +
    fieldHTML(t("settings.agentCityHint"), city, { w: 168, label: t("agent.cityAria"), id: "agent-city", invalid: v.cityError, disabled: v.city === null }) +
    `</div>` +
    (v.cityError ? note(st("err", t("settings.agentCityEmpty")), ' role="alert"') : "") +
    `</section>` +
    messagingHTML(v.messaging)
  );
}

/** What a key does while the sheet is open: Escape closes it (unless a shortcut is being recorded), Tab stays inside. */
export function sheetKey(key: string, recording: boolean): "close" | "trap" | null {
  if (key === "Escape") return recording ? null : "close";
  if (key === "Tab") return "trap";
  return null;
}

/** Something the focus can rest on. */
export interface FocusStop {
  focus(): void;
}

/** Keeps Tab inside: from the last stop to the first, and with Shift from the first to the last. */
export function trapFocus(shift: boolean, stops: readonly FocusStop[], focused: FocusStop | null): FocusStop | null {
  if (stops.length === 0) return null;
  const first = stops[0];
  const last = stops[stops.length - 1];
  const at = focused ? stops.indexOf(focused) : -1;
  if (shift && at <= 0) return last;
  if (!shift && (at === -1 || at === stops.length - 1)) return first;
  return null; // inside the loop: the browser moves it
}

export interface RaphaelSheet {
  /** The scrim and the sheet, for the page to hold. */
  el: HTMLElement;
  open(): void;
  close(): void;
  isOpen(): boolean;
}

/** `opener` is the button the focus goes back to when the sheet closes. */
export function createRaphaelSheet(app: AppContext, opener: () => HTMLElement | null): RaphaelSheet {
  const el = document.createElement("div");
  el.className = "ra-sheet-wrap";
  el.innerHTML = sheetFrameHTML();
  const sheet = el.querySelector<HTMLElement>(".ra-sheet")!;
  const body = el.querySelector<HTMLElement>(".ra-sheet-body")!;
  const title = el.querySelector<HTMLElement>("#ra-sheet-title")!;
  let open = false;
  let altgr = false;
  let city: string | null = null;
  let cityDraft: string | null = null;
  let cityError = false;
  let loads = 0;

  const messaging: Messaging = createMessaging({ visible: () => open, redraw: () => render() });
  const recording: HotkeyRecording = createHotkeyRecording(app, () => {
    if (open) render();
  });

  const view = (): SheetView => ({ device: app.device(), altgr, city, cityDraft, cityError, messaging: messaging.view() });

  /** Draws the rows again where the reader is: the focus and what is being typed stay. */
  function render(): void {
    const active = document.activeElement as HTMLElement | null;
    const focusedId = active && body.contains(active) ? active.id : "";
    const focusedAct = active && body.contains(active) ? (active.dataset.msg ?? active.dataset.act ?? "") : "";
    const typedToken = el.querySelector<HTMLInputElement>("#msg-token")?.value;
    const typedCity = focusedId === "agent-city" ? el.querySelector<HTMLInputElement>("#agent-city")?.value : undefined;
    body.innerHTML = sheetBodyHTML(view());
    const token = el.querySelector<HTMLInputElement>("#msg-token");
    if (token && typedToken !== undefined) token.value = typedToken;
    const cityInput = el.querySelector<HTMLInputElement>("#agent-city");
    if (cityInput && typedCity !== undefined) cityInput.value = typedCity;
    if (focusedId) el.querySelector<HTMLElement>(`#${CSS.escape(focusedId)}`)?.focus();
    else if (focusedAct) el.querySelector<HTMLElement>(`[data-msg="${CSS.escape(focusedAct)}"], [data-act="${CSS.escape(focusedAct)}"]`)?.focus();
  }

  async function load(): Promise<void> {
    const mine = ++loads;
    void messaging.load();
    const [settings, layout] = await Promise.allSettled([api.settingsGet(), api.keyboardHasAltgr()]);
    if (mine !== loads || !open) return;
    city = settings.status === "fulfilled" ? (settings.value.settings.agent_city ?? "") : null;
    altgr = layout.status === "fulfilled" && layout.value;
    render();
  }

  async function commitCity(input: HTMLInputElement): Promise<void> {
    const value = input.value.trim();
    if (value === "") {
      cityDraft = input.value;
      cityError = true;
      render();
      return;
    }
    cityDraft = null;
    cityError = false;
    if (value === city) return render();
    try {
      city = (await api.settingsSet("agent_city", value)).settings.agent_city ?? value;
    } catch (err) {
      toaster.info(describeError(err));
    }
    if (open) render();
  }

  function onKey(e: KeyboardEvent): void {
    if (!open) return;
    const action = sheetKey(e.key, recording.active());
    if (action === "close" && !e.defaultPrevented) {
      e.preventDefault();
      close();
    } else if (action === "trap") {
      const stops = [...sheet.querySelectorAll<HTMLElement>("button:not([disabled]), input:not([disabled]), a[href], [tabindex='0']")].filter(
        (stop) => stop.offsetParent !== null,
      );
      const to = trapFocus(e.shiftKey, stops, document.activeElement as HTMLElement | null);
      if (to) {
        e.preventDefault();
        to.focus();
      }
    }
  }

  el.addEventListener("click", (e) => {
    const target = e.target as Element;
    if (messaging.click(target)) return;
    const act = target.closest<HTMLElement>("[data-act]")?.dataset.act;
    if (act === "sheet-close") close();
    else if (act === "rec-agent") {
      const cell = el.querySelector<HTMLElement>('[data-row="agent-hotkey"] .caps');
      if (cell) void recording.start("agent_keys", cell);
    }
  });
  el.addEventListener("input", (e) => {
    const input = e.target as HTMLInputElement;
    if (messaging.input(input)) return;
    if (input.id === "agent-city") cityDraft = input.value;
  });
  // `change` comes when the field loses focus or Enter is pressed: no save button needed.
  el.addEventListener("change", (e) => {
    const input = e.target as HTMLInputElement;
    if (input.id === "agent-city") void commitCity(input);
  });
  el.addEventListener("keydown", (e) => void messaging.key(e));

  onLocaleChange(() => {
    title.querySelector("span")!.textContent = t("agent.settingsShort");
    sheet.setAttribute("aria-label", t("agent.settings"));
    const closeButton = el.querySelector<HTMLElement>(".ra-sheet-head [data-act='sheet-close']")!;
    closeButton.setAttribute("aria-label", t("common.close"));
    closeButton.title = t("common.close");
    if (open) render();
  });

  function openSheet(): void {
    if (open) return;
    open = true;
    cityDraft = null;
    cityError = false;
    render();
    sheet.dataset.open = "true";
    el.dataset.open = "true";
    document.addEventListener("keydown", onKey);
    title.focus({ preventScroll: true });
    void load();
  }

  function close(): void {
    if (!open) return;
    open = false;
    loads++;
    sheet.dataset.open = "false";
    el.dataset.open = "false";
    document.removeEventListener("keydown", onKey);
    messaging.reset();
    void recording.cancel();
    opener()?.focus();
  }

  return { el, open: openSheet, close, isOpen: () => open };
}
