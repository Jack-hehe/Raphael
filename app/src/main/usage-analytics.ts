/** Self-contained analytics view. Mount in a settings slot and destroy when the page leaves. */
import "./usage-analytics.css";
import { formatUsd } from "./format";
import { onLocaleChange, t } from "./i18n";
import type { MessageKey } from "./i18n/zh-TW";
import type { UsageAnalytics, UsageGroup, UsageRequest, UsageSeries } from "./usage-analytics-types";

export type { UsageAnalytics, UsageRequest } from "./usage-analytics-types";
export interface UsageAnalyticsApi { usageAnalytics(params: UsageRequest): Promise<UsageAnalytics> }
export interface UsageAnalyticsMount {
  /** Initial request completion (errors render in the component). */
  ready: Promise<void>;
  refresh(): Promise<void>;
  destroy(): void;
}

const esc = (value: string | number) => String(value).replace(/[&<>"']/g, c => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
})[c]!);
const count = (value: number) => new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }).format(value);
const share = (value: number) => `${(value * 100).toFixed(1)}%`;
const exactUsd = (value: number) => `US$${new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 20, useGrouping: false }).format(value)}`;
const compact = (value: number) => value >= 1_000_000 ? `${+(value / 1_000_000).toFixed(1)}M`
  : value >= 1000 ? `${+(value / 1000).toFixed(1)}K` : `${+value.toPrecision(3)}`;

type PlainUsageKey = Exclude<Extract<MessageKey, `usage.${string}`>, "usage.deleted" | "usage.estimates">;
const labelKeys: Record<string, PlainUsageKey> = {
  __other__: "usage.other", other: "usage.other", unknown: "usage.unknown",
  agent: "usage.agent", cleanup: "usage.cleanup", stt: "usage.stt", memory: "usage.memory",
  web_search: "usage.web_search", web_read: "usage.web_read", computer: "usage.computer",
  routines: "usage.routines", ask_member: "usage.ask_member", files: "usage.files",
  app: "usage.app", voice: "usage.voice", routine: "usage.routine", member: "usage.memberSource",
  telegram: "usage.telegram",
};
const choiceKeys = {
  "7d": "usage.7d", "30d": "usage.30d", purpose: "usage.purpose", model: "usage.model",
  provider: "usage.provider", source: "usage.source", dictations: "usage.dictations", words: "usage.words",
} as const;
function seriesLabel(series: UsageSeries, category: string): string {
  // A model or a connection's name is user data even when it matches a translated category.
  const key = series.key;
  if (key === "__other__" || key === "unknown" || (category !== "model" && category !== "provider")) {
    const translated = labelKeys[key];
    if (translated) return t(translated);
  }
  return series.label;
}

function legend(group: UsageGroup, category: string, money = false): string {
  return `<ul class="ua-legend">${group.series.map((s, i) => `<li><i class="ua-color ua-color-${i}" aria-hidden="true"></i>` +
    `<span>${esc(seriesLabel(s, category))}</span><strong>${money ? share(s.share) : count(s.total)}</strong></li>`).join("")}</ul>`;
}

