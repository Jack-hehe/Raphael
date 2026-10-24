/** Aggregate retained usage and subscription allowances, alongside daily analytics. */
import "./run-analytics.css";
import { t, onLocaleChange } from "./i18n";
import { usageModel } from "./usage-models";
import { CLIS, cliModels } from "./cli-brains";
import { providerMark } from "./provider-marks";
import { modelsFor } from "./model-picker";
import type { CatalogModel } from "./types";
import { formatUsd, describeError } from "./format";
import { faceHTML } from "./members";
import { btn as uiBtn, esc, openMenu, sel, tabs, toaster } from "./ui";
import type { MessageKey } from "./i18n/zh-TW";
import type { CliAllowances, RunAnalyticsApi, RunGrouping, RunPeriod, RunReport, RunSummary } from "./run-analytics-types";

const btn = (label: string, attrs = "") => uiBtn(label, "", attrs);
type RunKey = Exclude<Extract<MessageKey, `runs.${string}`>, "runs.minutesSeconds" | "runs.seconds" | "runs.allowanceUpdated">;
const periodKey: Record<RunPeriod, RunKey> = { week: "runs.week", month: "runs.month", last_month: "runs.lastMonth", all: "runs.all" };
const groupKey: Record<RunGrouping, RunKey> = { member: "runs.member", model: "runs.model", day: "runs.day", provider: "runs.provider", routine: "runs.routine" };
const number = (n: number) => new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }).format(n);
const duration = (ms: number | null) => {
  if (ms === null) return t("runs.untimed");
  const seconds = Math.max(0, Math.round(ms / 1000)), m = Math.floor(seconds / 60), s = seconds % 60;
  return m ? t("runs.minutesSeconds",{m,s}) : t("runs.seconds",{s});
};
const memberName = (id: number | undefined, name: string) => id === 0 || name === "Raphael" ? t("nav.agent") : name;
const time = (ms: number) => new Date(ms).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
function tiles(s: RunSummary): string {
  return `<div class="run-tiles">${([
    ["runs.time", duration(s.duration_ms)], ["runs.cost", formatUsd(s.cost_usd)], ["runs.tokens", number(s.tokens)],
    ["runs.ai", number(s.ai_steps)], ["runs.tools", number(s.tools)], ["runs.errors", number(s.errors)],
  ] as [RunKey, string][]).map(([key, value]) => `<div class="run-tile"><span>${t(key)}</span><strong>${esc(value)}</strong></div>`).join("")}</div>`;
}

