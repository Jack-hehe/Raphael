// Home: Raphael's report on the stage, this month's spend, how OpenRouter is, and the latest
// dictation (spec §12.3). Loads `status.get` when shown and every 30 seconds while it is.

import * as api from "../api";
import { homeHint } from "../dictation";
import { formatClock, formatLimit, formatSeconds, formatUsd } from "../format";
import { bindingLabels } from "../keys";
import { buildReport, coreDownReport, runOutDay } from "../report";
import { Stage } from "../stage";
import type { AppContext, Page, Status } from "../types";
import { btn, esc, group, keys, row, st } from "../ui";

/** How often the status is read while the page is on screen. */
const REFRESH_MS = 30_000;
/** From this share of the limit on, the bar is orange. */
const WARN_FROM = 0.8;

function spendRow(spend: Status["spend"], nowMs: number): string {
  const { month_usd: month, limit_usd: limit } = spend;
  const used = limit > 0 ? month / limit : 1;
  let forecast = "";
  if (month >= limit) forecast = "本月額度已用完";
  else if (used >= WARN_FROM) {
    const { m, d } = runOutDay(month, limit, nowMs);
    forecast = `照目前的速度，${m} 月 ${d} 日會用完`;
  } else if (spend.forecast_usd > 0) forecast = `照目前的速度，月底約 ${formatUsd(spend.forecast_usd)}`;
  const width = (Math.min(1, Math.max(0, used)) * 100).toFixed(1);
  return row(
    "本月花費",
    esc(forecast),
    `<div class="spend"><div class="bar"><i class="${used >= WARN_FROM ? "warn" : ""}" style="width:${width}%"></i></div>` +
      `<span class="val num">${esc(formatUsd(month))} / ${esc(formatLimit(limit))}</span></div>`,
  );
}

function openrouterRow(o: Status["openrouter"]): string {
  let status: string;
  let detail = "";
  switch (o.state) {
    case "ok":
      status = st("ok", "已連線");
      if (o.latency_ms !== null) detail = `延遲 ${o.latency_ms} ms`;
      break;
    case "error":
      status = st("err", o.error === "auth" ? "金鑰無效" : "無法連線");
      if (o.checked_at_ms !== null) detail = `最後檢查 ${formatClock(o.checked_at_ms)}`;
      break;
    case "no_key":
      status = st("idle", "還沒設定金鑰");
      break;
    default:
      status = st("idle", "尚未檢查");
  }
  const needsKey = o.state === "no_key" || (o.state === "error" && o.error === "auth");
  return row(
    "OpenRouter",
    "",
    status +
      (detail ? `<span class="val num">${esc(detail)}</span>` : "") +
      (needsKey ? btn("設定金鑰", "sm", ' data-go="settings"') : ""),
  );
}

function lastRow(last: Status["last"]): string {
  if (!last) return row("最近一次聽寫", `<span class="recent-txt">還沒有聽寫紀錄</span>`);
  return row(
    "最近一次聽寫",
    `<span class="recent-txt">${esc(last.text)}</span>`,
    `<span class="val num">${esc(formatClock(last.created_at_ms))}，${esc(formatSeconds(last.stt_ms + last.llm_ms))}</span>` +
      btn("全部", "quiet", ' data-go="history"'),
  );
}

export function createHome(app: AppContext): Page {
  const el = document.createElement("div");
  el.className = "page";
  el.dataset.page = "home";
  el.hidden = true;
  el.innerHTML =
    `<div class="stage"><canvas class="emb" aria-hidden="true"></canvas><div class="subs" aria-live="polite"></div></div>` +
    `<div class="rows"></div><p class="hint"></p>`;
  const rows = el.querySelector<HTMLElement>(".rows")!;
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

  function paintHint(): void {
    const parts = app.device().dictation_keys.map((binding) => keys(bindingLabels(binding)));
    hint.innerHTML = `${parts.join("<span>或</span>")}<span>${esc(homeHint(app.device().dictation_mode))}</span>`;
  }

  function paint(status: Status | null): void {
    const now = Date.now();
    stage.show(status ? buildReport(status, now) : coreDownReport());
    rows.innerHTML = status
      ? group("", [spendRow(status.spend, now), openrouterRow(status.openrouter), lastRow(status.last)])
      : "";
    paintHint();
  }

  /** `replay`: the page was just shown, so the emblem appears again. */
  async function load(replay: boolean): Promise<void> {
    const mine = ++loads;
    if (replay) stage.play();
    let status: Status | null = null;
    try {
      status = await api.statusGet();
    } catch {
      // the core does not answer: the report says so
    }
    if (mine !== loads || !visible) return;
    paint(status);
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
      // the report below says what is wrong
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
