/** One dictation destination, preserving the existing history and dictionary pages. */
import { onLocaleChange, t } from "../i18n";
import { createProblemNotice } from "../problem-notice";
import type { AppContext, DictationTab, Page } from "../types";
import { esc, tabs } from "../ui";
import "../dictation-page.css";

const TAB_IDS: DictationTab[] = ["overview", "history", "dictionary"];
const STORAGE_KEY = "raphael.dictation.tab";
export function createDictation(app: AppContext, pages: Record<DictationTab, Page>, chooseTab: (tab: DictationTab) => void = tab => setTab(tab)) {
  const el = document.createElement("div"); el.className = "page page-dictation"; el.dataset.page = "dictation"; el.hidden = true;
  const head = document.createElement("div");
  const notice = createProblemNotice(app);
  el.append(head, notice.el);
  let selected: DictationTab = "overview", visible = false;
  try { const saved = localStorage.getItem(STORAGE_KEY); if (TAB_IDS.includes(saved as DictationTab)) selected = saved as DictationTab; } catch { /* Storage can be unavailable. */ }
  function paint(): void {
    head.innerHTML = `<div class="page-head"><h1>${esc(t("nav.dictation"))}</h1></div>` +
      tabs(TAB_IDS.map(id => ({ id: `dictation-${id}`, label: t(id === "overview" ? "dictation.overview" : id === "history" ? "main.002" : "main.003"), attrs: ` data-dictation-tab="${id}"` })), `dictation-${selected}`, { label: t("nav.dictation"), kind: "nav" });
    for (const id of TAB_IDS) {
      pages[id].el.hidden = id !== selected;
      const button = head.querySelector<HTMLElement>(`[data-dictation-tab="${id}"]`)!;
      button.id = `dictation-tab-${id}`;
      button.setAttribute("aria-controls", `dictation-panel-${id}`);
      button.tabIndex = id === selected ? 0 : -1;
    }
  }
  function setTab(tab: DictationTab, activate = true): void {
    if (tab !== selected && visible) pages[selected].hide();
    const changed = tab !== selected;
    selected = tab;
    try { localStorage.setItem(STORAGE_KEY, tab); } catch { /* The in-memory selection remains usable. */ }
    paint();
    if (changed && visible && activate) void pages[selected].show();
  }
  for (const id of TAB_IDS) {
    const panel = pages[id].el;
    panel.id = `dictation-panel-${id}`; panel.setAttribute("role", "tabpanel"); panel.setAttribute("aria-labelledby", `dictation-tab-${id}`);
    el.append(panel);
  }
  head.addEventListener("click", event => {
    const tab = (event.target as Element).closest<HTMLElement>("[data-dictation-tab]")?.dataset.dictationTab as DictationTab | undefined;
    if (tab) chooseTab(tab);
  });
  head.addEventListener("keydown", event => {
    const tab = (event.target as Element).closest<HTMLElement>("[data-dictation-tab]");
    if (!tab || !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const at = TAB_IDS.indexOf(selected);
    const next = event.key === "Home" ? 0 : event.key === "End" ? 2 : (at + (event.key === "ArrowRight" ? 1 : 2)) % 3;
    head.querySelector<HTMLButtonElement>(`[data-dictation-tab="${TAB_IDS[next]}"]`)!.click();
    head.querySelector<HTMLElement>(`[data-dictation-tab="${TAB_IDS[next]}"]`)?.focus();
  });
  onLocaleChange(paint); paint();
  return {
    id: "dictation" as const, el, tab: () => selected, setTab,
    show() { visible = true; paint(); notice.show(); void pages[selected].show(); },
    hide() { visible = false; pages[selected].hide(); notice.hide(); },
  };
}
