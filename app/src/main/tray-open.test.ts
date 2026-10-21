import { describe, expect, test } from "vitest";
import { followTray, type TrayTarget } from "./tray-open";
import type { MainEvent } from "./types";

/** A window that notes what it was asked to do, in order. */
function fakeWindow(inGuide = false): TrayTarget & { done: string[] } {
  const done: string[] = [];
  return {
    done,
    inGuide: () => inGuide,
    go: (page) => void done.push(`go ${page}`),
    addWord: () => void done.push("add word"),
  };
}

const open = (page: "home" | "history" | "dictionary" | "settings", action?: "add-word"): MainEvent => ({ type: "TrayOpen", page, action });

describe("the tray's choice of a page", () => {
  test.each(["home", "history", "dictionary", "settings"] as const)("opens the %s page and nothing more", (page) => {
    const target = fakeWindow();
    followTray(open(page), target);
    expect(target.done).toEqual([`go ${page}`]);
  });

  test("adding a word goes to the dictionary first, then puts the cursor in the word", () => {
    const target = fakeWindow();
    followTray(open("dictionary", "add-word"), target);
    expect(target.done).toEqual(["go dictionary", "add word"]);
  });

  test("an action that belongs to the dictionary does nothing on another page", () => {
    const target = fakeWindow();
    followTray(open("history", "add-word"), target);
    expect(target.done).toEqual(["go history"]);
  });

  test("the first-run guide stays in front, even when the tray asks for the dictionary", () => {
    const target = fakeWindow(true);
    followTray(open("dictionary", "add-word"), target);
    followTray(open("settings"), target);
    expect(target.done).toEqual([]);
  });

  test("events that are not the tray's are left alone", () => {
    const target = fakeWindow();
    followTray({ type: "Level", value: 0.5 }, target);
    followTray({ type: "LearnLimited", app: "vscode", reason: "accessibility_off" }, target);
    expect(target.done).toEqual([]);
  });

  test("the event is the shape Rust sends", () => {
    // commands.rs: {"type":"TrayOpen","page":"dictionary","action":"add-word"}, no action otherwise
    const sent = JSON.parse('{"type":"TrayOpen","page":"dictionary","action":"add-word"}') as MainEvent;
    const target = fakeWindow();
    followTray(sent, target);
    expect(target.done).toEqual(["go dictionary", "add word"]);
    const plain = JSON.parse('{"type":"TrayOpen","page":"history"}') as MainEvent;
    followTray(plain, target);
    expect(target.done).toEqual(["go dictionary", "add word", "go history"]);
  });
});