/** All exact values remain keyboard-accessible; line styles also distinguish series without color. */
function chart(days: string[], group: UsageGroup, title: string, category: string, mode: "bars" | "lines", money = false): string {
  if (!group.series.length || group.total === 0) return `<p class="ua-empty">${t("usage.empty")}</p>`;
  const left = 64, width = 640, top = 18, height = 154, bottom = top + height;
  const totals = days.map((_, i) => group.series.reduce((sum, s) => sum + s.values[i]!, 0));
  const peak = Math.max(1e-9, ...(mode === "bars" ? totals : group.series.flatMap(s => s.values)));
  const magnitude = 10 ** Math.floor(Math.log10(peak / 3));
  const interval = [1, 2, 2.5, 5, 10].find(n => n * magnitude >= peak / 3)! * magnitude;
  const max = interval * 3;
  const step = width / days.length;
  const x = (i: number) => left + (i + 0.5) * step;
  const y = (v: number) => bottom - v / max * height;
  let svg = `<svg class="ua-chart" viewBox="0 0 720 218" role="group" aria-label="${esc(title)}">`;
  for (let i = 0; i <= 3; i++) {
    const value = max * i / 3, at = y(value);
    svg += `<line class="ua-grid" x1="${left}" x2="704" y1="${at}" y2="${at}"/>` +
      `<text class="ua-axis" x="56" y="${at + 4}" text-anchor="end">${money ? "$" : ""}${compact(value)}</text>`;
  }
  const prior = days.map(() => 0);
  group.series.forEach((s, seriesIndex) => {
    const name = seriesLabel(s, category);
    if (mode === "lines") {
      svg += `<path class="ua-line ua-color-${seriesIndex}" stroke-dasharray="${["none", "8 3", "3 3", "10 3 2 3", "2 4", "12 4"][seriesIndex]}" d="` +
        s.values.map((v, i) => `${i ? "L" : "M"}${x(i)},${y(v)}`).join(" ") + `"/>`;
    }
    s.values.forEach((v, i) => {
      const exact = `${days[i]} · ${name} · ${money ? exactUsd(v) : count(v)}`;
      const attrs = `class="ua-mark ua-color-${seriesIndex}" tabindex="0" role="img" aria-label="${esc(exact)}" data-tooltip="${esc(exact)}"`;
      if (mode === "bars") {
        if (v > 0) svg += `<rect ${attrs} x="${x(i) - step * 0.32}" y="${y(prior[i]! + v)}" width="${step * 0.64}" height="${v / max * height}"><title>${esc(exact)}</title></rect>`;
        prior[i]! += v;
      } else {
        svg += `<circle ${attrs} cx="${x(i)}" cy="${y(v)}" r="3"><title>${esc(exact)}</title></circle>`;
      }
    });
  });
  const stride = Math.ceil((days.length - 1) / 5);
  days.forEach((day, i) => {
    if (i % stride === 0 || i === days.length - 1) svg += `<text class="ua-axis" x="${x(i)}" y="196" text-anchor="middle">${esc(day.slice(5).replace("-", "/"))}</text>`;
  });
  return `<div class="ua-chart-scroll">${svg}</svg></div>${legend(group, category, money)}`;
}

function options(keys: readonly (keyof typeof choiceKeys)[], selected: string): string {
  return keys.map(key => `<option value="${key}" ${key === selected ? "selected" : ""}>${t(choiceKeys[key])}</option>`).join("");
}

