// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { openDialog, type Dialog } from "./member-dialogs";
import { closeMenu, openMenu } from "./ui";

const dialogs: Dialog[] = [];
afterEach(() => { closeMenu(); for (const dialog of dialogs.splice(0)) dialog.close(); document.body.innerHTML = ""; });
function open(canClose?: () => boolean) {
  const dialog = openDialog({ title: "Test dialog", body: '<input aria-label="Draft">', actions: {}, canClose, onAct: () => {} });
  dialogs.push(dialog); return dialog;
}
function press(start: Element, end = start, button = 0) {
  start.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, cancelable: true, button, pointerId: 1, isPrimary: true }));
  end.dispatchEvent(new PointerEvent("pointerup", { bubbles: true, button, pointerId: 1, isPrimary: true }));
  end.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, button, detail: 1 }));
}

test("a selection that starts inside and ends on the backdrop leaves the dialog open", () => {
  const dialog = open();
  press(dialog.el.querySelector("input")!, dialog.el.parentElement!);
  expect(dialog.el.isConnected).toBe(true);
});

test("an outside press closes a menu first and never also closes its dialog", () => {
  const dialog = open(), anchor = document.createElement("button"); dialog.el.append(anchor);
  openMenu(anchor, [{ value: "one", label: "One" }], "one", () => {});
  press(dialog.el.parentElement!);
  expect(document.getElementById("app-popover")).toBeNull();
  expect(dialog.el.isConnected).toBe(true);
  expect(document.activeElement).toBe(anchor);
  press(dialog.el.parentElement!);
  expect(dialog.el.isConnected).toBe(false);
});

test("a press on the page behind closes only the top dialog and restores focus", () => {
  const lower = open(), input = lower.el.querySelector<HTMLInputElement>("input")!; input.focus();
  const upper = open();
  press(document.body);
  expect(upper.el.isConnected).toBe(false); expect(lower.el.isConnected).toBe(true);
  expect(document.activeElement).toBe(input);
});

test("inside presses, outside-to-inside drags, and secondary buttons leave the dialog open", () => {
  const dialog = open();
  press(dialog.el); press(document.body, dialog.el); press(document.body, document.body, 2);
  expect(dialog.el.isConnected).toBe(true);
  press(document.body); expect(dialog.el.isConnected).toBe(false);
});

test("a busy dialog that ignores Escape also ignores outside presses until it is ready", () => {
  let busy = true; const dialog = open(() => !busy);
  document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  press(document.body); expect(dialog.el.isConnected).toBe(true);
  busy = false; press(document.body); expect(dialog.el.isConnected).toBe(false);
});

test("a cancelled pointer gesture never dismisses the dialog", () => {
  const dialog = open();
  document.body.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, button: 0, pointerId: 1, isPrimary: true }));
  document.body.dispatchEvent(new PointerEvent("pointercancel", { bubbles: true, pointerId: 1 }));
  document.body.dispatchEvent(new MouseEvent("click", { bubbles: true, detail: 1 }));
  expect(dialog.el.isConnected).toBe(true);
});

test("releasing an outside press dismisses immediately and consumes the following background click", () => {
  const background = document.createElement("button"), action = vi.fn();
  document.body.append(background); background.addEventListener("click", action);
  const dialog = open();
  background.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, cancelable: true, button: 0, pointerId: 1, isPrimary: true }));
  background.dispatchEvent(new PointerEvent("pointerup", { bubbles: true, button: 0, pointerId: 1, isPrimary: true }));
  expect(dialog.el.isConnected).toBe(false);
  background.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, detail: 1 }));
  expect(action).not.toHaveBeenCalled();
});
