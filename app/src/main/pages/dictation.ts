/** One dictation destination, preserving the existing history and dictionary pages. */
import { onLocaleChange, t } from "../i18n";
import { createProblemNotice } from "../problem-notice";
import type { AppContext, DictationTab, Page } from "../types";
import { esc, ico } from "../ui";
import "../dictation-page.css";

const TAB_IDS: DictationTab[] = ["overview", "history", "dictionary"];
const STORAGE_KEY = "raphael.dictation.tab";
export function createDictation(app: AppContext, pages: Record<DictationTab, Page>, chooseTab?: (tab: DictationTab) => void) {
  const el = document.createElement("div"); el.className = "page page-dictation"; el.dataset.page = "dictation"; el.hidden = true;
  const head = document.createElement("div"); head.className = "dictation-navigation";
  const content = document.createElement("div"); content.className = "dictation-content";
  const notice = createProblemNotice(app);
  content.append(notice.el); el.append(head, content);
  let selected: DictationTab = "overview", visible = false;
  try { const saved = localStorage.getItem(STORAGE_KEY); if (TAB_IDS.includes(saved as DictationTab)) selected = saved as DictationTab; } catch { /* Storage can be unavailable. */ }
  function paint(): void {
    const restoreFocus = head.contains(document.activeElement);
    head.innerHTML = `<div class="page-head"><h1>${esc(t("nav.dictation"))}</h1></div>` +
      `<div class="dictation-nav" role="tablist" aria-orientation="vertical" aria-label="${esc(t("nav.dictation"))}">` +
      TAB_IDS.map(id => `<button type="button" class="nav" role="tab" aria-selected="${id === selected}" data-dictation-tab="${id}">` +
        ico(id === "overview" ? "grid" : id === "history" ? "clock" : "book") +
        `<span>${esc(t(id === "overview" ? "dictation.overview" : id === "history" ? "main.002" : "main.003"))}</span></button>`).join("") + `</div>`;
    for (const id of TAB_IDS) {
      pages[id].el.hidden = id !== selected;
      const button = head.querySelector<HTMLElement>(`[data-dictation-tab="${id}"]`)!;
      button.id = `dictation-tab-${id}`;
      button.setAttribute("aria-controls", `dictation-panel-${id}`);
      button.tabIndex = id === selected ? 0 : -1;
    }
    if (restoreFocus) head.querySelector<HTMLElement>(`[data-dictation-tab="${selected}"]`)?.focus({ preventScroll: true });
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
    content.append(panel);
  }
  head.addEventListener("click", event => {
    const tab = (event.target as Element).closest<HTMLElement>("[data-dictation-tab]")?.dataset.dictationTab as DictationTab | undefined;
    if (tab) (chooseTab ?? setTab)(tab);
  });
  head.addEventListener("keydown", event => {
    const tab = (event.target as Element).closest<HTMLElement>("[data-dictation-tab]");
    if (!tab || !["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const at = TAB_IDS.indexOf(selected);
    const next = event.key === "Home" ? 0 : event.key === "End" ? 2 : (at + (event.key === "ArrowDown" ? 1 : 2)) % 3;
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
