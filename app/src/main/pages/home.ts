import * as api from "../api";
import { avatarHTML, onAvatarChange } from "../avatar";
import type { HomeItem, HomeOverview } from "../home-types";
import { getLocale, onLocaleChange, t } from "../i18n";
import { loadingHTML } from "../loading";
import { buildReport } from "../report";
import { durationParts, formatCount } from "../stats";
import { openMemberThread } from "../team-navigation";
import type { AppContext, Page } from "../types";
import { esc, ico } from "../ui";
import "../home.css";

const money = (value: number): string => value.toFixed(2);
const duration = (value: number): string => durationParts(value).map(part => part.join(" ")).join(" ");
const stamp = (at: number, date = false): string => new Date(at).toLocaleString(getLocale(), date
  ? { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }
  : { hour: "2-digit", minute: "2-digit", hour12: false });

function row(item: HomeItem, calendar = false): string {
  const face = item.kind === "routine" || item.kind === "event" ? ico("calendar")
    : item.member_id === 0 ? avatarHTML({ size: 32 }) : item.avatar_url?.startsWith("data:image/")
      ? `<img class="ho-face" src="${esc(item.avatar_url)}" alt="">` : ico("person");
  return `<button type="button" class="btn ho-row" data-home-item="${esc(item.id)}">` +
    `<span class="ho-leading">${face}</span><span class="ho-copy"><span class="ho-row-top"><strong>${esc(item.kind === "report" ? item.name : item.title)}</strong>` +
    `<time datetime="${new Date(item.at_ms).toISOString()}">${esc(stamp(item.at_ms, calendar))}</time></span>` +
    `<span class="ho-summary">${esc(item.kind === "report" ? item.summary : `${item.name} · ${t(`overview.${item.kind}`)}`)}</span></span>${ico("right")}</button>`;
}

function section(title: string, rows: HomeItem[], empty: string, count: number, key: string, expanded: Set<string>, calendar = false): string {
  const open = expanded.has(key);
  const shown = open ? rows : rows.slice(0, 4);
  return `<section class="ho-panel" data-home-section="${key}"><header><h2>${esc(title)} <span class="ho-count">${count}</span></h2>` +
    (rows.length > 4 ? `<button type="button" class="btn sm ho-link" data-home-expand="${key}" aria-expanded="${open}">${esc(t(open ? "overview.less" : "overview.all"))}${ico(open ? "up" : "down")}</button>` : "") + `</header>` +
    (shown.length ? shown.map(item => row(item, calendar)).join("") : `<p class="ho-empty">${esc(empty)}</p>`) +
    (open && count > rows.length ? `<p class="ho-empty">${esc(t("overview.limited", { p0: rows.length, p1: count }))}</p>` : "") + `</section>`;
}

function usageHTML(data: HomeOverview): string {
  const usage = data.usage;
  const share = Math.max(0, Math.min(100, usage.limit_usd > 0 ? usage.month_usd / usage.limit_usd * 100 : 0));
  const values = usage.daily;
  const max = Math.max(0.01, ...values);
  const points = values.map((value, i) => `${i * 260 / Math.max(1, values.length - 1)},${44 - Math.max(0, value) / max * 40}`).join(" ");
  const allowances = usage.allowances.brains.map(brain => `<button type="button" class="btn ho-allowance" data-home-go="usage"><strong>${esc(brain.name)}</strong>` +
    (brain.windows.length ? brain.windows.map(window => `<span title="${esc(window.resets_at ? t("overview.reset", { p0: stamp(window.resets_at * 1000, true) }) : t("overview.unknown"))}">` +
      `${esc(t(`overview.${window.period}`))}<b>${esc(window.remaining_percent === null ? t("overview.unknown") : t("overview.remaining", { p0: window.remaining_percent }))}</b></span>`).join("")
      : `<span>${esc(t("overview.unknown"))}</span>`) + `</button>`).join("");
  return `<section class="ho-panel ho-usage"><header><h2>${esc(t("overview.usage"))}</h2></header>` +
    `<button type="button" class="btn ho-spend" data-home-go="usage"><span>${esc(t("overview.month"))}</span><strong>US$ ${money(usage.month_usd)}</strong>` +
    `<span>${esc(t("overview.budget", { p0: money(usage.limit_usd) }))}</span><span class="ho-meter"><i style="width:${share}%"></i></span>` +
    `<svg class="ho-spark" viewBox="0 0 260 48" role="img" aria-label="${esc(t("overview.chart", { p0: values.map(money).join(", ") }))}"><polyline points="${points}"/></svg>` +
    `<span class="ho-chart-label">${esc(t("overview.days"))}</span></button>` + (allowances || `<p class="ho-empty">${esc(t("overview.emptyAllowances"))}</p>`) + `</section>`;
}

