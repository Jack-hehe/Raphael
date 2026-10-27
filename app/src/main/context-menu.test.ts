// @vitest-environment happy-dom
// Right click is the ⋯ (context-menu.ts): a right click anywhere on a row that has a ⋯ opens that button's menu at the
// pointer, the keys do the same for the focused row, a drag is not a click, and no ⋯ is left without its right click.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { afterEach, beforeEach, describe, expect, test } from "vitest";
import { OVERFLOW_ROWS, installContextMenus, overflowAt, uncoveredOverflows } from "./context-menu";
import { closeMenu, iconButton, openPopover } from "./ui";

/** A menu of two items per ⋯, the way `openActions` draws one: the item labels name the row. */
function openFor(button: HTMLElement): void {
  const row = button.closest(".conversation-row")!.id;
  openPopover(button, ["rename", "move", "delete"].map((id) => `<button type="button" role="menuitem" data-pick="${id}-${row}">${id}</button>`).join(""), { className: "menu", role: "menu" });
}

const picks = (): string[] => [...document.querySelectorAll<HTMLElement>("#app-popover [data-pick]")].map((item) => item.dataset.pick!);
const popover = (): HTMLElement | null => document.querySelector<HTMLElement>("#app-popover");

const press = (target: Element, x: number, y: number): void => {
  target.dispatchEvent(new MouseEvent("pointerdown", { bubbles: true, cancelable: true, button: 2, clientX: x, clientY: y }));
};
/** What the browser sends when the right button is released: a `contextmenu` with the button and the place. */
const release = (target: Element, x: number, y: number): MouseEvent => {
  const event = new MouseEvent("contextmenu", { bubbles: true, cancelable: true, button: 2, clientX: x, clientY: y });
  target.dispatchEvent(event);
  return event;
};
const rightClick = (target: Element, x = 300, y = 200): MouseEvent => {
  press(target, x, y);
  return release(target, x, y);
};
/** A click of the person's own: the browser marks it trusted, which a script's `.click()` is not. */
const personClick = (target: Element): void => {
  const click = new MouseEvent("click", { bubbles: true, cancelable: true });
  Object.defineProperty(click, "isTrusted", { value: true });
  target.dispatchEvent(click);
};
/** The key: no button of the mouse behind it. */
const menuKey = (target: Element): MouseEvent => {
  const event = new MouseEvent("contextmenu", { bubbles: true, cancelable: true, button: 0, clientX: 4, clientY: 4 });
  target.dispatchEvent(event);
  return event;
};

let stop: () => void;
beforeEach(() => {
  closeMenu();
  document.body.innerHTML =
    ["a", "b"].map((id) => `<div class="conversation-row" id="${id}"><button type="button" class="btn conversation-open" id="open-${id}">${id}</button>` +
      iconButton("more", "更多", ` data-conversation-more="${id}" aria-haspopup="menu"`, "conversation-more") + `</div>`).join("") +
    `<div class="conversation-row" id="c"><button type="button" class="btn conversation-open">c</button>${iconButton("more", "更多", ' data-conversation-more="c" disabled')}</div>` +
    `<p id="plain">nothing here has a menu</p><input id="field">`;
  document.addEventListener("click", clicked);
  stop = installContextMenus(document);
});
afterEach(() => {
  stop();
  document.removeEventListener("click", clicked);
  closeMenu();
});
function clicked(event: Event): void {
  const more = (event.target as Element).closest<HTMLElement>("[data-conversation-more]");
  if (more) openFor(more);
}

