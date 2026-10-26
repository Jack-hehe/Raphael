// @vitest-environment happy-dom
import { beforeEach, expect, it, vi } from "vitest";
import { createFirstBot } from "./first-bot";
import { setLocale } from "./i18n";

beforeEach(() => { document.body.replaceChildren(); setLocale("en"); });

it("preserves free answers across Back and submits only an ordinary proposal", () => {
  const propose = vi.fn();
  const card = createFirstBot(propose);
  document.body.append(card.el);
  card.sync(true);
  const click = (act: string) => card.el.querySelector<HTMLButtonElement>(`[data-first-act="${act}"]`)!.click();
  const answer = (text: string) => {
    const field = card.el.querySelector<HTMLTextAreaElement>("textarea")!;
    field.value = text; field.dispatchEvent(new Event("input", { bubbles: true }));
  };
  expect(card.el.querySelector<HTMLButtonElement>('[data-first-act="next"]')!.disabled).toBe(true);
  answer("Night Shift"); click("next");
  answer("Direct and kind"); click("back");
  expect(card.el.querySelector<HTMLTextAreaElement>("textarea")!.value).toBe("Night Shift");
  click("next");
  expect(card.el.querySelector<HTMLTextAreaElement>("textarea")!.value).toBe("Direct and kind");
  click("next"); answer("Review my notes");
  expect(propose).not.toHaveBeenCalled();
  click("next");
  expect(propose).toHaveBeenCalledOnce();
  expect(propose.mock.calls[0]![0]).toContain("settings draft");
  expect(propose.mock.calls[0]![0]).not.toContain("member_update");
  for (const text of ["Night Shift", "Direct and kind", "Review my notes"]) expect(propose.mock.calls[0]![0]).toContain(text);
  card.sync(true);
  expect(card.el.hidden).toBe(true);
});

it("cancel discards the setup without proposing or saving a profile", () => {
  const propose = vi.fn();
  const card = createFirstBot(propose);
  card.sync(true);
  card.el.querySelector<HTMLButtonElement>('[data-first-act="cancel"]')!.click();
  card.sync(true);
  expect(card.el.hidden).toBe(true);
  expect(propose).not.toHaveBeenCalled();
  const reopened = createFirstBot(propose);
  reopened.sync(true);
  expect(reopened.el.hidden).toBe(false);
});
