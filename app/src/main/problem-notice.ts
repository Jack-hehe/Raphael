/** Actionable dictation status, shared by the dictation page and conversation composer. */
import * as api from "./api";
import { onLocaleChange } from "./i18n";
import { buildReport, coreDownReport, type Report } from "./report";
import type { AppContext, Status } from "./types";
import { btn, esc, ico } from "./ui";
import "./problem-notice.css";

export function createProblemNotice(app: AppContext, changed: () => void = () => {}) {
  const el = document.createElement("div");
  el.className = "problem-notice";
  el.setAttribute("role", "status");
  el.hidden = true;
  let visible = false, generation = 0;
  let timer: ReturnType<typeof setInterval> | undefined;
  let recheck: ReturnType<typeof setTimeout> | undefined;
  let status: Status | null | undefined;
  let report: Report | undefined;
  function paint(): void {
    report = status === undefined ? undefined : status === null ? coreDownReport() : buildReport(status, Date.now());
    el.hidden = !report?.action;
    el.innerHTML = report?.action ? `${ico("info")}<span>${esc(report.text)}</span>${btn(report.action.label, "sm", ' data-problem-action')}` : "";
    changed();
  }
  async function check(again = true): Promise<void> {
    const mine = generation;
    try {
      const checked = await api.statusCheckOpenrouter();
      if (!visible || mine !== generation) return;
      await load(false);
      if (again && checked.state === "error" && (checked.error === "timeout" || checked.error === "network")) {
        clearTimeout(recheck);
        recheck = setTimeout(() => { if (visible) void check(false); }, 6000);
      }
    } catch { if (visible && mine === generation) void load(false); }
  }
  async function load(checkUnknown = true): Promise<void> {
    const mine = ++generation;
    let next: Status | null;
    try { next = await api.statusGet(); } catch { next = null; }
    if (!visible || mine !== generation) return;
    status = next;
    paint();
    if (checkUnknown && next?.openrouter.state === "unknown") void check();
  }
  el.addEventListener("click", event => {
    if (!(event.target as Element).closest("[data-problem-action]")) return;
    if (report?.action?.target === "settings") app.go("settings", "usage");
    else void check();
  });
  onLocaleChange(paint);
  app.onEvent(event => { if (visible && event.type === "SettingsChanged") void load(); });
  return {
    el,
    show() {
      visible = true;
      void load();
      clearInterval(timer);
      timer = setInterval(() => { if (!document.hidden) void load(); }, 30_000);
    },
    hide() { visible = false; generation++; clearInterval(timer); clearTimeout(recheck); },
  };
}
