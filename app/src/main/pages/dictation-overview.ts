// Dictation overview: four usage numbers, activity grid and shortcuts.

import { onLocaleChange, t } from "../i18n";

import { getVersion } from "@tauri-apps/api/app";
import * as api from "../api";
import { homeHint } from "../dictation";
import { buildGrid, dateOfTarget, heatAction, heatmapSVG, weeksThatFit } from "../heatmap";
import { bindingLabels } from "../keys";
import { loadingHTML } from "../loading";
import { durationParts, formatCount, hasDictations, periodSpeed } from "../stats";
import type { AppContext, Page, PageId, SettingsPane, StatsSummary } from "../types";
import { btn, esc, iconBtn, keys } from "../ui";
import "../dictation-overview.css";

/** How often the page is read again while it is on screen. */
const REFRESH_MS = 30_000;
/** The days the activity grid asks for first (26 weeks), and when paging back (the most there is). */
const DAYS_FIRST = 182;
const DAYS_ALL = 371;
const unit = (n: string, u: string): string => `${esc(n)}<small>${esc(u)}</small>`;
const duration = (seconds: number): string =>
  durationParts(seconds)
    .map(([n, u]) => unit(n, u))
    .join("");

/** One of the four numbers: the value (HTML, with its units set smaller) and what it is. */
const card = (value: string, label: string, none = false): string =>
  `<div class="icard"><div class="v${none ? " none" : ""}">${value}</div><div class="k">${esc(label)}</div></div>`;

/** The four numbers. */
function statsHTML(s: StatsSummary): string {
  const all = s.all;
  const speed = s.speed_wpm ?? periodSpeed(all);
  const estimated = (s.estimated_dictations ?? 0) > 0
    ? ` title="${esc(t("home.estimated", { p0: formatCount(s.estimated_words ?? 0) }))}"`
    : "";
  return (
    `<div class="icards"${estimated}>` +
    card(unit(formatCount(all.words), t("home.003")), t("home.004")) +
    card(duration(s.time_saved_seconds), t("home.005")) +
    (speed === null ? card(t("home.006"), t("home.007"), true) : card(unit(formatCount(speed), t("home.008")), t("home.007"))) +
    card(duration(all.audio_seconds), t("home.009")) +
    `</div>`
  );
}

/** The days, the streak and the longest streak, in a line. */
function activitySummary(s: StatsSummary): string {
  const part = (text: string): string => `<span class="num">${esc(text)}</span>`;
  return part(t("home.activeDays", { p0: formatCount(s.active_days) })) + part(t("home.streak", { p0: formatCount(s.streak_days) })) + part(t("home.longest", { p0: formatCount(s.longest_streak_days) }));
}

const legend = (): string =>
  `<div class="legend" aria-hidden="true"><span>${esc(t("home.less"))}</span>` +
  [1, 2, 3, 4].map((k) => `<i class="h${k}"></i>`).join("") +
  `<span>${esc(t("home.more"))}</span></div>`;

function activityHTML(s: StatsSummary): string {
  return (
    `<section class="card activity" aria-labelledby="activity-h">` +
    `<div class="activity-head"><h2 id="activity-h">${esc(t("home.activity"))}</h2><div class="activity-sum">${activitySummary(s)}</div>` +
    `<div class="activity-nav">${iconBtn("left", t("home.023"), ' data-act="older"')}${iconBtn("right", t("home.024"), ' data-act="newer"')}</div></div>` +
    `<div class="heat-box"></div>` +
    legend() +
    `</section>`
  );
}

/** The lasting usage cards: the four numbers, then the activity. */
export const usageHTML = (s: StatsSummary): string => statsHTML(s) + activityHTML(s);

/** Usage beside the shortcuts; the conversation lives on its own page. */
export const overviewHTML = (): string =>
  `<div class="home">` +
  `<div class="home-main"><div class="usage"></div></div>` +
  `<aside class="home-side" aria-label="${esc(t("home.shortcuts"))}">` +
  `<section class="card keys-card"></section></aside>` +
  `</div>` +
  `<p class="ver num home-version" hidden></p>`;

