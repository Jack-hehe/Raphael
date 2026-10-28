// @vitest-environment happy-dom
// 「選擇主要成員」 (primary-picker.ts): real buttons in a radio group, one chosen at a time, the primary action waiting
// for a choice, Raphael among the choices, and an answer only for a choice that is shown.
import { afterEach, describe, expect, test } from "vitest";
import { openPrimaryPicker } from "./primary-picker";
import type { Member } from "./types";

const member = (id: number, patch: Partial<Member> = {}): Member => ({
  id, name: `成員${id}`, role: `職責${id}`, instructions: "", avatar: "", group_id: null, model: "", tools: null, position: id, created: 0, unread: false, waiting: false, preview: "", last_at: null, ...patch,
});
const MEMBERS = [member(0, { name: "拉斐爾", role: "" }), member(2, { name: "小晴", role: "研究" }), member(3, { name: "小森", role: "文書" })];

const dialog = (): HTMLElement => document.querySelector<HTMLElement>(".mb-primary-picker")!;
const choice = (id: number): HTMLButtonElement => dialog().querySelector<HTMLButtonElement>(`[data-id="${id}"]`)!;
const setButton = (): HTMLButtonElement => dialog().querySelector<HTMLButtonElement>('[data-dlg="set"]')!;
const checked = (): string[] => [...dialog().querySelectorAll<HTMLElement>('[role="radio"][aria-checked="true"]')].map((el) => el.dataset.id!);

afterEach(() => { document.body.innerHTML = ""; });

describe("the primary contact's window", () => {
  test("lists the members as real buttons of one radio group, none chosen and 設為主要 waiting for one", async () => {
    const answer = openPrimaryPicker({ members: MEMBERS });
    expect(dialog().querySelector("h2")!.textContent).toBe("選擇主要成員");
    const group = dialog().querySelector('[role="radiogroup"]')!;
    expect(group.getAttribute("aria-label")).toBe("選擇主要成員");
    const radios = [...group.querySelectorAll<HTMLElement>('[role="radio"]')];
    expect(radios.map((el) => el.dataset.id)).toEqual(["0", "2", "3"]);
    expect(radios.every((el) => el.tagName === "BUTTON" && el.getAttribute("type") === "button")).toBe(true);
    expect(dialog().querySelector("select, input[type=radio], input[type=checkbox]")).toBeNull();
    expect(checked()).toEqual([]);
    expect(setButton().disabled).toBe(true);
    expect(radios[1].textContent).toContain("小晴");
    expect(radios[1].textContent).toContain("研究");
    // Raphael uses the same localized role as other member surfaces.
    expect(radios[0].textContent).toBe("拉斐爾幕僚長");
    dialog().querySelector<HTMLElement>('[data-dlg="cancel"]')!.click();
    expect(await answer).toBeNull();
  });

  test("the member whose menu opened it is chosen already, so one press of 設為主要 sets it", async () => {
    const answer = openPrimaryPicker({ members: MEMBERS, chosen: 2 });
    expect(checked()).toEqual(["2"]);
    expect(choice(2).querySelector("svg.i")).not.toBeNull();
    expect(setButton().disabled).toBe(false);
    setButton().click();
    expect(await answer).toBe(2);
    expect(document.querySelector(".mb-primary-picker")).toBeNull();
  });

  test("choosing another moves the choice, and the answer is the one chosen last", async () => {
    const answer = openPrimaryPicker({ members: MEMBERS, chosen: 2 });
    // pressed with the keyboard or the pointer, the button has the focus, and the list drawn again hands it back to it
    choice(3).focus();
    choice(3).click();
    expect(checked()).toEqual(["3"]);
    expect(choice(3).getAttribute("aria-checked")).toBe("true");
    expect(choice(2).getAttribute("aria-checked")).toBe("false");
    expect(document.activeElement).toBe(choice(3));
    setButton().click();
    expect(await answer).toBe(3);
  });

  test("Raphael can be chosen again, and his id 0 is an answer, not nothing", async () => {
    const answer = openPrimaryPicker({ members: MEMBERS });
    choice(0).click();
    setButton().click();
    expect(await answer).toBe(0);
  });

  test("a member that is not on offer cannot be the answer: a first choice that is not listed chooses nobody", async () => {
    const answer = openPrimaryPicker({ members: MEMBERS, chosen: 99 });
    expect(checked()).toEqual([]);
    expect(setButton().disabled).toBe(true);
    setButton().click();
    expect(document.querySelector(".mb-primary-picker")).not.toBeNull();
    dialog().querySelector<HTMLElement>('[data-dlg="close"]')!.click();
    expect(await answer).toBeNull();
  });

  test("取消, Escape and × leave without an answer", async () => {
    for (const leave of [
      () => dialog().querySelector<HTMLElement>('[data-dlg="cancel"]')!.click(),
      () => document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true })),
      () => dialog().querySelector<HTMLElement>('[data-dlg="close"]')!.click(),
    ]) {
      const answer = openPrimaryPicker({ members: MEMBERS, chosen: 2 });
      leave();
      expect(await answer).toBeNull();
    }
  });

  test("the search narrows the list by name or role, and a choice it hides is dropped, never answered", async () => {
    const answer = openPrimaryPicker({ members: MEMBERS, chosen: 2 });
    const input = dialog().querySelector<HTMLInputElement>("[data-primary-search]")!;
    input.value = "文書";
    input.dispatchEvent(new Event("input"));
    expect([...dialog().querySelectorAll<HTMLElement>('[role="radio"]')].map((el) => el.dataset.id)).toEqual(["3"]);
    expect(checked()).toEqual([]);
    expect(setButton().disabled).toBe(true);
    input.value = "沒有這個人";
    input.dispatchEvent(new Event("input"));
    expect(dialog().querySelector(".mb-none")!.textContent).toBe("沒有相符的成員");
    input.value = "";
    input.dispatchEvent(new Event("input"));
    expect(dialog().querySelectorAll('[role="radio"]')).toHaveLength(3);
    choice(3).click();
    setButton().click();
    expect(await answer).toBe(3);
  });

  test("Enter in the search sets the chosen member, and does nothing while none is chosen", async () => {
    const answer = openPrimaryPicker({ members: MEMBERS });
    const input = dialog().querySelector<HTMLInputElement>("[data-primary-search]")!;
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
    expect(document.querySelector(".mb-primary-picker")).not.toBeNull();
    choice(2).click();
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
    expect(await answer).toBe(2);
  });
});
