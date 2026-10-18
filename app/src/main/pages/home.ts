// Home: Raphael's report on the stage, then how much has been dictated: this month's words, the
// time it saved, the speed, the streak and the last 14 days (`stats.summary`, all from this
// computer's history). The key, the connection and the spend are on the settings page; home only
// says so when one of them keeps dictation from working fully. Loads when shown and every 30 seconds.

import * as api from "../api";
import { homeHint } from "../dictation";
import { formatUsd } from "../format";
import { bindingLabels } from "../keys";
import { blockingAlert, buildReport, coreDownReport } from "../report";
import { Stage } from "../stage";
import { chartSVG, durationParts, formatCount, hasDictations } from "../stats";
import type { AppContext, Page, Status, StatsSummary } from "../types";
import { btn, esc, keys, st } from "../ui";

/** How often the page is read again while it is on screen. */
const REFRESH_MS = 30_000;

/** A headline number: the value (HTML, with its units set smaller) and what it is. */
function stat(value: string, label: string): string {
  return `<div class="stat"><div class="v">${value}</div><div class="k">${esc(label)}</div></div>`;
}

const unit = (n: string, u: string): string => `${esc(n)}<small>${esc(u)}</small>`;

function dashboardHTML(s: StatsSummary): string {
  const saved = durationParts(s.time_saved_seconds)
    .map(([n, u]) => unit(n, u))
    .join("");
  const month = s.month;
  return (
    `<section class="dash" aria-label="本月用量">` +
    `<div class="dash-head"><h2>本月</h2>` +
    `<span class="sum num">${esc(`${formatCount(month.dictations)} 次聽寫，花費 ${formatUsd(month.cost_usd)}`)}</span></div>` +
    `<div class="stats">` +
    stat(unit(formatCount(month.words), "字"), "聽寫字數") +
    stat(saved, "省下的打字時間") +
    (s.speed_wpm === null
      ? `<div class="stat"><div class="v none">資料還不夠</div><div class="k">平均速度</div></div>`
      : stat(unit(formatCount(s.speed_wpm), "字/分鐘"), "平均速度")) +
    stat(unit(String(s.streak_days), "天"), "連續使用") +
    `</div></section>` +
    `<section class="trend"><h3>過去 14 天</h3>${chartSVG(s.by_day)}</section>`
  );
}

export function createHome(app: AppContext): Page {
  const el = document.createElement("div");
  el.className = "page";
  el.dataset.page = "home";
  el.hidden = true;
  el.innerHTML =
    `<div class="stage"><canvas class="emb" aria-hidden="true"></canvas><div class="subs" aria-live="polite"></div></div>` +
    `<div class="alert-slot"></div><div class="usage"></div><p class="hint"></p>`;
  const alertSlot = el.querySelector<HTMLElement>(".alert-slot")!;
  const usage = el.querySelector<HTMLElement>(".usage")!;
  const hint = el.querySelector<HTMLElement>(".hint")!;

  const stage = new Stage(
    el.querySelector("canvas")!,
    el.querySelector<HTMLElement>(".subs")!,
    (target) => (target === "settings" ? app.go("settings") : void retry()),
  );

  let visible = false;
  let timer: ReturnType<typeof setInterval> | undefined;
  /** Counts the loads, so that an answer that comes after a newer load started is dropped. */
  let loads = 0;

  function hintHTML(): string {
    const parts = app.device().dictation_keys.map((binding) => keys(bindingLabels(binding)));
    return `${parts.join("<span>或</span>")}<span>${esc(homeHint(app.device().dictation_mode))}</span>`;
  }

  function paint(status: Status | null, stats: StatsSummary | null): void {
    const now = Date.now();
    const alert = status ? blockingAlert(status) : null;
    const report = status ? buildReport(status, now) : coreDownReport();
    // The line under the stage carries the way to the settings; the stage keeps only "try again".
    stage.show(alert && report.action?.target === "settings" ? { zh: report.zh, ja: report.ja } : report);
    alertSlot.innerHTML = alert
      ? `<div class="alert" role="status">${st("warn", alert.text)}${btn(esc(alert.action), "sm", ' data-go="settings"')}</div>`
      : "";

    if (stats && hasDictations(stats)) {
      usage.innerHTML = dashboardHTML(stats);
      hint.hidden = false;
      hint.innerHTML = hintHTML();
    } else if (stats) {
      usage.innerHTML =
        `<section class="dash-empty"><h2>還沒有聽寫紀錄</h2>` +
        `<p>在任何可以打字的地方說話，文字會直接貼到游標的位置。用過之後，這裡會顯示你說了多少字、省下多少打字時間，以及說話的速度。</p>` +
        `<p class="hint">${hintHTML()}</p></section>`;
      hint.hidden = true;
    } else {
      // no numbers from the core (it is down, or older than this page): only the hint
      usage.innerHTML = "";
      hint.hidden = false;
      hint.innerHTML = hintHTML();
    }
  }

  /** `replay`: the page was just shown, so the emblem appears again. */
  async function load(replay: boolean): Promise<void> {
    const mine = ++loads;
    if (replay) stage.play();
    const [statusResult, statsResult] = await Promise.allSettled([api.statusGet(), api.statsSummary()]);
    if (mine !== loads || !visible) return;
    // a core that does not answer: the report says so
    const status = statusResult.status === "fulfilled" ? statusResult.value : null;
    const stats = statsResult.status === "fulfilled" ? statsResult.value : null;
    paint(status, stats);
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

  el.addEventListener("click", (e) => {
    const go = (e.target as Element).closest<HTMLElement>("[data-go]");
    if (go) app.go(go.dataset.go as "settings" | "history");
  });

  return {
    id: "home",
    el,
    show() {
      visible = true;
      void load(true);
      clearInterval(timer);
      timer = setInterval(() => {
        if (!document.hidden) void load(false);
      }, REFRESH_MS);
    },
    hide() {
      visible = false;
      loads++;
      clearInterval(timer);
    },
  };
}
