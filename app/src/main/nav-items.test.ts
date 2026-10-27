// @vitest-environment happy-dom
import { afterEach, describe, expect, test } from "vitest";
import { avatarHTML, setAvatar } from "./avatar";
import { labelNavItem, navAppsHTML, navItemHTML, navHomeHTML, renderAppsEntry } from "./nav-items";
import { connectionsHTML, emptyConnectionsView } from "./connections";
import { setLocale } from "./i18n";
import type { AppsInventory } from "./types";

afterEach(() => { setAvatar(null); setLocale("zh-TW"); });

describe("the sidebar's pages", () => {
  test("the emblem returns to the home page with a localized accessible name", () => {
    for (const [locale, label] of [["zh-TW", "回到首頁"], ["en", "Back to home"], ["ja", "ホームへ"]] as const) {
      setLocale(locale);
      const nav = document.createElement("nav");
      nav.innerHTML = navHomeHTML() + navItemHTML("agent", "Raphael", "");
      const emblem = nav.querySelector<HTMLButtonElement>(".brand")!;
      expect(emblem.tagName).toBe("BUTTON");
      expect(emblem.type).toBe("button");
      expect(emblem.getAttribute("aria-label")).toBe(label);
      expect(emblem.dataset.go).toBe("home");
      expect(emblem.hasAttribute("data-window-drag")).toBe(false);
      expect(emblem.querySelector("img")).not.toBeNull();
    }
  });
  test("a new language names 拉斐爾 again and leaves his face where it is, the person's picture too", () => {
    const nav = document.createElement("nav");
    nav.innerHTML = navItemHTML("agent", "拉斐爾", avatarHTML({ size: 18, cls: "nav-face" })) + navItemHTML("home", "首頁", "<svg></svg>");
    // the picture is loaded: it takes the emblem's place
    setAvatar({ url: "data:image/png;base64,iVBORw0KGgo=", crop: { x: 0.5, y: 0.5, zoom: 1 } });
    const item = nav.querySelector<HTMLElement>('[data-go="agent"]')!;
    item.querySelector(".av")!.replaceWith(document.createRange().createContextualFragment(avatarHTML({ size: 18, cls: "nav-face" })));
    labelNavItem(nav, "agent", "Raphael");
    labelNavItem(nav, "home", "Home");
    expect(item.querySelector(".av.av-custom img")).not.toBeNull();
    expect(item.querySelector(".av")!.textContent).toBe("");
    expect(item.textContent).toBe("Raphael");
    expect(nav.querySelector('[data-go="home"]')!.textContent).toBe("Home");
  });
});

describe("the sidebar's 連結應用程式", () => {
  const sidebar = (): HTMLElement => {
    const nav = document.createElement("nav");
    nav.innerHTML = navItemHTML("agent", "拉斐爾", "") + `<div class="side-foot">${navAppsHTML()}${navItemHTML("settings", "設定", "<svg></svg>")}</div>`;
    return nav;
  };
  const apps = (count: number | null, state: AppsInventory["state"] = "complete"): AppsInventory => ({ state, count, apps: [] });

  test("is a button at the bottom that opens the settings at the connections: no page of its own", () => {
    const entry = sidebar().querySelector<HTMLButtonElement>(".nav-apps")!;
    expect(entry.tagName).toBe("BUTTON");
    expect(entry.type).toBe("button");
    expect([entry.dataset.go, entry.dataset.pane]).toEqual(["settings", "connections"]);
    expect(entry.querySelector("svg.i")).not.toBeNull(); // the plug of the connections tab
  });

  test("names itself and shows how many apps are connected; naming the settings does not rename it", () => {
    const nav = sidebar();
    renderAppsEntry(nav, apps(3));
    labelNavItem(nav, "settings", "Settings");
    const entry = nav.querySelector<HTMLElement>(".nav-apps")!;
    expect(entry.querySelector(".nav-t")!.textContent).toBe("連結應用程式");
    expect(entry.querySelector<HTMLElement>(".nav-count")!.textContent).toBe("3");
    expect(entry.querySelector<HTMLElement>(".nav-count")!.hidden).toBe(false);
    expect(entry.getAttribute("aria-label")).toBe("連結應用程式，已連結 3 個");
    expect(entry.dataset.navTitle).toBe("連結應用程式，已連結 3 個"); // the rail's tooltip
    expect(nav.querySelector('.nav[data-go="settings"]:not([data-pane]) .nav-t')!.textContent).toBe("Settings");
    // none connected is a number too
    renderAppsEntry(nav, apps(0));
    expect(entry.querySelector(".nav-count")!.textContent).toBe("0");
  });

  test("shows no number while it is read, and says it cannot be read instead of a number", () => {
    const nav = sidebar();
    const entry = nav.querySelector<HTMLElement>(".nav-apps")!;
    const badge = entry.querySelector<HTMLElement>(".nav-count")!;
    renderAppsEntry(nav, "loading");
    expect([badge.hidden, badge.textContent, badge.hasAttribute("data-unknown")]).toEqual([true, "", false]);
    for (const inventory of ["failed", apps(null, "partial"), apps(null, "unknown")] as const) {
      renderAppsEntry(nav, inventory);
      expect([badge.hidden, badge.textContent, badge.hasAttribute("data-unknown")]).toEqual([false, "—", true]);
      expect(entry.getAttribute("aria-label")).toBe("連結應用程式，無法讀取已連結的數量");
    }
    renderAppsEntry(nav, apps(2));
    expect(badge.hasAttribute("data-unknown")).toBe(false);
  });

  test("is named in the language of the window", () => {
    for (const [locale, name] of [["en", "Connect apps"], ["ja", "アプリを連携"]] as const) {
      setLocale(locale);
      const nav = sidebar();
      renderAppsEntry(nav, apps(1));
      expect(nav.querySelector(".nav-apps .nav-t")!.textContent).toBe(name);
      expect(nav.querySelector(".nav-apps")!.getAttribute("aria-label")).toContain(name);
    }
  });

  test("shows the number the connections tab shows beside 已連結: one count, from one inventory", () => {
    const inventory: AppsInventory = { state: "complete", count: 4, apps: [] };
    const nav = sidebar();
    renderAppsEntry(nav, inventory);
    const tab = connectionsHTML({ ...emptyConnectionsView(), groups: [], catalog: [], inventory, skills: [] });
    expect(nav.querySelector(".nav-count")!.textContent).toBe("4");
    expect(tab).toMatch(/data-id="installed"[^>]*>已連結<span class="n">4<\/span>/);
    // and when it cannot be known, both say so in the same way
    renderAppsEntry(nav, "failed");
    expect(nav.querySelector(".nav-count")!.textContent).toBe("—");
    expect(connectionsHTML({ ...emptyConnectionsView(), groups: [], catalog: [], inventory: "failed", skills: [] })).toMatch(/data-id="installed"[^>]*>已連結<span class="n">—<\/span>/);
  });
});
