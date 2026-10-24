// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
vi.mock("../api", () => ({ statusGet: vi.fn(async () => ({ openrouter: { state: "ok" }, spend: { month_usd: 0, limit_usd: 10 } })) }));
import { createDictation } from "./dictation";
import { resolvePage } from "../navigation-pages";
import { samePage } from "../nav-history";
import type { AppContext, Page } from "../types";

afterEach(() => { localStorage.clear(); document.body.replaceChildren(); });
test("legacy home returns to conversation; dictation deep links select their tab", () => {
  expect(resolvePage("home")).toEqual({ page: "agent" });
  expect(resolvePage("history")).toEqual({ page: "dictation", tab: "history" });
  expect(resolvePage("dictionary")).toEqual({ page: "dictation", tab: "dictionary" });
  expect(samePage({ page: "dictation", tab: "overview" }, { page: "dictation", tab: "history" })).toBe(false);
});
test("dictation mounts the existing pages, hides inactive tabs and remembers the last tab", () => {
  const make = (id: Page["id"]): Page => ({ id, el: document.createElement("section"), show: vi.fn(), hide: vi.fn() });
  const overview = make("dictation"), history = make("history"), dictionary = make("dictionary");
  const app = { go: vi.fn(), onEvent: () => () => {} } as unknown as AppContext;
  const page = createDictation(app, { overview, history, dictionary });
  document.body.append(page.el); page.show();
  expect(page.el.querySelector('[role="tablist"]')?.getAttribute("aria-orientation")).toBe("vertical");
  expect(page.el.querySelector(".dictation-content")?.contains(history.el)).toBe(true);
  expect(overview.el.hidden).toBe(false);
  expect(history.el.hidden).toBe(true);
  page.setTab("history");
  expect(overview.hide).toHaveBeenCalled();
  expect(history.el.hidden).toBe(false);
  expect(page.el.querySelector('[role="tab"][aria-selected="true"]')?.textContent).toBe("歷史紀錄");
  expect(page.el.querySelector('[data-dictation-tab="dictionary"]')?.getAttribute("tabindex")).toBe("-1");
  const key = (value: string) => document.activeElement?.dispatchEvent(new KeyboardEvent("keydown", { key: value, bubbles: true }));
  const selected = page.el.querySelector<HTMLElement>('[data-dictation-tab="history"]')!;
  selected.focus(); selected.click();
  expect(document.activeElement?.getAttribute("data-dictation-tab")).toBe("history");
  key("ArrowDown");
  expect(page.tab()).toBe("dictionary");
  expect(document.activeElement?.getAttribute("aria-controls")).toBe(dictionary.el.id);
  key("ArrowDown");
  expect(page.tab()).toBe("overview");
  key("End");
  expect(page.tab()).toBe("dictionary");
  key("Home"); key("ArrowUp");
  expect(page.tab()).toBe("dictionary");
  page.setTab("history");
  page.hide();
  const reopened = createDictation(app, { overview, history, dictionary });
  expect(reopened.tab()).toBe("history");
  reopened.show(); reopened.hide();
});
