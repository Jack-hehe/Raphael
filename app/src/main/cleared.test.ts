/// <reference types="node" />
import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, test, vi } from "vitest";
import { Cleared } from "./cleared";

const source = (file: string): string => readFileSync(path.resolve(import.meta.dirname, file), "utf8");

describe("a clear of 資料 is heard by the pages that show it", () => {
  test("a page hears the clear of what it shows, and only that, until it stops hearing", () => {
    const cleared = new Cleared();
    const history = vi.fn();
    const conversation = vi.fn();
    const stopHistory = cleared.on("dictation", history);
    cleared.on("conversation", conversation);

    cleared.say("dictation");
    expect(history).toHaveBeenCalledOnce();
    expect(conversation).not.toHaveBeenCalled();

    cleared.say("conversation");
    expect(conversation).toHaveBeenCalledOnce();

    stopHistory();
    cleared.say("dictation");
    expect(history).toHaveBeenCalledOnce();
  });

  test("a page that fails to forget does not keep the others from forgetting", () => {
    const cleared = new Cleared();
    const after = vi.fn();
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    cleared.on("dictation", () => {
      throw new Error("broken page");
    });
    cleared.on("dictation", after);
    cleared.say("dictation");
    expect(after).toHaveBeenCalledOnce();
    warn.mockRestore();
  });

  test("the settings say so once the core has emptied it, never before", () => {
    const settings = source("pages/settings.ts");
    const commit = /commit: async \(\) => \{([\s\S]*?)\n      \},/.exec(settings.slice(settings.indexOf("function clear(what")))![1];
    const core = commit.search(/api\.historyClear\(\)|api\.agentHistoryClear\(\)/);
    const said = commit.indexOf("app.cleared.say(what)");
    expect(core).toBeGreaterThan(-1);
    expect(said).toBeGreaterThan(core);
  });

  test("the history, the 拉斐爾 page and home listen, and the window hands every page the same signal", () => {
    expect(source("pages/history.ts")).toContain('app.cleared.on("dictation"');
    expect(source("pages/agent.ts")).toContain('app.cleared.on("conversation"');
    const home = source("pages/home.ts");
    expect(home).toContain('app.cleared.on("dictation"');
    expect(home).toContain('app.cleared.on("conversation"');
    expect(source("main.ts")).toContain("cleared: new Cleared()");
  });

  test("the 拉斐爾 page forgets its turns the same way for its own 清除全部 and for the settings' clear", () => {
    const agent = source("pages/agent.ts");
    const listener = /app\.cleared\.on\("conversation", \(\) => \{([\s\S]*?)\n  \}\);/.exec(agent)![1];
    expect(listener).toContain("forgetTurns()");
    expect(listener).toContain("loads++"); // a load that started before the clear brings nothing back
    const own = agent.slice(agent.indexOf("function clearAll"));
    expect(/await api\.agentHistoryClear\(\);\s*forgetTurns\(\);/.test(own)).toBe(true);
  });
});
