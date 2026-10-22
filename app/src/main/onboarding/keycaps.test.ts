import { describe, expect, test } from "vitest";
import { allDown, bindingParts, capsHTML, hookAfter, litKeys } from "./keycaps";

describe("keycaps", () => {
  test("a binding is the names the keys of the window have, in either spelling", () => {
    expect(bindingParts("AltRight+Space")).toEqual(["altright", "space"]);
    expect(bindingParts("AltRight+V")).toEqual(bindingParts("AltRight+KeyV"));
  });

  test("the keys the window sees down are lit", () => {
    const lit = litKeys(new Set(["altright"]), { dictation: false, agent: false }, ["altright"], ["altright", "space"]);
    expect([...lit]).toEqual(["altright"]);
  });

  test("a shortcut the app heard go down lights all of its keys, the window saw them or not", () => {
    const lit = litKeys(new Set(), { dictation: false, agent: true }, ["altright"], ["altright", "space"]);
    expect([...lit].sort()).toEqual(["altright", "space"]);
  });

  test("the events of the app move the hook state, and other events leave it", () => {
    let hook = { dictation: false, agent: false };
    hook = hookAfter(hook, "dictation_down");
    expect(hook).toEqual({ dictation: true, agent: false });
    hook = hookAfter(hook, "agent_down");
    hook = hookAfter(hook, "paste_last");
    expect(hook).toEqual({ dictation: true, agent: true });
    hook = hookAfter(hookAfter(hook, "dictation_up"), "agent_up");
    expect(hook).toEqual({ dictation: false, agent: false });
  });

  test("a combination is down only when every key is", () => {
    expect(allDown(new Set(["altright", "space"]), ["altright", "space"])).toBe(true);
    expect(allDown(new Set(["altright"]), ["altright", "space"])).toBe(false);
    expect(allDown(new Set(["altright"]), [])).toBe(false);
  });

  test("the caps are drawn with their name and one cap for each key", () => {
    const html = capsHTML("AltRight+Space", "shortcut");
    expect(html).toContain('data-cap="altright"');
    expect(html).toContain('data-cap="space"');
    expect(html.match(/<kbd/g)).toHaveLength(2);
    expect(html).toContain('aria-label="shortcut"');
  });
});
