// @vitest-environment happy-dom
import { afterEach, describe, expect, test } from "vitest";
import { avatarHTML, setAvatar } from "./avatar";
import { labelNavItem, navItemHTML } from "./nav-items";

afterEach(() => setAvatar(null));

describe("the sidebar's pages", () => {
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