export function createDictationOverview(app: AppContext): Page {
  const el = document.createElement("div");
  el.className = "page page-overview";
  el.dataset.page = "overview";
  el.hidden = true;
  el.innerHTML = overviewHTML();
  const usage = el.querySelector<HTMLElement>(".usage")!;
  const keysCard = el.querySelector<HTMLElement>(".keys-card")!;
  const ver = el.querySelector<HTMLElement>(".ver")!;

  let visible = false;
  let timer: ReturnType<typeof setInterval> | undefined;
  /** Counts the loads, so that an answer that comes after a newer load started is dropped. */
  let loads = 0;
  let stats: StatsSummary | null = null;
  let version = "";
  /** How many days the grid asks for, and how many weeks back from this one it shows. */
  let daysWanted = DAYS_FIRST;
  let back = 0;
  /** The day of the grid that a Tab reaches; the newest one with a dictation until another is used. */
  let heatStop: string | null = null;

  function hintHTML(): string {
    const parts = app.device().dictation_keys.map((binding) => keys(bindingLabels(binding)));
    return `${parts.join(t("home.017"))}<span>${esc(homeHint(app.device().dictation_mode))}</span>`;
  }

  /** The shortcuts: what each does and its keys. */
  function keysCardHTML(): string {
    const d = app.device();
    const shortcut = (name: string, bindings: string[]): string =>
      `<div class="kc-row"><div class="kc-name">${esc(name)}</div>` +
      `<div class="kc-keys">${bindings.map((b) => keys(bindingLabels(b))).join(t("home.017"))}</div></div>`;
    // an app from before the question key has no `agent_keys` yet
    const agentKeys = d.agent_keys ?? [];
    return (
      `<div class="card-head"><h2>${esc(t("home.shortcuts"))}</h2>${btn(t("home.018"), "sm", ' data-go="settings" data-pane="dictation"')}</div>` +
      shortcut(t("home.020"), d.dictation_keys) +
      (agentKeys.length ? shortcut(t("home.askRaphael"), agentKeys) : "") +
      (d.paste_last_keys.length ? shortcut(t("home.021"), d.paste_last_keys) : "") +
      shortcut(t("common.cancel"), ["Escape"])
    );
  }

  /** The cell of the day, if the grid has it. */
  const heatCell = (date: string): SVGElement | null => el.querySelector<SVGElement>(`.heat [data-date="${date}"]`);

  /** The activity grid alone, drawn again when the page changes width or pages back. */
  function paintGrid(): void {
    const box = el.querySelector<HTMLElement>(".heat-box");
    if (!box || !stats) return;
    // a day that has the focus keeps it through the redraw
    const focused = dateOfTarget(document.activeElement);
    if (focused !== null) heatStop = focused;
    const grid = buildGrid(stats.by_day, weeksThatFit(box.clientWidth || 600), back);
    box.innerHTML = heatmapSVG(grid, heatStop);
    el.querySelector<HTMLButtonElement>('[data-act="older"]')!.disabled = !(grid.older || daysWanted < DAYS_ALL);
    el.querySelector<HTMLButtonElement>('[data-act="newer"]')!.disabled = back === 0;
    if (focused !== null) heatCell(focused)?.focus();
  }

  /** Moves the focus (and the Tab stop) from one day of the grid to another. */
  function moveHeatFocus(date: string): void {
    const cell = heatCell(date);
    if (!cell) return;
    el.querySelector(`.heat [data-date][tabindex="0"]`)?.setAttribute("tabindex", "-1");
    cell.setAttribute("tabindex", "0");
    heatStop = date;
    cell.focus();
  }

  function paint(s: StatsSummary | null): void {
    keysCard.innerHTML = keysCardHTML();
    // redrawing everything takes the focus from a day of the grid; it is given back below
    const focused = dateOfTarget(document.activeElement);
    if (focused !== null) heatStop = focused;
    stats = s;
    if (s && hasDictations(s)) {
      usage.innerHTML = usageHTML(s);
      paintGrid();
      if (focused !== null) heatCell(focused)?.focus();
    } else if (s) {
      usage.innerHTML = t("home.025") + t("home.026") + `<p class="hint">${hintHTML()}</p></section>`;
    } else {
      // no numbers from the core (it is down, or older than this page)
      usage.innerHTML = `<p class="hint">${hintHTML()}</p>`;
    }
  }

  async function load(): Promise<void> {
    const mine = ++loads;
    if (!stats) usage.innerHTML = loadingHTML();
    usage.setAttribute("aria-busy", "true");
    let next: StatsSummary | null;
    try { next = await api.statsSummary(daysWanted); } catch { next = null; }
    if (mine !== loads || !visible) return;
    paint(next);
    usage.setAttribute("aria-busy", "false");
  }
  app.cleared.on("dictation", () => { if (visible) void load(); });

  /** Pages the grid a screenful of weeks back or forward; reads the whole year the first time back. */
  async function turn(older: boolean): Promise<void> {
    const box = el.querySelector<HTMLElement>(".heat-box");
    if (!box || !stats) return;
    const step = weeksThatFit(box.clientWidth || 600);
    if (!older) {
      back = Math.max(0, back - step);
      paintGrid();
      return;
    }
    if (!buildGrid(stats.by_day, step, back).older && daysWanted < DAYS_ALL) {
      daysWanted = DAYS_ALL;
      try {
        stats = await api.statsSummary(daysWanted);
      } catch {
        return;
      }
    }
    if (buildGrid(stats.by_day, step, back).older) back += step;
    paintGrid();
  }

  /** What a click or a key on a day of the grid does: opens the history at that day, or moves to another day. */
  function onHeat(event: MouseEvent | KeyboardEvent): void {
    const dates = Array.from(el.querySelectorAll(".heat [data-date]"), (cell) => cell.getAttribute("data-date")!);
    const action = heatAction(
      { type: event.type === "click" ? "click" : "keydown", key: event instanceof KeyboardEvent ? event.key : undefined, target: event.target as Element | null },
      dates,
    );
    if (action === null) return;
    event.preventDefault();
    if ("open" in action) {
      heatStop = action.open;
      app.showDay(action.open);
    } else {
      moveHeatFocus(action.focus);
    }
  }

  el.addEventListener("click", (e) => {
    const target = e.target as Element;
    if (dateOfTarget(target) !== null) {
      onHeat(e);
      return;
    }
    const go = target.closest<HTMLElement>("[data-go]");
    if (go) app.go(go.dataset.go as PageId, go.dataset.pane as SettingsPane | undefined);
    const act = target.closest<HTMLElement>("[data-act]")?.dataset.act;
    if (act === "older" || act === "newer") void turn(act === "older");
  });
  el.addEventListener("keydown", (e) => {
    if (dateOfTarget(e.target as Element) !== null) onHeat(e);
  });

  // The grid shows as many weeks as fit.
  let lastWidth = 0;
  new ResizeObserver(() => {
    const width = el.querySelector<HTMLElement>(".heat-box")?.clientWidth ?? 0;
    if (width && Math.abs(width - lastWidth) > 8) {
      lastWidth = width;
      paintGrid();
    }
  }).observe(usage);

  getVersion()
    .then((v) => {
      version = v;
      ver.textContent = t("home.027", { p0: v });
    })
    .catch(() => undefined);

  onLocaleChange(() => {
    el.querySelector("aside")!.setAttribute("aria-label", t("home.shortcuts"));
    if (version) ver.textContent = t("home.027", { p0: version });
    paint(stats);
  });

  return {
    id: "dictation",
    el,
    show() {
      visible = true;
      ver.hidden = false;
      void load();
      clearInterval(timer);
      timer = setInterval(() => {
        if (!document.hidden) void load();
      }, REFRESH_MS);
    },
    hide() {
      visible = false;
      ver.hidden = true;
      loads++;
      clearInterval(timer);
    },
  };
}