export function mountRunAnalytics(host: HTMLElement, api: RunAnalyticsApi, onModels: (models: CatalogModel[]) => void = () => {}) {
  const root = document.createElement("section"); root.className = "run-analytics"; host.append(root);
  let models: CatalogModel[] = [], allMembers = false;
  function modelHTML(id: string): string {
    return usageModel(id,models).html;
  }
  let range: RunPeriod = "month", grouping: RunGrouping = "model", report: RunReport | null = null;
  let allowance: CliAllowances = { brains: [] }, busy = false, exportBusy = false, allowanceBusy = false, error = "", revision = 0, disposed = false;
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const params = () => ({ range, tz });
  function allowanceHTML(): string {
    return allowance.brains.map(brain => {
      const cli = CLIS.find(c => c.id === brain.id), name = brain.id === "claude" ? "Claude" : cli?.name ?? brain.name;
      const quota = allowance.quotas?.find(q => q.provider === brain.id);
      const reported = brain.source === "reported";
      const periods = { fiveHour: "runs.fiveHour", weekly: "runs.weekly", weeklyOpus: "runs.weeklyOpus", weeklySonnet: "runs.weeklySonnet" } as const;
      return `<article class="run-subscription"><header><h4>${providerMark(cli?.mark ?? brain.id)}${esc(name)}</h4>` +
        (brain.windows.length ? `<span class="run-allowance-source">${t(reported ? "runs.lastReported" : "runs.liveAllowance")}</span>` : "") + `</header>` +
        (quota ? `<p class="run-quota"><strong>${t("runs.exhausted")}</strong><small>${quota.reset_at_ms ? `${t("runs.resets")} ${esc(time(quota.reset_at_ms))}` : t("runs.resetUnknown")}</small></p>` : "") +
        (!brain.windows.length ? `<p class="run-allowance-unavailable">${t("runs.allowanceUnavailable")}</p>` : brain.windows.map(w => {
          const known = typeof w.remaining_percent === "number" && Number.isFinite(w.remaining_percent);
          const remaining = Math.min(100,Math.max(0,w.remaining_percent ?? 0));
          return `<div class="run-window"><span>${t(periods[w.period])}</span>` +
            (known ? `<strong>${remaining}% ${t("runs.remaining")}</strong>` : `<span>${t("runs.allowancePercentUnknown")}</span>`) +
            (w.status ? `<small>${t(w.status === "allowed" ? "runs.allowanceAllowed" : w.status === "allowed_warning" ? "runs.allowanceWarning" : "runs.exhausted")}</small>` : "") +
            (known ? `<div class="run-bar-track" role="progressbar" aria-label="${esc(name)} ${esc(t(periods[w.period]))}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${remaining}"><i style="width:${remaining}%"></i></div>` : "") +
            (w.resets_at ? `<small>${t("runs.resets")} ${esc(time(w.resets_at * 1000))}</small>` : "") +
            (reported && w.updated_at_ms ? `<small>${t("runs.allowanceUpdated", { time: time(w.updated_at_ms) })}</small>` : "") +
            (reported && w.resets_at && w.resets_at * 1000 <= Date.now() ? `<small>${t("runs.allowanceExpired")}</small>` : "") + `</div>`;
        }).join("")) + `</article>`;
    }).join("");
  }
  function bars(group: RunGrouping): string {
    const all = report!.groups[group], values = group === "member" && !allMembers ? all.slice(0,5) : all, peak = Math.max(1, ...all.map(g => g.tokens || g.runs));
    return values.length ? `<ul class="run-bars">${values.map(g => `<li><div><span class="run-group-label">${group === "model" ? modelHTML(g.label) : group === "member" ? faceHTML({id:g.member_id ?? (g.label === "Raphael" ? 0 : -1),name:g.label,avatar_url:g.avatar_url},24) + esc(memberName(g.member_id,g.label)) : esc(g.label === "unknown" ? t("runs.unknown") : g.label)}</span><strong>${number(g.tokens || g.runs)}</strong></div>` +
      `<div class="run-bar-track"><i style="width:${Math.max(1, (g.tokens || g.runs) / peak * 100)}%"></i></div><small>${formatUsd(g.cost_usd)}</small></li>`).join("")}</ul>${group === "member" && all.length > 5 ? btn(t(allMembers ? "runs.showLess" : "runs.showAll"), ' data-run-act="members"') : ""}` : `<p class="ua-empty">${t("runs.empty")}</p>`;
  }
  function render(): void {
    if (disposed) return;
    const focus = root.contains(document.activeElement) ? (document.activeElement as HTMLElement).dataset.runAct : undefined;
    root.setAttribute("aria-busy", String(busy));
    let html = `<header class="run-header"><h2>${t("runs.overview")}</h2><div>${sel(Object.entries(periodKey).map(([value, key]) => ({ value, label: t(key) })), range, t("usage.period"), ' data-run-act="period"')}${btn(t("runs.export"), ` data-run-act="export"${busy || exportBusy ? " disabled" : ""}`)}</div></header>`;
    if (report) html += tiles(report.summary) + `<h3>${t("runs.where")}</h3><div class="run-groups"><section class="run-members"><h4>${t("runs.member")}</h4>${bars("member")}</section><section><div class="run-segments">` +
      tabs((["model", "day", "provider", "routine"] as RunGrouping[]).map(g => ({id:g,label:t(groupKey[g]),attrs:` data-run-group="${g}" tabindex="${grouping === g ? 0 : -1}"`})),grouping,{label:t("runs.where"),kind:"nav"}) + `</div>${bars(grouping)}</section></div>`;
    if (busy) html += `<p role="status" class="ua-status">${t("usage.loading")}</p>`;
    if (error) html += `<p role="alert">${esc(error)}</p>${btn(t("usage.retry"), ' data-run-act="retry"')}`;
    html += `<section class="run-allowance"><header><h3>${t("runs.allowance")}</h3>${btn(t("runs.refresh"), ` data-run-act="allowance"${allowanceBusy ? " disabled" : ""}`)}</header>` +
      allowanceHTML() + `</section>`;
    root.innerHTML = html;
    if (focus) root.querySelector<HTMLElement>(`[data-run-act="${focus}"]`)?.focus({ preventScroll: true });
  }
  async function refresh(): Promise<void> {
    const current = ++revision; busy = true; error = ""; render();
    try {
      const result = await api.usageRuns(params());
      if (disposed || current !== revision) return;
      report = result;
    } catch (e) { if (current === revision) error = describeError(e); }
    if (current === revision) { busy = false; render(); }
  }
  async function allowances(): Promise<void> {
    allowanceBusy = true; render();
    try { allowance = await api.usageAllowances(); } catch { allowance = { brains: [] }; }
    allowanceBusy = false; render();
  }
  async function click(event: Event): Promise<void> {
    const button = (event.target as Element).closest<HTMLButtonElement>("button"); if (!button || button.disabled) return;
    const act = button.dataset.runAct;
    if (act === "period") openMenu(button, Object.entries(periodKey).map(([value, key]) => ({ value, label: t(key) })), range, value => {
      range = value as RunPeriod; report = null; void refresh();
    });
    else if (act === "retry") void refresh();
    else if (act === "members") { allMembers = !allMembers; render(); }
    else if (act === "allowance") void allowances();
    else if (act === "export") {
      exportBusy = true; render();
      try { const result = await api.usageExport(params()); if (!disposed && await api.saveUsageCsv(result.csv)) toaster.info(t("runs.exported")); }
      catch (e) { error = describeError(e); }
      exportBusy = false; render();
    } else if (button.dataset.runGroup) { grouping = button.dataset.runGroup as RunGrouping; render(); }

  }
  root.addEventListener("click", click);
  const key = (event: KeyboardEvent) => {
    const tab = (event.target as Element).closest<HTMLButtonElement>("[data-run-group]");
    if (!tab || !["ArrowLeft","ArrowRight","Home","End"].includes(event.key)) return;
    event.preventDefault();
    const values = ["model","day","provider","routine"] as RunGrouping[], index = values.indexOf(grouping);
    grouping = values[event.key === "Home" ? 0 : event.key === "End" ? 3 : (index + (event.key === "ArrowRight" ? 1 : 3)) % 4];
    render(); root.querySelector<HTMLButtonElement>(`[data-run-group="${grouping}"]`)?.focus();
  };
  root.addEventListener("keydown",key);
  const stopLocale = onLocaleChange(render);
  const metadata = Promise.all([api.modelsCatalog?.().catch(() => null), api.brainsCli?.().catch(() => null)]).then(([catalog,brains]) => {
    models = [...(catalog ? [...modelsFor(catalog,"answer"),...modelsFor(catalog,"stt")] : []), ...cliModels(brains?.clis ?? [],false)];
    if (!disposed) { onModels(models); render(); }
  });
  const ready = Promise.all([refresh(), allowances(), metadata]).then(() => {});
  return { ready, refresh: async () => { await Promise.all([refresh(), allowances()]); }, destroy() { disposed = true; ++revision; stopLocale(); root.removeEventListener("click", click); root.removeEventListener("keydown",key); root.remove(); } };
}
