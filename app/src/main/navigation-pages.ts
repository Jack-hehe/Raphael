/** Page factories registered once by the main window before navigation is created. */
import type { AppContext, DictationTab, MainPageId, Page, PageId } from "./types";

export const START_PAGE = "home";
/** Legacy entry points keep working while the rail has one dictation destination. */
export function resolvePage(id: Exclude<PageId, "settings">): { page: MainPageId; tab?: DictationTab } {
  if (id === "history" || id === "dictionary") return { page: "dictation", tab: id };
  return { page: id };
}

type Area = "automations" | "team";
const factories: Partial<Record<Area, (ctx: AppContext) => Page>> = {};
export function registerNavigationPages(pages: Record<Area, (ctx: AppContext) => Page>): void { Object.assign(factories, pages); }
export function createNavigationPage(id: Area, ctx: AppContext): Page {
  const factory = factories[id];
  if (!factory) throw new Error(`Navigation page is not registered: ${id}`);
  return factory(ctx);
}
