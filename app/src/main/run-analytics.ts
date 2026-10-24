/** Retained runs and their measured spans, alongside the existing daily analytics. */
import "./run-analytics.css";
import { t, onLocaleChange } from "./i18n";
import { usageModel } from "./usage-models";
import { cliModels } from "./cli-brains";
import { modelsFor } from "./model-picker";
import type { CatalogModel } from "./types";
import { formatUsd, describeError } from "./format";
import { faceHTML } from "./members";
import { btn as uiBtn, esc, ico, iconButton, openMenu, sel, tabs, toaster } from "./ui";
import type { MessageKey } from "./i18n/zh-TW";
import type { CliAllowances, RunAnalyticsApi, RunGrouping, RunPeriod, RunReport, RunSpan, RunSummary, RunTrace } from "./run-analytics-types";

const btn = (label: string, attrs = "") => uiBtn(label, "", attrs);
type RunKey = Exclude<Extract<MessageKey, `runs.${string}`>, "runs.minutesSeconds" | "runs.seconds">;
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
function toolName(span: RunSpan): string {
  const labels: Record<string, Exclude<Extract<MessageKey, `usage.${string}`>, "usage.deleted" | "usage.estimates">> = { web_search: "usage.web_search", web_read: "usage.web_read", computer: "usage.computer", ask_member: "usage.ask_member", files: "usage.files", routines: "usage.routines" };
  const name = span.tool || span.kind;
  return labels[name] ? t(labels[name]) : name.replace(/[_-]+/g, " ");
}
function tiles(s: RunSummary, legacy?: { duration_ms: number | null; tokens: number | null }): string {
  return `<div class="run-tiles">${([
    ["runs.time", duration(legacy ? legacy.duration_ms : s.duration_ms)], ["runs.cost", formatUsd(s.cost_usd)], ["runs.tokens", legacy?.tokens === null ? t("runs.unknown") : number(s.tokens)],
    ["runs.ai", number(s.ai_steps)], ["runs.tools", number(s.tools)], ["runs.errors", number(s.errors)],
  ] as [RunKey, string][]).map(([key, value]) => `<div class="run-tile"><span>${t(key)}</span><strong>${esc(value)}</strong></div>`).join("")}</div>`;
}

