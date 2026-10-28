// @vitest-environment happy-dom
import { afterEach, describe, expect, test, vi } from "vitest";
import { avatarHTML, cleanCrop, cropStyle, currentAvatar, loadAvatar, onAvatarChange, readAvatar, setAvatar } from "./avatar";
import { cropDialog, dragCrop } from "./avatar-settings";
import { openDialog } from "./member-dialogs";
import { pictureRect } from "../overlay/emblem";

const PNG = "data:image/png;base64,iVBORw0KGgo=";

afterEach(() => setAvatar(null));

describe("Raphael's face", () => {
  test("the emblem of the theme: both pictures, the stylesheet shows one; the larger file for a larger face", () => {
    const small = avatarHTML({ size: 26 });
    expect(small).toContain('style="--av:26px"');
    expect(small).toContain('<img class="em-l" src="/brand/app-emblem-light-64.webp"');
    expect(small).toContain('<img class="em-d" src="/brand/app-emblem-dark-64.webp"');
    expect(small).toContain('aria-hidden="true"');
    expect(avatarHTML({ size: 132 })).toContain("app-emblem-light-128.webp");
    // while he thinks or listens, and on the empty page, the turning loop
    expect(avatarHTML({ size: 26, loop: true })).toContain('src="/brand/app-emblem-dark-loop.webp"');
  });

  test("the person's picture, in a circle, cut as they chose; the same still or moving", () => {
    setAvatar({ url: PNG, crop: { x: 0.25, y: 0.5, zoom: 2 } });
    const html = avatarHTML({ size: 34, loop: true, cls: "x" });
    expect(html).toContain('class="av av-custom x"');
    expect(html).toContain(`src="${PNG}"`);
    expect(html).toContain('style="object-position:25% 50%;transform-origin:25% 50%;transform:scale(2)"');
    expect(html).not.toContain("emblem");
    // a page may ask for the emblem whatever is in force
    expect(avatarHTML({ size: 34, avatar: null })).toContain("app-emblem");
  });

  test("only a data URL of the four kinds is a picture; anything else is the emblem", () => {
    expect(readAvatar({ url: PNG, crop: { x: 0.1, y: 0.2, zoom: 1.5 } })).toEqual({ url: PNG, crop: { x: 0.1, y: 0.2, zoom: 1.5 } });
    for (const url of [
      "https://example.com/a.png",
      "javascript:alert(1)",
      "data:image/svg+xml;base64,PHN2Zz4=",
      'data:image/png;base64,AAAA" onerror="x',
      "data:text/html;base64,PGI+",
    ]) {
      expect(readAvatar({ url, crop: { x: 0.5, y: 0.5, zoom: 1 } }), url).toBeNull();
    }
    expect(readAvatar(null)).toBeNull();
    expect(readAvatar("x")).toBeNull();
  });

  test("a crop is kept in range; what is not a number is the middle, unenlarged", () => {
    expect(cleanCrop({ x: -1, y: 2, zoom: 9 })).toEqual({ x: 0, y: 1, zoom: 4 });
    expect(cleanCrop({ x: Number.NaN, zoom: 0.5 })).toEqual({ x: 0.5, y: 0.5, zoom: 1 });
    expect(cleanCrop(null)).toEqual({ x: 0.5, y: 0.5, zoom: 1 });
    expect(cropStyle({ x: 1 / 3, y: 0, zoom: 1.23456 })).toBe("object-position:33.3% 0%;transform-origin:33.3% 0%;transform:scale(1.235)");
  });

  test("every place that shows him hears the change; a picture that cannot be read is the emblem", async () => {
    const heard = vi.fn();
    const stop = onAvatarChange(heard);
    await loadAvatar(async () => ({ url: PNG, crop: { x: 0.5, y: 0.5, zoom: 1 } }));
    expect(currentAvatar()?.url).toBe(PNG);
    await loadAvatar(async () => {
      throw new Error("no app");
    });
    expect(currentAvatar()).toBeNull();
    expect(heard).toHaveBeenCalledTimes(2);
    stop();
    setAvatar(null);
    expect(heard).toHaveBeenCalledTimes(2);
  });
});

