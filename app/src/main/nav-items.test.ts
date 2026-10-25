// @vitest-environment happy-dom
import { afterEach, describe, expect, test } from "vitest";
import { avatarHTML, setAvatar } from "./avatar";
import { labelNavItem, navItemHTML, navHomeHTML } from "./nav-items";
import { setLocale } from "./i18n";

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
