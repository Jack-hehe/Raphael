// The arrow keys on a selector. The Settings page hears keys on `document`, and so does the menu:
// which of the two was added first decides which one hears a key first. These tests run the real
// menu code against a small stand-in for the browser that keeps listeners in registration order.

import { afterEach, beforeEach, expect, test, vi } from "vitest";

type UiModule = typeof import("./ui");

class FakeNode {}

class FakeKeyEvent {
  defaultPrevented = false;
  private stopped = false;
  constructor(
    readonly key: string,
    readonly target: FakeElement,
  ) {}
  preventDefault(): void {
    this.defaultPrevented = true;
  }
  stopPropagation(): void {}
  stopImmediatePropagation(): void {
    this.stopped = true;
  }
  get immediatelyStopped(): boolean {
    return this.stopped;
  }
}

class FakeDocument {
  activeElement: FakeElement | null = null;
  readonly body = new FakeElement("body");
  private readonly listeners: Array<{ type: string; handler: (event: FakeKeyEvent) => void }> = [];

  addEventListener(type: string, handler: (event: FakeKeyEvent) => void): void {
    this.listeners.push({ type, handler });
  }
  getElementById(): null {
    return null;
  }
  createElement(tag: string): FakeElement {
    return new FakeElement(tag);
  }
  /** A key goes to the listeners in the order they were added, until one stops it. */
  press(target: FakeElement, key: string): FakeKeyEvent {
    const event = new FakeKeyEvent(key, target);
    for (const { type, handler } of [...this.listeners]) {
      if (type !== "keydown") continue;
      handler(event);
      if (event.immediatelyStopped) break;
    }
    return event;
  }
}

let page: FakeDocument;

class FakeElement extends FakeNode {
  className = "";
  id = "";
  tabIndex = 0;
  disabled = false;
  offsetParent: object | null = {};
  offsetWidth = 200;
  offsetHeight = 120;
  readonly dataset: Record<string, string | undefined> = {};
  readonly style: Record<string, string> = {};
  readonly children: FakeElement[] = [];
  onClick: (() => void) | undefined;
  private parent: FakeElement | null = null;
  private readonly attributes = new Map<string, string>();

  constructor(readonly tag: string) {
    super();
  }

  setAttribute(name: string, value: string): void {
    this.attributes.set(name, value);
  }
  getAttribute(name: string): string | null {
    return this.attributes.get(name) ?? null;
  }
  removeAttribute(name: string): void {
    this.attributes.delete(name);
  }
  append(child: FakeElement): void {
    child.parent = this;
    this.children.push(child);
  }
  remove(): void {
    this.parent?.children.splice(this.parent.children.indexOf(this), 1);
    this.parent = null;
  }
  /** The menu's buttons come from the HTML it sets: each `<button ...>` is one child. */
  set innerHTML(html: string) {
    this.children.length = 0;
    for (const [, attributes] of html.matchAll(/<button\b([^>]*)>/g)) {
      const button = new FakeElement("button");
      for (const [, name, value] of attributes.matchAll(/([\w-]+)="([^"]*)"/g)) {
        button.setAttribute(name, value);
        if (name === "data-value") button.dataset.value = value;
      }
      this.append(button);
    }
  }
  contains(node: unknown): boolean {
    return node === this || this.children.some((child) => child.contains(node));
  }
  closest(selector: string): FakeElement | null {
    if (selector !== ".sel") throw new Error(`the stand-in only knows ".sel", not ${selector}`);
    for (let node: FakeElement | null = this; node; node = node.parent) {
      if (node.className.split(" ").includes("sel")) return node;
    }
    return null;
  }
  querySelectorAll(selector: string): FakeElement[] {
    if (selector !== "button") throw new Error(`the stand-in only knows "button", not ${selector}`);
    return this.children.filter((child) => child.tag === "button");
  }
  querySelector(selector: string): FakeElement | null {
    if (selector === "button") return this.querySelectorAll(selector)[0] ?? null;
    if (selector === '[aria-checked="true"]') return this.children.find((child) => child.getAttribute("aria-checked") === "true") ?? null;
    // An item's label, which the menu looks at for a tooltip when it is cut: the stand-in draws none.
    if (selector === ".mt") return null;
    throw new Error(`the stand-in does not know ${selector}`);
  }
  addEventListener(): void {}
  getBoundingClientRect(): { top: number; bottom: number; right: number } {
    return { top: 100, bottom: 130, right: 300 };
  }
  focus(): void {
    page.activeElement = this;
  }
  click(): void {
    this.onClick?.();
  }
}

