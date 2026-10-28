/// <reference types="node" />
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, test, vi } from "vitest";
import { ownsSettingsKey, repaintKeepingFocus } from "./settings";

// The tests run without a page: the panel, the controls and the body are stand-ins.
const node = (): Node => ({}) as Node;
const holder = (...inside: Node[]): Node => ({ contains: (other: Node | null) => other !== null && inside.includes(other) }) as Node;

describe("keys while the settings are open", () => {
  const row = node();
  const minimize = node();
  const pageUnder = node();
  const body = node();
  const owners = [holder(row), holder(minimize)];

  test("a key from the panel or the window's controls is the settings'", () => {
    expect(ownsSettingsKey(row, owners, [body])).toBe(true);
    expect(ownsSettingsKey(minimize, owners, [body])).toBe(true);
  });

  test("a key from the body is the settings' too: the focus fell there when what had it was drawn away", () => {
    expect(ownsSettingsKey(body, owners, [body])).toBe(true);
  });

  test("a key from anywhere else is not", () => {
    expect(ownsSettingsKey(pageUnder, owners, [body])).toBe(false);
    expect(ownsSettingsKey(null, owners, [body])).toBe(false);
    expect(ownsSettingsKey(pageUnder, [null, undefined], [body])).toBe(false);
  });
});

describe("the tab list drawn again", () => {
  /** A list of tab buttons whose drawing replaces every button, as `innerHTML` does. */
  function tabList(panes: string[]) {
    let buttons: { pane: string; el: HTMLElement; focus: ReturnType<typeof vi.fn> }[] = [];
    const draw = (): void => {
      buttons = panes.map((pane) => {
        const focus = vi.fn();
        return { pane, focus, el: { dataset: { pane }, focus } as unknown as HTMLElement };
      });
    };
    draw();
    const list = {
      contains: (other: Node | null) => buttons.some((b) => b.el === (other as unknown)),
      querySelector: (selector: string) => buttons.find((b) => selector === `[data-pane="${b.pane}"]`)?.el ?? null,
    } as unknown as HTMLElement;
    return { list, draw, button: (pane: string) => buttons.find((b) => b.pane === pane)! };
  }

  test("the tab that had the focus has it again, so Enter on a tab leaves the keyboard where it was", () => {
    const tabs = tabList(["general", "data"]);
    const before = tabs.button("data");
    repaintKeepingFocus(tabs.list, before.el, tabs.draw);
    expect(tabs.button("data")).not.toBe(before); // drawn anew
    expect(tabs.button("data").focus).toHaveBeenCalledOnce();
    expect(tabs.button("general").focus).not.toHaveBeenCalled();
  });

  test("a list that did not have the focus does not take it", () => {
    const tabs = tabList(["general", "data"]);
    const draw = vi.fn(tabs.draw);
    repaintKeepingFocus(tabs.list, { dataset: { pane: "data" } } as unknown as HTMLElement, draw);
    repaintKeepingFocus(tabs.list, null, draw);
    expect(draw).toHaveBeenCalledTimes(2);
    expect(tabs.button("data").focus).not.toHaveBeenCalled();
  });

  test("the settings repaint their list this way and listen for keys that fell to the body", () => {
    // what the panel itself does, read from its source: it cannot be run without a page
    const settings = readFileSync(path.resolve(import.meta.dirname, "settings.ts"), "utf8");
    const paint = /function paintNav\(\): void \{([\s\S]*?)\n  \}/.exec(settings)![1];
    expect(paint).toContain("repaintKeepingFocus(navList");
    expect(settings).toMatch(/ownsSettingsKey\(e\.target, \[el, chrome\], \[document\.body, document\.documentElement\]\)/);
  });
});
