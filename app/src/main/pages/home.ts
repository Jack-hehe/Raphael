// Home: Raphael's report on the stage, then what the history says: four insight cards
// (words, time saved, speed, time spoken), and the activity card with the active days, the streaks
// and a grid of the days (`stats.summary`, all from this computer's history). Shortcuts follow
// the activity, or sit on the right in wide windows. The key, connection and spend are in settings; home only
// says so when one of them keeps dictation from working fully. Loads when shown and every 30 seconds.

import { onLocaleChange, t } from "../i18n";

import { getVersion } from "@tauri-apps/api/app";
import * as api from "../api";
import { homeHint } from "../dictation";
import { loadingHTML } from "../loading";
import { formatUsd } from "../format";
import { buildGrid, heatmapSVG, weeksThatFit } from "../heatmap";
import { bindingLabels } from "../keys";
import { blockingAlert, buildReport, coreDownReport } from "../report";
import { Stage } from "../stage";
import { durationParts, formatCount, hasDictations, periodSpeed } from "../stats";
import type { AppContext, Page, Status, StatsSummary } from "../types";
import { btn, esc, iconBtn, keys, st } from "../ui";

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

/** An insight card: the value (HTML, with its units set smaller) and what it is. */
const card = (value: string, label: string, none = false): string =>
  `<div class="icard"><div class="v${none ? " none" : ""}">${value}</div><div class="k">${esc(label)}</div></div>`;

function insightsHTML(s: StatsSummary): string {
  const all = s.all;
  const speed = s.speed_wpm ?? periodSpeed(all);
  return (
    `<section class="insights" aria-labelledby="ins-h">` +
    t("home.001") +
    `<span class="sum num">${esc(t("home.002", { p0: formatCount(s.month.dictations), p1: formatUsd(s.month.cost_usd) }))}</span></div>` +
    `<div class="icards">` +
    card(unit(formatCount(all.words), t("home.003")), t("home.004")) +
    card(duration(s.time_saved_seconds), t("home.005")) +
    (speed === null ? card(t("home.006"), t("home.007"), true) : card(unit(formatCount(speed), t("home.008")), t("home.007"))) +
    card(duration(all.audio_seconds), t("home.009")) +
    `</div></section>`
  );
}

/** An older core does not say the active days and the longest streak: they come from the days it lists. */
function activityHead(s: StatsSummary): string {
  const active = s.active_days ?? s.by_day.filter((d) => d.dictations > 0).length;
  const longest = s.longest_streak_days ?? s.streak_days;
  const n = (v: number, label: string): string =>
    `<div class="anum"><div class="v">${unit(formatCount(v), t("home.010"))}</div><div class="k">${esc(label)}</div></div>`;
  return `<div class="anums">${n(active, t("home.011"))}${n(s.streak_days, t("home.012"))}${n(longest, t("home.013"))}</div>`;
}

const LEGEND =
  () => t("home.014") +
  [1, 2, 3, 4].map((k) => `<i class="h${k}"></i>`).join("") +
  t("home.015");

