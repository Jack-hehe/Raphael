/** Page factories registered once by the main window before navigation is created. */
import type { AppContext, Page } from "./types";

type Area = "automations" | "team";
const factories: Partial<Record<Area, (ctx: AppContext) => Page>> = {};
export function registerNavigationPages(pages: Record<Area, (ctx: AppContext) => Page>): void { Object.assign(factories, pages); }
export function createNavigationPage(id: Area, ctx: AppContext): Page {
  const factory = factories[id];
  if (!factory) throw new Error(`Navigation page is not registered: ${id}`);
  return factory(ctx);
}
