// @vitest-environment happy-dom
import { expect, test } from "vitest";
import { createNavigationPage, registerNavigationPages } from "./navigation-pages";
import type { AppContext, Page } from "./types";

test("the rail has stable destinations before and after the other worktree registers its pages", () => {
  const ctx = {} as AppContext;
  const fallback = createNavigationPage("team", ctx);
  fallback.show();
  expect(fallback.id).toBe("team");
  expect(fallback.el.textContent).toContain("團隊");
  const page = { id: "team", el: document.createElement("section"), show() {}, hide() {} } as Page;
  registerNavigationPages({ team: received => { expect(received).toBe(ctx); return page; } });
  expect(createNavigationPage("team", ctx)).toBe(page);
  expect(createNavigationPage("automations", ctx).id).toBe("automations");
});