describe("a right click is the ⋯ of its row", () => {
  test("a right click anywhere on a row opens the menu of its ⋯, the same one a click on the ⋯ opens", () => {
    for (const target of [document.getElementById("open-a")!, document.querySelector("#a [data-conversation-more]")!, document.getElementById("a")!]) {
      closeMenu();
      expect(rightClick(target).defaultPrevented).toBe(true);
      expect(picks()).toEqual(["rename-a", "move-a", "delete-a"]);
    }
    const viaRightClick = picks();
    closeMenu();
    document.querySelector<HTMLElement>("#a [data-conversation-more]")!.click();
    expect(picks()).toEqual(viaRightClick);
    rightClick(document.getElementById("open-b")!);
    expect(picks()).toEqual(["rename-b", "move-b", "delete-b"]);
    expect(document.querySelectorAll("#app-popover")).toHaveLength(1);
  });

  test("the menu stands at the pointer, and a click on the ⋯ keeps it under the button", () => {
    rightClick(document.getElementById("open-a")!, 300, 200);
    expect([popover()!.style.left, popover()!.style.top]).toEqual(["300px", "204px"]);
    closeMenu();
    personClick(document.querySelector("#a [data-conversation-more]")!);
    expect(popover()!.style.left).not.toBe("300px");
  });

  test("the menu is kept inside the window at the edge of it", () => {
    rightClick(document.getElementById("open-a")!, innerWidth - 2, innerHeight - 2);
    const box = popover()!;
    expect(parseFloat(box.style.left)).toBeLessThanOrEqual(innerWidth - 8);
    expect(parseFloat(box.style.top)).toBeLessThanOrEqual(innerHeight - 8);
  });

  test("another row's right click replaces the open menu, and the one of the first row opens again", () => {
    rightClick(document.getElementById("open-a")!);
    rightClick(document.getElementById("open-b")!, 500, 300);
    expect(picks()).toEqual(["rename-b", "move-b", "delete-b"]);
    expect(document.querySelectorAll("#app-popover")).toHaveLength(1);
    expect(popover()!.style.left).toBe("500px");
    rightClick(document.getElementById("open-a")!, 100, 100);
    expect(picks()).toEqual(["rename-a", "move-a", "delete-a"]);
    expect(popover()!.style.left).toBe("100px");
  });

  test("a menu that replaces the first from the same button, 「移到分組」 after the first, stands at the pointer too", () => {
    rightClick(document.getElementById("open-a")!, 260, 180);
    const first = popover()!;
    document.querySelector<HTMLElement>('#app-popover [data-pick="move-a"]')!.addEventListener("click", () => {
      openPopover(document.querySelector<HTMLElement>("#a [data-conversation-more]")!, '<button type="button" role="menuitemradio" data-value="1">專案</button>', { className: "menu", role: "menu" });
    });
    document.querySelector<HTMLElement>('#app-popover [data-pick="move-a"]')!.click();
    expect(popover()).not.toBe(first);
    expect(popover()!.querySelector("[data-value]")).not.toBeNull();
    expect(popover()!.style.left).toBe("260px");
  });

  test("a click of the person's own on the ⋯ places its menu under the button again", () => {
    rightClick(document.getElementById("open-a")!, 300, 200);
    personClick(document.querySelector("#a [data-conversation-more]")!);
    expect(popover()!.style.left).not.toBe("300px");
  });

  test("a menu at the pointer closes when the page scrolls, and one under its button follows it", () => {
    rightClick(document.getElementById("open-a")!);
    document.dispatchEvent(new Event("scroll"));
    expect(popover()).toBeNull();
    personClick(document.querySelector("#a [data-conversation-more]")!);
    document.dispatchEvent(new Event("scroll"));
    expect(popover()).not.toBeNull();
  });
});

describe("the keys", () => {
  test("Shift+F10 and the Menu key open the menu of the focused row's ⋯ under that button", () => {
    const row = document.getElementById("open-a")!;
    row.focus();
    expect(menuKey(row).defaultPrevented).toBe(true);
    expect(picks()).toEqual(["rename-a", "move-a", "delete-a"]);
    expect(popover()!.style.left).not.toBe("4px");
    expect(document.querySelector("#a [data-conversation-more]")!.getAttribute("aria-expanded")).toBe("true");
  });

  test("Escape closes the menu a key opened and gives the focus back to the ⋯", () => {
    const row = document.getElementById("open-b")!;
    row.focus();
    menuKey(row);
    expect(popover()).not.toBeNull();
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
    expect(popover()).toBeNull();
    expect(document.activeElement).toBe(document.querySelector("#b [data-conversation-more]"));
  });
});

describe("what is not a click", () => {
  test("a right button dragged more than a few pixels opens no menu when it ends", () => {
    const row = document.getElementById("open-a")!;
    press(row, 100, 100);
    const event = release(row, 180, 104);
    expect(event.defaultPrevented).toBe(true);
    expect(popover()).toBeNull();
    // the next click is a click again
    expect(rightClick(row, 100, 100).defaultPrevented).toBe(true);
    expect(popover()).not.toBeNull();
  });

  test("a right button that barely moves is still a click", () => {
    const row = document.getElementById("open-a")!;
    press(row, 100, 100);
    release(row, 102, 101);
    expect(picks()).toEqual(["rename-a", "move-a", "delete-a"]);
  });

  test("a right click on anything without a ⋯ keeps no browser menu, and a text field keeps its own", () => {
    expect(rightClick(document.getElementById("plain")!).defaultPrevented).toBe(true);
    expect(popover()).toBeNull();
    expect(rightClick(document.body).defaultPrevented).toBe(true);
    expect(rightClick(document.getElementById("field")!).defaultPrevented).toBe(false);
    expect(popover()).toBeNull();
  });

  test("a ⋯ that cannot be pressed opens nothing, and a right click that another handler took is left to it", () => {
    expect(rightClick(document.getElementById("c")!).defaultPrevented).toBe(true);
    expect(popover()).toBeNull();
    const row = document.getElementById("open-a")!;
    const claim = (event: Event): void => event.preventDefault();
    row.addEventListener("contextmenu", claim);
    release(row, 10, 10);
    row.removeEventListener("contextmenu", claim);
    expect(popover()).toBeNull();
  });
});

