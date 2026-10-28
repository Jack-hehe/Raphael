import { afterEach, describe, expect, test } from "vitest";
import { appOf, connectedCount, countText, countUnknown, entryLabel, isConnected, whyNot } from "./apps-inventory";
import { setLocale } from "./i18n";
import type { AppsInventory, InventoryApp } from "./types";

afterEach(() => setLocale("zh-TW"));

const app = (id: string, over: Partial<InventoryApp> = {}): InventoryApp => ({ id, name: id, enabled: true, state: "ready", tools: 2, connected: true, ...over });
const whole: AppsInventory = { state: "complete", count: 3, apps: [app("a"), app("b"), app("c"), app("w", { state: "needs_sign_in", connected: false, tools: 0 })] };
const partial: AppsInventory = { state: "partial", count: null, apps: [app("a"), app("x", { state: "unknown", connected: false, tools: 0 })] };
const unknown: AppsInventory = { state: "unknown", count: null, apps: [] };

describe("the owner's count of connected apps", () => {
  test("is the number the core gave, and none while it is read", () => {
    expect(connectedCount(whole)).toBe(3);
    expect(countText(whole)).toBe("3");
    expect(connectedCount("loading")).toBeNull();
    expect(countText("loading")).toBe("");
    expect(countUnknown("loading")).toBe(false); // not yet is not never
    expect(countText({ ...whole, count: 0, apps: [] })).toBe("0"); // none connected is a number
  });

  test("is never a number over an inventory that is not whole: it cannot be read, it was read in part, or the core did not answer", () => {
    for (const inventory of [partial, unknown, "failed"] as const) {
      expect(connectedCount(inventory)).toBeNull();
      expect(countUnknown(inventory)).toBe(true);
      expect(countText(inventory)).toBe("—");
    }
    // a count the core gave with a state that is not complete is not believed
    expect(connectedCount({ state: "partial", count: 2, apps: [] })).toBeNull();
  });

  test("names the sidebar's entry with how many are connected, or that it cannot be read, in each language", () => {
    expect(entryLabel(whole)).toBe("連結應用程式，已連結 3 個");
    expect(entryLabel(partial)).toBe("連結應用程式，無法讀取已連結的數量");
    expect(entryLabel("loading")).toBe("連結應用程式");
    setLocale("en");
    expect(entryLabel(whole)).toBe("Connect apps, 3 connected");
    expect(entryLabel("failed")).toBe("Connect apps, the number connected cannot be read");
    setLocale("ja");
    expect(entryLabel(whole)).toBe("アプリを連携、3 件接続済み");
  });

  test("finds an app by its id and says whether it is connected; an inventory that was not read says nothing", () => {
    expect(appOf(whole, "w")?.state).toBe("needs_sign_in");
    expect(isConnected(whole, "a")).toBe(true);
    expect(isConnected(whole, "w")).toBe(false);
    expect(isConnected(whole, "nope")).toBeNull();
    expect(isConnected("loading", "a")).toBeNull();
    expect(isConnected("failed", "a")).toBeNull();
  });

  test("gives the reason only for what a row does not say by itself", () => {
    expect(whyNot("duplicate")).toBe("conn.state.duplicate");
    expect(whyNot("no_tools")).toBe("conn.state.noTools");
    expect(whyNot("unknown")).toBe("conn.state.unknown");
    for (const state of ["ready", "disabled", "needs_sign_in", "needs_secret", "error", "unchecked"] as const) expect(whyNot(state)).toBeNull();
  });
});