describe("the crop dialog", () => {
  test("a drag moves the picture with the pointer: the overlay's rectangle moves by as much", () => {
    const start = { x: 0.5, y: 0.5, zoom: 1 };
    // a wide picture in a 100 px circle: it can move sideways only
    const moved = dragCrop(start, 30, 40, 200, 100, 100);
    expect(moved.y).toBe(0.5);
    const before = pictureRect(200, 100, 100, start);
    const after = pictureRect(200, 100, 100, moved);
    expect(after.x - before.x).toBeCloseTo(30);
    // enlarged, it moves both ways, and as far as the pointer
    const zoomed = { x: 0.5, y: 0.5, zoom: 2 };
    const both = dragCrop(zoomed, -20, 10, 100, 100, 100);
    expect(pictureRect(100, 100, 100, both).x - pictureRect(100, 100, 100, zoomed).x).toBeCloseTo(-20);
    expect(pictureRect(100, 100, 100, both).y - pictureRect(100, 100, 100, zoomed).y).toBeCloseTo(10);
  });

  test("the picture never leaves the circle uncovered, and one that fits exactly does not move", () => {
    expect(dragCrop({ x: 0.5, y: 0.5, zoom: 1 }, 50, 50, 100, 100, 100)).toEqual({ x: 0.5, y: 0.5, zoom: 1 });
    expect(dragCrop({ x: 0.5, y: 0.5, zoom: 1 }, 5000, 0, 300, 100, 100).x).toBe(0);
    expect(dragCrop({ x: 0.5, y: 0.5, zoom: 1 }, 1, 1, 0, 0, 100)).toEqual({ x: 0.5, y: 0.5, zoom: 1 });
    for (const crop of [{ x: 0, y: 0, zoom: 1 }, { x: 1, y: 1, zoom: 3 }, { x: 0.2, y: 0.9, zoom: 1.7 }]) {
      const r = pictureRect(160, 90, 100, crop);
      expect(r.x).toBeLessThanOrEqual(1e-9);
      expect(r.y).toBeLessThanOrEqual(1e-9);
      expect(r.x + r.w).toBeGreaterThanOrEqual(100 - 1e-9);
      expect(r.y + r.h).toBeGreaterThanOrEqual(100 - 1e-9);
    }
  });

  test("traps Tab focus inside the crop dialog and Escape closes only the crop dialog when stacked over another dialog", async () => {
    document.body.innerHTML = "";
    const under = openDialog({ title: "Under", body: '<input id="u">', actions: {}, onAct: () => {} });
    const done = cropDialog(PNG);
    const zoom = document.querySelector<HTMLInputElement>(".av-crop-zoom")!;
    const doneBtn = document.querySelector<HTMLButtonElement>('[data-act="crop-done"]')!;
    expect(document.activeElement).toBe(doneBtn);
    doneBtn.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", bubbles: true }));
    expect(document.activeElement).toBe(zoom);
    zoom.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", shiftKey: true, bubbles: true }));
    expect(document.activeElement).toBe(doneBtn);

    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    expect(await done).toBeNull();
    expect(under.el.isConnected).toBe(true);
    under.close();
  });

  test("crop drags ending outside keep the crop open; an outside press cancels only the crop", async () => {
    document.body.innerHTML = "";
    const under = openDialog({ title: "Under", body: '<input id="u">', actions: {}, onAct: () => {} });
    const done = cropDialog(PNG);
    const press = (start: Element, end = start) => {
      start.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, cancelable: true, button: 0, pointerId: 1, isPrimary: true }));
      end.dispatchEvent(new PointerEvent("pointerup", { bubbles: true, button: 0, pointerId: 1, isPrimary: true }));
      end.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, detail: 1 }));
    };
    press(document.querySelector(".av-crop h2")!, document.body);
    expect(document.querySelector(".av-crop")).not.toBeNull();
    press(document.body); expect(await done).toBeNull();
    expect(under.el.isConnected).toBe(true); expect(document.activeElement).toBe(under.el.querySelector("input"));
    under.close();
  });
});