// ---------- no ⋯ without its right click ----------

const SRC = __dirname;
const walk = (dir: string): string[] =>
  readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? (name === "service-logos" ? [] : walk(path)) : [path];
  });
const rel = (path: string): string => relative(SRC, path).split(sep).join("/");
const files = walk(SRC);
const sources = files.filter((file) => file.endsWith(".ts") && !file.endsWith(".test.ts") && !file.includes(`${sep}i18n${sep}`));
const read = (file: string): string => readFileSync(file, "utf8");

/** The text of the lines a ⋯ is drawn on: the call that draws it, to the end of its line and two lines more. */
function drawn(): { where: string; text: string }[] {
  return sources.flatMap((file) => {
    const text = read(file);
    return [...text.matchAll(/\b(?:iconButton|iconBtn|action)\(\s*["']more["']/g)].map((match) => {
      const line = text.slice(0, match.index).split("\n").length;
      return { where: `${rel(file)}:${line}`, text: text.split("\n").slice(line - 1, line + 2).join("\n") };
    });
  });
}
/** What a line of the table is known by in the code that draws its ⋯: `data-member-more`, or `data-det="routine-menu"`. */
const needle = (more: string): string => more.slice(1, -1);

describe("no ⋯ is left without its right click", () => {
  test("every ⋯ the sources draw has its row in the table, and every row of the table is drawn", () => {
    const sites = drawn();
    expect(sites.length).toBeGreaterThanOrEqual(OVERFLOW_ROWS.length);
    const missing = sites.filter((site) => !OVERFLOW_ROWS.some(({ more }) => site.text.includes(needle(more)))).map((site) => site.where);
    expect(missing, "a ⋯ drawn with no line in OVERFLOW_ROWS (context-menu.ts): add its button and its row").toEqual([]);
    const unused = OVERFLOW_ROWS.filter(({ more }) => !sites.some((site) => site.text.includes(needle(more)))).map(({ more }) => more);
    expect(unused, "a line of OVERFLOW_ROWS that no ⋯ is drawn with").toEqual([]);
  });

  test("every row of the table is a selector of the page, made of classes and attributes some code or style sheet has", () => {
    const everything = files.filter((file) => /\.(?:ts|css)$/.test(file) && !file.endsWith(".test.ts")).map(read).join("\n");
    for (const { more, row } of OVERFLOW_ROWS) {
      expect(() => document.querySelector(more), more).not.toThrow();
      expect(() => document.querySelector(row), row).not.toThrow();
      for (const [, name] of row.matchAll(/\.([\w-]+)/g)) expect(everything, `${row}: ${name}`).toContain(name);
      for (const [, name] of row.matchAll(/\[([\w-]+)/g)) expect(everything, `${row}: ${name}`).toContain(name);
    }
  });

  test("the ⋯ glyph is drawn by iconButton alone, which marks every ⋯ data-overflow", () => {
    const outside = sources.filter((file) => rel(file) !== "ui.ts" && /\bico\(\s*["']more["']/.test(read(file))).map(rel);
    expect(outside).toEqual([]);
    expect(iconButton("more", "更多")).toContain(" data-overflow");
    expect(iconButton("trash", "刪除")).not.toContain("data-overflow");
    expect(read(join(SRC, "ui.ts"))).toMatch(/name === "more" \? " data-overflow" : ""/);
  });

  test("a ⋯ that no row reaches is found, and so is a row that holds two", () => {
    expect(uncoveredOverflows(document)).toEqual([]);
    const stray = document.createElement("div");
    stray.innerHTML = iconButton("more", "更多", ' data-nowhere="1"');
    document.body.append(stray);
    expect(uncoveredOverflows(document).map((button) => button.dataset.nowhere)).toEqual(["1"]);
    stray.remove();
    const twice = document.querySelector("#a")!;
    twice.insertAdjacentHTML("beforeend", iconButton("more", "更多", ' data-conversation-more="x"'));
    expect(uncoveredOverflows(document)).toHaveLength(2);
    expect(overflowAt(document.getElementById("open-a"))!.button.dataset.conversationMore).toBe("a");
  });
});