let nextId = 0;
const mounted = new WeakMap<HTMLElement, UsageAnalyticsMount>();
export function mountUsageAnalytics(host: HTMLElement, api: UsageAnalyticsApi): UsageAnalyticsMount {
  mounted.get(host)?.destroy();
  const root = document.createElement("section");
  root.className = "usage-analytics";
  host.replaceChildren(root);
  const tooltipId = `usage-tooltip-${++nextId}`;
  let data: UsageAnalytics | null = null;
  let range: UsageRequest["range"] = "7d";
  let endDay: string | undefined;
  let costBy: "purpose" | "model" | "provider" = "purpose";
  let messageBy: "model" | "source" = "model";
  let dictationBy: "dictations" | "words" = "dictations";
  const expanded = new Set<number>();
  let more = false, loading = true, failed = false, disposed = false, revision = 0;
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;

  function render(): void {
    if (disposed) return;
    const focused = root.contains(document.activeElement) ? (document.activeElement as HTMLElement).dataset.control : undefined;
    root.setAttribute("aria-busy", String(loading));
    root.setAttribute("aria-label", t("usage.history"));
    root.innerHTML = `<header class="ua-header"><h2>${t("usage.history")}</h2><div class="ua-period" role="group" aria-label="${t("usage.period")}">` +
      `<div class="ua-segment">${(["7d", "30d"] as const).map(key => `<button type="button" class="btn" data-control="${key}" data-range="${key}" aria-pressed="${range === key}">${t(choiceKeys[key])}</button>`).join("")}</div>` +
      `<button type="button" class="btn" data-control="previous" aria-label="${t("usage.previous")}" ${!data || loading ? "disabled" : ""}>‹</button>` +
      `<button type="button" class="btn" data-control="next" aria-label="${t("usage.next")}" ${!data || loading || data.end_day >= data.today_day ? "disabled" : ""}>›</button>` +
      `</div></header>`;
    if (loading) root.innerHTML += `<p role="status" class="ua-status">${t("usage.loading")}</p>`;
    else if (failed) root.innerHTML += `<p role="alert">${t("usage.error")}</p><button type="button" class="btn" data-control="retry">${t("usage.retry")}</button>`;
    else if (data) {
      const usage = data;
      const costs = usage.cost_by[costBy];
      root.innerHTML += `<div class="ua-cost-head"><div><p class="ua-dates">${esc(usage.start_day)} — ${esc(usage.end_day)} · ${esc(usage.tz)}</p>` +
        `<strong class="ua-total">${formatUsd(costs.total)}</strong></div>` +
        `<select data-control="cost" aria-label="${t("usage.costGrouping")}">${options(["purpose", "model", "provider"], costBy)}</select></div>` +
        chart(usage.days, costs, t("usage.history"), costBy, "bars", true) + `<p class="ua-note">${t("usage.recordedCosts")}</p>`;
      root.innerHTML += `<section class="ua-section"><h3>${t("usage.members")}</h3>`;
      if (!usage.members.length) root.innerHTML += `<p class="ua-empty">${t("usage.empty")}</p>`;
      else {
        root.innerHTML += `<div class="ua-table-scroll"><table><thead><tr><th scope="col">${t("usage.member")}</th><th scope="col">${t("usage.count")}</th><th scope="col">${t("usage.cost")}</th><th scope="col">${t("usage.share")}</th></tr></thead><tbody>` +
          usage.members.slice(0, more ? undefined : 5).map(m => {
            const open = expanded.has(m.id), detailsId = `${tooltipId}-member-${m.id}`;
            return `<tr><th scope="row"><button type="button" class="btn" data-control="member-${m.id}" data-member="${m.id}" aria-expanded="${open}" aria-controls="${detailsId}">` +
              `<span aria-hidden="true">${open ? "⌄" : "›"}</span> ${esc(m.deleted ? t("usage.deleted", { id: m.id }) : m.name)}</button>` +
              (m.role ? `<small>${esc(m.role)}</small>` : "") + `</th><td>${count(m.turns)}</td><td>${formatUsd(m.cost_usd)}</td><td>${share(m.share)}</td></tr>` +
              `<tr id="${detailsId}" ${open ? "" : "hidden"}><td colspan="4"><ul class="ua-models">${m.models.map(model => `<li><span>${esc(model.model === "unknown" ? t("usage.unknown") : model.model)}</span><span>${count(model.turns)} · ${formatUsd(model.cost_usd)}</span></li>`).join("")}</ul></td></tr>`;
          }).join("") + `</tbody></table></div>`;
        if (usage.members.length > 5) root.innerHTML += `<button type="button" class="btn ua-more" data-control="more">${t(more ? "usage.less" : "usage.more")}</button>`;
      }
      root.innerHTML += `<p class="ua-note">${t("usage.memberCosts")}</p></section>` +
        `<section class="ua-section"><h3>${t("usage.tools")}</h3><strong class="ua-total">${count(usage.tools.total)}</strong>` +
        chart(usage.days, usage.tools, t("usage.tools"), "tools", "lines") + `<p class="ua-note">${t("usage.retained")}</p></section>` +
        `<section class="ua-section"><div class="ua-section-head"><div><h3>${t("usage.messages")}</h3><strong class="ua-total">${count(usage.messages_by[messageBy].total)}</strong></div>` +
        `<select data-control="messages" aria-label="${t("usage.messageGrouping")}">${options(["model", "source"], messageBy)}</select></div>` +
        chart(usage.days, usage.messages_by[messageBy], t("usage.messages"), messageBy, "lines") + `</section>`;
      const values = usage.dictation[dictationBy], total = values.reduce((a, b) => a + b, 0);
      const dictation = { total, series: [{ key: dictationBy, label: t(choiceKeys[dictationBy]), values, total, share: 1 }] };
      root.innerHTML += `<section class="ua-section"><div class="ua-section-head"><div><h3>${t("usage.dictation")}</h3><strong class="ua-total">${count(total)}</strong></div>` +
        `<select data-control="dictation" aria-label="${t("usage.dictation")}">${options(["dictations", "words"], dictationBy)}</select></div>` +
        chart(usage.days, dictation, t("usage.dictation"), "dictation", "bars") + `<p class="ua-note">${t("usage.captureDays")}</p>` +
        (usage.dictation.estimated.some(Boolean) ? `<p class="ua-note ua-estimates">${t("usage.estimates", { dictations: count(usage.dictation.estimated_dictations), words: count(usage.dictation.estimated_words) })}</p>` : "") + `</section>`;
    }
    root.innerHTML += `<div id="${tooltipId}" role="tooltip" class="ua-tooltip" hidden></div>`;
    if (focused) root.querySelector<HTMLElement>(`[data-control="${focused}"]`)?.focus();
  }

  async function refresh(): Promise<void> {
    if (disposed) return;
    const current = ++revision;
    loading = true;
    failed = false;
    render();
    try {
      const result = await api.usageAnalytics({ range, ...(endDay ? { end_day: endDay } : {}), ...(tz ? { tz } : {}) });
      if (disposed || current !== revision) return;
      data = result;
    } catch {
      if (disposed || current !== revision) return;
      failed = true;
    }
    loading = false;
    render();
  }

  function hideTooltip(): void {
    const tooltip = root.querySelector<HTMLElement>(".ua-tooltip");
    if (tooltip) tooltip.hidden = true;
    root.querySelectorAll("[aria-describedby]").forEach(mark => mark.removeAttribute("aria-describedby"));
  }
  function showTooltip(event: Event): void {
    const mark = (event.target as Element).closest<SVGElement>("[data-tooltip]");
    if (!mark) return;
    hideTooltip();
    const tooltip = root.querySelector<HTMLElement>(".ua-tooltip")!;
    tooltip.textContent = mark.dataset.tooltip!;
    tooltip.hidden = false;
    mark.setAttribute("aria-describedby", tooltipId);
    const box = mark.getBoundingClientRect();
    tooltip.style.left = `${Math.max(8, Math.min(box.left, window.innerWidth - tooltip.offsetWidth - 8))}px`;
    tooltip.style.top = `${Math.max(8, Math.min(box.bottom + 8, window.innerHeight - tooltip.offsetHeight - 8))}px`;
  }
  function keydown(event: KeyboardEvent): void { if (event.key === "Escape") hideTooltip(); }
  function clicked(event: MouseEvent): void {
    const button = (event.target as Element).closest<HTMLButtonElement>("button");
    if (!button || button.disabled) return;
    const control = button.dataset.control;
    if (button.dataset.range) {
      const next = button.dataset.range as UsageRequest["range"];
      if (next !== range) { range = next; void refresh(); }
    } else if (control === "retry") void refresh();
    else if ((control === "previous" || control === "next") && data) {
      const day = new Date(`${data.end_day}T12:00:00Z`);
      day.setUTCDate(day.getUTCDate() + (control === "previous" ? -1 : 1) * (range === "7d" ? 7 : 30));
      endDay = day.toISOString().slice(0, 10);
      if (endDay > data.today_day) endDay = data.today_day;
      void refresh();
    } else if (control === "more") { more = !more; render(); }
    else if (button.dataset.member !== undefined) {
      const id = Number(button.dataset.member);
      if (expanded.has(id)) expanded.delete(id); else expanded.add(id);
      render();
    }
  }
  function changed(event: Event): void {
    const select = event.target as HTMLSelectElement;
    if (select.dataset.control === "cost") costBy = select.value as typeof costBy;
    else if (select.dataset.control === "messages") messageBy = select.value as typeof messageBy;
    else if (select.dataset.control === "dictation") dictationBy = select.value as typeof dictationBy;
    else return;
    render();
  }
  root.addEventListener("focusin", showTooltip);
  root.addEventListener("pointerover", showTooltip);
  root.addEventListener("focusout", hideTooltip);
  root.addEventListener("pointerout", hideTooltip);
  root.addEventListener("keydown", keydown);
  root.addEventListener("click", clicked);
  root.addEventListener("change", changed);
  window.addEventListener("scroll", hideTooltip, true);
  window.addEventListener("resize", hideTooltip);
  const stopLocale = onLocaleChange(render);
  const ready = refresh();
  const controller: UsageAnalyticsMount = {
    ready, refresh,
    destroy() {
      disposed = true;
      ++revision;
      stopLocale();
      root.removeEventListener("focusin", showTooltip);
      root.removeEventListener("pointerover", showTooltip);
      root.removeEventListener("focusout", hideTooltip);
      root.removeEventListener("pointerout", hideTooltip);
      root.removeEventListener("keydown", keydown);
      root.removeEventListener("click", clicked);
      root.removeEventListener("change", changed);
      window.removeEventListener("scroll", hideTooltip, true);
      window.removeEventListener("resize", hideTooltip);
      root.remove();
      if (mounted.get(host) === controller) mounted.delete(host);
    },
  };
  mounted.set(host, controller);
  return controller;
}
