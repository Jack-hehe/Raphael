// @vitest-environment happy-dom
import { afterEach, describe, expect, test } from "vitest";
import { KEYS_ATTRIBUTE, installFocusKeys } from "./focus-keys";

const keyed = (): boolean => document.documentElement.hasAttribute(KEYS_ATTRIBUTE);
const press = (target: EventTarget, key: string): void => {
  target.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true }));
};
const point = (target: EventTarget): void => {
  target.dispatchEvent(new Event("pointerdown", { bubbles: true, cancelable: true }));
};

let stop: (() => void) | undefined;
afterEach(() => {
  stop?.();
  stop = undefined;
  document.body.innerHTML = "";
});

describe("the focus rings follow the keyboard only", () => {
  test("nothing is marked before any key or click", () => {
    stop = installFocusKeys(document);
    expect(keyed()).toBe(false);
  });

  test("Tab marks the window, with or without Shift", () => {
    stop = installFocusKeys(document);
    press(document.body, "Tab");
    expect(keyed()).toBe(true);
    point(document.body);
    document.body.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", shiftKey: true, bubbles: true }));
    expect(keyed()).toBe(true);
  });

  test("Alt, the dictation key, and the keys typed into a field mark nothing", () => {
    stop = installFocusKeys(document);
    for (const key of ["Alt", "AltGraph", "Control", "Shift", "Enter", " ", "a", "Escape", "ArrowDown"]) press(document.body, key);
    expect(keyed()).toBe(false);
  });

  test("a press of the pointer takes the mark away", () => {
    stop = installFocusKeys(document);
    press(document.body, "Tab");
    point(document.body);
    expect(keyed()).toBe(false);
  });

  test("a handler that stops the events further down cannot hide them", () => {
    stop = installFocusKeys(document);
    const button = document.createElement("button");
    document.body.append(button);
    button.addEventListener("keydown", (event) => event.stopPropagation());
    button.addEventListener("pointerdown", (event) => event.stopPropagation());
    press(button, "Tab");
    expect(keyed()).toBe(true);
    point(button);
    expect(keyed()).toBe(false);
  });

  test("taking it away removes the listeners and the mark", () => {
    stop = installFocusKeys(document);
    press(document.body, "Tab");
    stop();
    stop = undefined;
    expect(keyed()).toBe(false);
    press(document.body, "Tab");
    expect(keyed()).toBe(false);
  });
});