const CHOICES = ["a", "b", "c", "d"].map((value) => ({ value, label: value.toUpperCase() }));

let ui: UiModule;

beforeEach(async () => {
  page = new FakeDocument();
  vi.stubGlobal("document", page);
  vi.stubGlobal("window", { addEventListener() {} });
  vi.stubGlobal("Node", FakeNode);
  vi.stubGlobal("Element", FakeElement);
  vi.stubGlobal("innerWidth", 1000);
  vi.stubGlobal("innerHeight", 700);
  vi.resetModules();
  ui = await import("./ui");
});
afterEach(() => {
  vi.unstubAllGlobals();
});

/** A selector button whose click opens its menu with "c" chosen, as the pages do. */
function selector(): FakeElement {
  const anchor = new FakeElement("button");
  anchor.className = "sel";
  const element = anchor as unknown as HTMLElement;
  anchor.onClick = () => ui.openMenu(element, CHOICES, "c", () => {});
  page.body.append(anchor);
  return anchor;
}

/** What the Settings page does: its key handler goes on `document` when the page opens. */
const pageKeys = (event: FakeKeyEvent): void => {
  ui.openMenuOnArrow(event as unknown as KeyboardEvent);
};

/** Opening a menu is when the menu starts listening for keys; this leaves it closed again. */
function startMenuListening(anchor: FakeElement): void {
  ui.openMenu(anchor as unknown as HTMLElement, CHOICES, "c", () => {});
  ui.closeMenu();
}

const focused = (): string | undefined => page.activeElement?.dataset.value;

test("ArrowDown on a selector opens its menu on the first item, even when the menu listens after the page", () => {
  const anchor = selector();
  page.addEventListener("keydown", pageKeys);
  startMenuListening(anchor); // the menu's listener now comes after the page's
  anchor.focus();
  page.press(anchor, "ArrowDown");
  expect(focused()).toBe("a");
});

test("ArrowUp on a selector opens its menu on the last item, even when the menu listens after the page", () => {
  const anchor = selector();
  page.addEventListener("keydown", pageKeys);
  startMenuListening(anchor);
  anchor.focus();
  page.press(anchor, "ArrowUp");
  expect(focused()).toBe("d");
});

test("the first and last items also come first when the menu listens before the page", () => {
  const anchor = selector();
  startMenuListening(anchor);
  page.addEventListener("keydown", pageKeys);
  anchor.focus();
  page.press(anchor, "ArrowDown");
  expect(focused()).toBe("a");
  ui.closeMenu();
  anchor.focus();
  page.press(anchor, "ArrowUp");
  expect(focused()).toBe("d");
});

test("inside an open menu the arrow keys still move between the items and wrap", () => {
  const anchor = selector();
  startMenuListening(anchor);
  page.addEventListener("keydown", pageKeys);
  anchor.focus();
  page.press(anchor, "ArrowDown");
  const open = page.activeElement!;
  page.press(open, "ArrowDown");
  expect(focused()).toBe("b");
  page.press(page.activeElement!, "ArrowUp");
  expect(focused()).toBe("a");
  page.press(page.activeElement!, "ArrowUp");
  expect(focused()).toBe("d");
  page.press(page.activeElement!, "ArrowDown");
  expect(focused()).toBe("a");
});
