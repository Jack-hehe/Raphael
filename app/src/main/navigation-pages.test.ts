// @vitest-environment happy-dom
import { expect, test } from "vitest";
import { createNavigationPage, registerNavigationPages } from "./navigation-pages";
import type { AppContext, Page } from "./types";

test("both rail destinations use their registered production factories", () => {
  const ctx = {} as AppContext;
  const page = (id: "team" | "automations") => ({ id, el: document.createElement("section"), show() {}, hide() {} }) as Page;
  const team = page("team"), automations = page("automations");
  registerNavigationPages({
    team: received => { expect(received).toBe(ctx); return team; },
    automations: received => { expect(received).toBe(ctx); return automations; },
  });
  expect(createNavigationPage("team", ctx)).toBe(team);
  expect(createNavigationPage("automations", ctx)).toBe(automations);
});