function dictationHTML(data: HomeOverview): string {
  const d = data.dictation;
  const numbers = [[formatCount(d.words), t("home.004")], [duration(d.time_saved_seconds), t("home.005")],
    [d.speed_wpm === null ? "—" : `${formatCount(d.speed_wpm)} ${t("home.008")}`, t("home.007")], [duration(d.audio_seconds), t("home.009")]];
  return `<button type="button" class="btn ho-dictation" data-home-go="dictation"><span>${esc(t("overview.dictation"))}</span>` +
    numbers.map(([value, label]) => `<span><strong>${esc(value)}</strong><small>${esc(label)}</small></span>`).join("") + ico("right") + `</button>`;
}

export function homeHTML(data: HomeOverview, expanded = new Set<string>()): string {
  const report = buildReport(data.report, data.generated_at_ms);
  const intro = `<header class="ho-heading"><p>${esc(new Date(data.generated_at_ms).toLocaleDateString(getLocale(), { month: "long", day: "numeric", weekday: "long" }))}</p>` +
    `<h1>${esc(t("overview.title"))}</h1><p>${esc(t("overview.subtitle"))}</p></header>`;
  const strip = `<div class="ho-raphael">${avatarHTML({ size: 54, avatar: null })}<div><strong>${esc(t("nav.agent"))}</strong><p>${esc(report.text)}</p></div>` +
    `<button type="button" class="btn sm ho-link" data-home-go="${report.action ? "report" : "agent"}">${esc(report.action?.label ?? t("home.askRaphael"))}${ico("right")}</button></div>`;
  const today = section(t("overview.today"), data.today, t("overview.emptyToday"), data.counts.today, "today", expanded);
  const reports = section(t("overview.reports"), data.reports, t("overview.emptyReports"), data.counts.reports, "reports", expanded);
  const calendar = section(t("overview.calendar"), data.calendar, t("overview.emptyCalendar"), data.counts.calendar, "calendar", expanded, true);
  return intro + strip + `<div class="ho-grid"><div>${today}${reports}</div><div>${calendar}${usageHTML(data)}</div></div>` + dictationHTML(data);
}

export function createHome(app: AppContext): Page {
  const el = document.createElement("section");
  el.className = "page page-home";
  el.dataset.page = "home";
  el.hidden = true;
  let visible = false, generation = 0;
  let data: HomeOverview | null = null;
  const expanded = new Set<string>();
  let timer: ReturnType<typeof setInterval> | undefined;
  const paint = (): void => {
    if (data) el.innerHTML = homeHTML(data, expanded);
  };
  async function load(): Promise<void> {
    const mine = ++generation;
    if (!data) el.innerHTML = loadingHTML();
    try {
      const next = await api.homeOverview();
      if (!visible || mine !== generation) return;
      data = next;
      paint();
    } catch {
      if (!visible || mine !== generation) return;
      el.innerHTML = `<p class="ho-empty" role="status">${esc(t("overview.error"))}</p><button type="button" class="btn" data-home-go="reload">${esc(t("overview.retry"))}</button>`;
    }
  }
  el.addEventListener("click", event => {
    const target = (event.target as Element).closest<HTMLElement>("[data-home-item], [data-home-go], [data-home-expand]");
    if (!target) return;
    const key = target.dataset.homeExpand;
    if (key) {
      if (expanded.has(key)) expanded.delete(key); else expanded.add(key);
      paint();
      el.querySelector<HTMLElement>(`[data-home-expand="${key}"]`)?.focus({ preventScroll: true });
      return;
    }
    const item = data && [...data.today, ...data.reports, ...data.calendar].find(item => item.id === target.dataset.homeItem);
    if (item) {
      if (item.kind === "routine") app.go("automations");
      else openMemberThread(app, item.member_id, item.turn_id ?? undefined);
      return;
    }
    switch (target.dataset.homeGo) {
      case "reload": void load(); break;
      case "usage": app.go("settings", "usage"); break;
      case "dictation": app.go("dictation"); break;
      case "automations": app.go("automations"); break;
      case "report":
        if (data && buildReport(data.report, data.generated_at_ms).action?.target === "retry") {
          void api.statusCheckOpenrouter().then(load, load);
        } else app.go("settings", data?.report.openrouter.state === "ok" ? "usage" : "models");
        break;
      default: app.go("agent");
    }
  });
  onAvatarChange(() => { if (visible) paint(); });
  onLocaleChange(() => { if (visible) void load(); });
  app.onEvent(event => {
    if (visible && ["AgentTurn", "MembersChanged", "RoutinesChanged", "SettingsChanged", "QuotaChanged", "AgentQuestion", "AgentQuestionClosed", "ConversationsChanged", "ConnectionsChanged"].includes(event.type)) void load();
  });
  app.cleared.on("dictation", () => { if (visible) void load(); });
  app.cleared.on("conversation", () => { if (visible) void load(); });
  return { id: "home", el, show() { visible = true; void load(); clearInterval(timer); timer = setInterval(() => { if (!document.hidden) void load(); }, 30_000); },
    hide() { visible = false; generation++; clearInterval(timer); } };
}