export function mountRunAnalytics(host: HTMLElement, api: RunAnalyticsApi, onTrace: (open: boolean) => void = () => {}, onModels: (models: CatalogModel[]) => void = () => {}) {
  const root = document.createElement("section"); root.className = "run-analytics"; host.append(root);
  let models: CatalogModel[] = [], allMembers = false;
  function modelHTML(id: string): string {
    return usageModel(id,models).html;
  }
  let range: RunPeriod = "month", grouping: RunGrouping = "model", report: RunReport | null = null, trace: RunTrace | null = null;
  let allowance: CliAllowances = { brains: [] }, busy = false, exportBusy = false, allowanceBusy = false, error = "", revision = 0, disposed = false;
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const params = () => ({ range, tz });
  function bars(group: RunGrouping): string {
    const all = report!.groups[group], values = group === "member" && !allMembers ? all.slice(0,5) : all, peak = Math.max(1, ...all.map(g => g.tokens || g.runs));
    return values.length ? `<ul class="run-bars">${values.map(g => `<li><div><span class="run-group-label">${group === "model" ? modelHTML(g.label) : group === "member" ? faceHTML({id:g.member_id ?? (g.label === "Raphael" ? 0 : -1),name:g.label,avatar_url:g.avatar_url},24) + esc(memberName(g.member_id,g.label)) : esc(g.label === "unknown" ? t("runs.unknown") : g.label)}</span><strong>${number(g.tokens || g.runs)}</strong></div>` +
      `<div class="run-bar-track"><i style="width:${Math.max(1, (g.tokens || g.runs) / peak * 100)}%"></i></div><small>${formatUsd(g.cost_usd)}</small></li>`).join("")}</ul>${group === "member" && all.length > 5 ? btn(t(allMembers ? "runs.showLess" : "runs.showAll"), ' data-run-act="members"') : ""}` : `<p class="ua-empty">${t("runs.empty")}</p>`;
  }
  function spanHTML(span: RunSpan, origin: number, total: number, slowest: RunSpan | undefined): string {
    const offset = Math.max(0, span.at_ms - span.ms - origin), width = Math.min(100, Math.max(.5, span.ms / total * 100));
    return `<li class="run-span"><div><span>${span.kind === "model" ? modelHTML(span.model || span.server) : esc(toolName(span))}</span>` +
      `<small>${duration(span.ms)}</small>${span === slowest ? `<b class="run-slowest">${t("runs.slowest")}</b>` : ""}` +
      `${span.outcome !== "ok" ? `<b class="danger">${esc(span.outcome)}</b>` : ""}</div><div class="run-span-track"><i style="margin-left:${Math.min(99, offset / total * 100)}%;width:${Math.min(width, 100 - Math.min(99, offset / total * 100))}%"></i></div></li>`;
  }
  function traceHTML(): string {
    const { run, spans } = trace!, origin = run.started_at_ms ?? Math.min(...spans.map(s => s.at_ms - s.ms), run.at_ms);
    const total = Math.max(1, run.duration_ms || 0, ...spans.map(s => s.at_ms - origin));
    const slowest = spans.reduce<RunSpan | undefined>((prior, span) => !prior || span.ms > prior.ms ? span : prior, undefined);
    const groups = new Map<string, RunSpan[]>();
    for (const span of spans) if (span.kind !== "model") {
      const key = `${span.server || ""}:${span.tool || span.kind}`;
      groups.set(key, [...(groups.get(key) || []), span]);
    }
    let rows = "";
    const emitted = new Set<string>();
    for (const span of spans) {
      if (span.kind === "model") { rows += spanHTML(span, origin, total, slowest); continue; }
      const key = `${span.server || ""}:${span.tool || span.kind}`;
      if (emitted.has(key)) continue; emitted.add(key);
      const calls = groups.get(key)!;
      const listId = `run-tools-${emitted.size}`;
      rows += `<li><div class="run-tool-group"><header>${iconButton("right", toolName(span), ` data-run-act="tools" aria-expanded="false" aria-controls="${listId}"`)}${esc(toolName(span))} <small>${calls.length} ${t("runs.calls")} · ${duration(calls.reduce((n, s) => n + s.ms, 0))}</small>${slowest && calls.includes(slowest) ? ` <b class="run-slowest">${t("runs.slowest")}</b>` : ""}</header><ol id="${listId}" hidden>${calls.map(s => spanHTML(s, origin, total, undefined)).join("")}</ol></div></li>`;
    }
    return `<header class="run-header">${btn(t("runs.back"), ' data-run-act="back"')}<h2>${t("runs.trace")}</h2></header><h3 class="run-question">${esc(run.title || t("runs.noTitle"))}</h3>` +
      tiles(trace!.summary, run) + `<section class="run-timeline"><div class="run-whole">${faceHTML({ id: run.member_id, name: run.member_name, avatar_url: run.avatar_url }, 28)}<div><strong>${esc(memberName(run.member_id,run.member_name))}</strong><small>${modelHTML(run.model)}</small></div><span>${duration(run.duration_ms)}</span></div>` +
      `<div class="run-whole-track"></div><ol>${rows}</ol>${!spans.length ? `<p class="ua-empty">${t("runs.unknown")}</p>` : ""}</section>`;
  }
  function render(): void {
    if (disposed) return;
    onTrace(Boolean(trace));
    const focus = root.contains(document.activeElement) ? (document.activeElement as HTMLElement).dataset.runAct : undefined;
    root.setAttribute("aria-busy", String(busy));
    let html = trace ? traceHTML() : `<header class="run-header"><h2>${t("runs.overview")}</h2><div>${sel(Object.entries(periodKey).map(([value, key]) => ({ value, label: t(key) })), range, t("usage.period"), ' data-run-act="period"')}${btn(t("runs.export"), ` data-run-act="export"${busy || exportBusy ? " disabled" : ""}`)}</div></header>`;
    if (!trace && report) html += tiles(report.summary) + `<h3>${t("runs.where")}</h3><div class="run-groups"><section class="run-members"><h4>${t("runs.member")}</h4>${bars("member")}</section><section><div class="run-segments">` +
      tabs((["model", "day", "provider", "routine"] as RunGrouping[]).map(g => ({id:g,label:t(groupKey[g]),attrs:` data-run-group="${g}" tabindex="${grouping === g ? 0 : -1}"`})),grouping,{label:t("runs.where"),kind:"nav"}) + `</div>${bars(grouping)}</section></div>` +
      `<section class="run-list"><h3>${t("runs.title")}</h3><div class="run-card">${report.runs.length ? report.runs.map(run => `<button type="button" class="btn run-row" data-run-id="${run.id}" data-run-kind="${run.kind}">` +
        faceHTML({ id: run.member_id, name: run.member_name, avatar_url: run.avatar_url }, 32) + `<span class="run-row-copy"><strong>${esc(run.title || t("runs.noTitle"))}</strong><small>${esc(memberName(run.member_id,run.member_name))} · ${esc(time(run.at_ms))}${run.status ? ` · ${esc(t(run.status === "done" ? "auto.done" : run.status === "running" || run.status === "confirm" ? "auto.running" : run.status === "skipped" ? "auto.skipped" : "auto.error"))}` : ""}</small>${modelHTML(run.model)}</span><span class="run-row-numbers"><span>${duration(run.duration_ms)}</span><small>${formatUsd(run.cost_usd)}</small></span><span class="run-chevron" aria-hidden="true">${ico("right")}</span></button>`).join("") : `<p class="ua-empty">${t("runs.empty")}</p>`}</div>` +
      (report.runs.length < report.total ? btn(t("runs.loadMore"), ` data-run-act="more"${busy ? " disabled" : ""}`) : "") + `</section><p class="ua-note">${t("runs.legacy")}</p>`;
    if (busy) html += `<p role="status" class="ua-status">${t("usage.loading")}</p>`;
    if (error) html += `<p role="alert">${esc(error)}</p>${btn(t("usage.retry"), ' data-run-act="retry"')}`;
    if (!trace) html += `<section class="run-allowance"><header><h3>${t("runs.allowance")}</h3>${btn(t("runs.refresh"), ` data-run-act="allowance"${allowanceBusy ? " disabled" : ""}`)}</header>` +
      (allowance.quotas ?? []).map(quota => `<div class="run-window"><h4>${esc(({codex:"Codex CLI",claude:"Claude",gemini:"Gemini"} as Record<string,string>)[quota.provider] ?? quota.provider)}</h4><strong>${t("runs.exhausted")}</strong><small>${quota.reset_at_ms ? `${t("runs.resets")} ${esc(time(quota.reset_at_ms))}` : t("runs.resetUnknown")}</small></div>`).join("") +
      allowance.brains.map(brain => `<h4>${esc(brain.name)}</h4>${brain.windows.map(w => `<div class="run-window"><span>${t(w.period === "fiveHour" ? "runs.fiveHour" : "runs.weekly")}</span><strong>${w.remaining_percent}% ${t("runs.remaining")}</strong><div class="run-bar-track" role="progressbar" aria-label="${esc(brain.name)}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${w.remaining_percent}"><i style="width:${Math.min(100,Math.max(0,w.remaining_percent))}%"></i></div>` +
        (w.resets_at ? `<small>${t("runs.resets")} ${esc(time(w.resets_at * 1000))}</small>` : "") + `</div>`).join("")}`).join("") + `</section>`;
    root.innerHTML = html;
    if (focus) root.querySelector<HTMLElement>(`[data-run-act="${focus}"]`)?.focus({ preventScroll: true });
  }
  async function refresh(more = false): Promise<void> {
    const current = ++revision; busy = true; error = ""; trace = null; render();
    try {
      const result = await api.usageRuns({ ...params(), ...(more && report ? { offset: report.runs.length } : {}) });
      if (disposed || current !== revision) return;
      report = more && report ? { ...result, runs: [...report.runs, ...result.runs] } : result;
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
    if (act === "tools") {
      const list = button.closest(".run-tool-group")!.querySelector<HTMLOListElement>("ol")!;
      list.hidden = !list.hidden; button.setAttribute("aria-expanded",String(!list.hidden)); return;
    }
    if (act === "period") openMenu(button, Object.entries(periodKey).map(([value, key]) => ({ value, label: t(key) })), range, value => {
      range = value as RunPeriod; report = null; void refresh();
    });
    else if (act === "back") { ++revision; busy = false; error = ""; trace = null; render(); root.scrollIntoView?.({ block: "start" }); }
    else if (act === "retry") void refresh();
    else if (act === "more") void refresh(true);
    else if (act === "members") { allMembers = !allMembers; render(); }
    else if (act === "allowance") void allowances();
    else if (act === "export") {
      exportBusy = true; render();
      try { const result = await api.usageExport(params()); if (!disposed && await api.saveUsageCsv(result.csv)) toaster.info(t("runs.exported")); }
      catch (e) { error = describeError(e); }
      exportBusy = false; render();
    } else if (button.dataset.runGroup) { grouping = button.dataset.runGroup as RunGrouping; render(); }
    else if (button.dataset.runId) {
      const current = ++revision; busy = true; error = ""; render();
      try { const result = await api.usageTrace({ kind: button.dataset.runKind as "turn" | "task" | "routine", id: Number(button.dataset.runId) }); if (current === revision && !disposed) trace = result; }
      catch (e) { if (current === revision) error = describeError(e); }
      if (current === revision) { busy = false; render(); if (trace) root.scrollIntoView?.({ block: "start" }); }
    }
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