export function createHome(app: AppContext): Page {
  const el = document.createElement("div");
  el.className = "page page-home";
  el.dataset.page = "home";
  el.hidden = true;
  el.innerHTML =
    `<div class="home">` +
    `<div class="home-main">` +
    `<div class="stage home-stage" role="region" aria-label="Raphael"><canvas class="emb" aria-hidden="true"></canvas><div class="subs" aria-live="polite"></div></div>` +
    `<div class="alert-slot"></div><div class="usage"></div>` +
    `</div>` +
    `<aside class="home-side" aria-label="${esc(t("home.shortcuts"))}"><section class="card keys-card"></section></aside>` +
    `</div>` +
    `<p class="ver num home-version" hidden></p>`;
  const alertSlot = el.querySelector<HTMLElement>(".alert-slot")!;
  const usage = el.querySelector<HTMLElement>(".usage")!;
  const keysCard = el.querySelector<HTMLElement>(".keys-card")!;
  const ver = el.querySelector<HTMLElement>(".ver")!;

  const stage = new Stage(
    el.querySelector("canvas")!,
    el.querySelector<HTMLElement>(".subs")!,
    (target) => (target === "settings" ? app.go("settings") : void retry()),
  );

  let visible = false;
  let timer: ReturnType<typeof setInterval> | undefined;
  /** Counts the loads, so that an answer that comes after a newer load started is dropped. */
  let loads = 0;
  let stats: StatsSummary | null = null;
  let lastStatus: Status | null = null;
  let version = "";
  /** How many days the grid asks for, and how many weeks back from this one it shows. */
  let daysWanted = DAYS_FIRST;
  let back = 0;

  function hintHTML(): string {
    const parts = app.device().dictation_keys.map((binding) => keys(bindingLabels(binding)));
    return `${parts.join(t("home.017"))}<span>${esc(homeHint(app.device().dictation_mode))}</span>`;
  }

  function keysCardHTML(): string {
    const d = app.device();
    const shortcut = (name: string, bindings: string[], note = ""): string =>
      `<div class="kc-row"><div class="kc-name">${esc(name)}</div>` +
      `<div class="kc-keys">${bindings.map((b) => keys(bindingLabels(b))).join("")}</div>` +
      (note ? `<div class="kc-note">${esc(note)}</div>` : "") +
      `</div>`;
    return (
      t("home.019", { p0: btn(t("home.018"), "quiet sm", ' data-go="settings"') }) +
      shortcut(t("home.020"), d.dictation_keys, homeHint(d.dictation_mode)) +
      (d.paste_last_keys.length ? shortcut(t("home.021"), d.paste_last_keys) : "") +
      shortcut(t("onboarding.031"), ["Escape"])
    );
  }

  /** The activity grid alone, drawn again when the page changes width or pages back. */
  function paintGrid(): void {
    const box = el.querySelector<HTMLElement>(".heat-box");
    if (!box || !stats) return;
    const grid = buildGrid(stats.by_day, weeksThatFit(box.clientWidth || 600), back);
    box.innerHTML = heatmapSVG(grid);
    el.querySelector<HTMLButtonElement>('[data-act="older"]')!.disabled = !(grid.older || daysWanted < DAYS_ALL);
    el.querySelector<HTMLButtonElement>('[data-act="newer"]')!.disabled = back === 0;
  }

  function activityHTML(s: StatsSummary): string {
    return (
      `<section class="card activity" aria-labelledby="activity-h"><h2 id="activity-h">${esc(t("home.activity"))}</h2><div class="activity-body">` +
      activityHead(s) +
      `<div class="heat-wrap">${iconBtn("left", t("home.023"), ' data-act="older"')}<div class="heat-box"></div>${iconBtn("right", t("home.024"), ' data-act="newer"')}</div>` +
      LEGEND() +
      `</div></section>`
    );
  }

  function paint(status: Status | null, s: StatsSummary | null): void {
    lastStatus = status;
    const now = Date.now();
    const report = status ? buildReport(status, now) : coreDownReport();
    // A retry already lives beside the report; avoid repeating the same warning below it.
    const alert = status && report.action?.target === "settings" ? blockingAlert(status) : null;
    // The line under the stage carries the way to the settings; the stage keeps only "try again".
    stage.show(alert && report.action?.target === "settings" ? { zh: report.zh, ja: report.ja } : report);
    alertSlot.innerHTML = alert
      ? `<div class="alert" role="status">${st("warn", alert.text)}${btn(esc(alert.action), "sm", ' data-go="settings"')}</div>`
      : "";
    keysCard.innerHTML = keysCardHTML();

    stats = s;
    if (s && hasDictations(s)) {
      usage.innerHTML = insightsHTML(s) + activityHTML(s);
      paintGrid();
    } else if (s) {
      usage.innerHTML =
        t("home.025") +
        t("home.026") +
        `<p class="hint">${hintHTML()}</p></section>`;
    } else {
      // no numbers from the core (it is down, or older than this page)
      usage.innerHTML = `<p class="hint">${hintHTML()}</p>`;
    }
  }

  /** `replay`: the page was just shown, so the report starts again. */
  async function load(replay: boolean): Promise<void> {
    const mine = ++loads;
    if (!stats) usage.innerHTML = loadingHTML();
    usage.setAttribute("aria-busy", "true");
    if (replay) stage.play();
    const [statusResult, statsResult] = await Promise.allSettled([api.statusGet(), api.statsSummary(daysWanted)]);
    if (mine !== loads || !visible) return;
    // a core that does not answer: the report says so
    const status = statusResult.status === "fulfilled" ? statusResult.value : null;
    const s = statsResult.status === "fulfilled" ? statsResult.value : null;
    paint(status, s);
    usage.setAttribute("aria-busy", "false");
    if (replay && status?.openrouter.state === "unknown") void checkOpenrouter();
  }

  /** Nothing has been sent to OpenRouter yet since the core started: ask once. */
  async function checkOpenrouter(): Promise<void> {
    try {
      await api.statusCheckOpenrouter();
    } catch {
      return;
    }
    if (visible) void load(false);
  }

  async function retry(): Promise<void> {
    try {
      await api.statusCheckOpenrouter();
    } catch {
      // the report says what is wrong
    }
    if (visible) void load(true);
  }

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

  el.addEventListener("click", (e) => {
    const target = e.target as Element;
    const go = target.closest<HTMLElement>("[data-go]");
    if (go) app.go(go.dataset.go as "settings" | "history");
    const act = target.closest<HTMLElement>("[data-act]")?.dataset.act;
    if (act === "older" || act === "newer") void turn(act === "older");
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
    paint(lastStatus, stats);
  });

  return {
    id: "home",
    el,
    show() {
      visible = true;
      ver.hidden = false;
      void load(true);
      clearInterval(timer);
      timer = setInterval(() => {
        if (!document.hidden) void load(false);
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
