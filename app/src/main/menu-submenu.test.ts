// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
import { closeMenu, openMenu } from "./ui";

afterEach(() => { closeMenu(); document.body.replaceChildren(); });

test("action-only menus have no leading separator and keep their parent beside a submenu", () => {
  const anchor = document.createElement("button"), picked = vi.fn();
  document.body.append(anchor);
  openMenu(anchor, [], "", () => {}, undefined, [{ label: "Before acting", submenu: true, run(row) {
    openMenu(row, [{ value: "ask", label: "Ask first" }], "ask", picked, "Before acting", [], { placement: "beside" });
  } }]);
  const parent = document.getElementById("app-popover")!;
  expect(parent.querySelector('[role="separator"]')).toBeNull();
  const row = parent.querySelector<HTMLButtonElement>("button")!;
  expect(row.getAttribute("aria-haspopup")).toBe("menu");
  row.click();
  expect(parent.isConnected).toBe(true);
  expect(document.querySelector("#app-submenu [aria-checked=true]")).not.toBeNull();
  document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  expect(parent.isConnected).toBe(true);
  expect(document.getElementById("app-submenu")).toBeNull();
  expect(document.activeElement).toBe(row);
  row.click();
  document.querySelector<HTMLButtonElement>('#app-submenu [data-value="ask"]')!.click();
  expect(picked).toHaveBeenCalledWith("ask");
  expect(document.querySelector("#app-popover, #app-submenu")).toBeNull();
  expect(document.activeElement).toBe(anchor);
});

test("Tab or an outside activation closes both menu levels and restores the composer trigger", () => {
  const anchor = document.createElement("button"), outside = document.createElement("button");
  document.body.append(anchor, outside);
  const show = (): void => {
    openMenu(anchor, [], "", () => {}, undefined, [{ label: "Before acting", submenu: true, run(row) {
      openMenu(row, [{ value: "ask", label: "Ask first" }], "ask", () => {}, undefined, [], { placement: "beside" });
    } }]);
    document.querySelector<HTMLButtonElement>("#app-popover button")!.click();
  };
  show();
  document.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", bubbles: true }));
  expect(document.querySelector("#app-popover, #app-submenu")).toBeNull();
  expect(document.activeElement).toBe(anchor);
  show(); outside.click();
  expect(document.querySelector("#app-popover, #app-submenu")).toBeNull();
  expect(document.activeElement).toBe(anchor);
});
