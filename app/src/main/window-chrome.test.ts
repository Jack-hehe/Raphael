import { afterEach, describe, expect, test, vi } from "vitest";
import { RESIZE_SETTLE_MS, installWindowChrome, type WindowChromeWindow } from "./window-chrome";

function fixture(initiallyMaximized = false) {
  const target = new EventTarget();
  let maximized = initiallyMaximized;
  let resized: (() => void) | undefined;
  const stop = vi.fn();
  const calls: string[] = [];
  const states: boolean[] = [];
  const window: WindowChromeWindow = {
    minimize: async () => { calls.push("minimize"); },
    toggleMaximize: async () => { calls.push("maximize"); maximized = !maximized; },
    close: async () => { calls.push("close"); },
    startDragging: async () => { calls.push("drag"); },
    startResizeDragging: async (direction) => { calls.push(`resize:${direction}`); },
    isMaximized: async () => maximized,
    onResized: async (handler) => { resized = handler; return stop; },
  };
  const errors: unknown[] = [];
  const dispose = installWindowChrome(target, window, (state) => states.push(state), (error) => errors.push(error));
  const dispatch = (type: string, kind: "minimize" | "maximize" | "close" | "drag" | "interactive" | "resize", detail = 1, button = 0) => {
    const node = {
      dataset: { windowAction: kind, windowResize: "SouthEast" },
      closest: (selector: string) => {
        if (selector === "[data-window-action]") return ["minimize", "maximize", "close"].includes(kind) ? node : null;
        if (selector === "[data-window-resize]") return kind === "resize" ? node : null;
        if (selector === "[data-window-drag]") return kind === "drag" || kind === "interactive" ? node : null;
        return kind === "interactive" || ["minimize", "maximize", "close"].includes(kind) ? node : null;
      },
    };
    const event = new Event(type, { cancelable: true });
    Object.defineProperty(event, "target", { value: node });
    Object.assign(event, { detail, button });
    target.dispatchEvent(event);
    return event;
  };
  return { calls, states, window, errors, dispose, dispatch, stop, resize: () => resized?.(), setMaximized: (state: boolean) => { maximized = state; } };
}

const settle = async () => { await Promise.resolve(); await Promise.resolve(); };

describe("main window chrome", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  test("buttons request minimize, maximize and close while a keyboard click remains usable", async () => {
    const app = fixture();
    await settle();
    app.dispatch("click", "minimize", 0);
    app.dispatch("click", "maximize", 0);
    await settle();
    app.dispatch("click", "close", 0);
    expect(app.calls).toEqual(["minimize", "maximize", "close"]);
    expect(app.states).toEqual([false, true]);
    app.dispose();
  });

  test("only a primary press on empty drag space starts dragging", async () => {
    const app = fixture();
    app.dispatch("mousedown", "interactive");
    app.dispatch("mousedown", "close");
    app.dispatch("mousedown", "drag", 1, 2);
    const press = app.dispatch("mousedown", "drag");
    expect(app.calls).toEqual(["drag"]);
    expect(press.defaultPrevented).toBe(true);
    app.dispose();
  });

  test("the second titlebar press maximizes once even when dblclick also arrives", async () => {
    const app = fixture();
    app.dispatch("mousedown", "drag", 1);
    app.dispatch("mousedown", "drag", 2);
    app.dispatch("dblclick", "drag", 2);
    await settle();
    expect(app.calls).toEqual(["drag", "maximize"]);
    expect(app.states[app.states.length - 1]).toBe(true);
    app.dispatch("dblclick", "interactive", 2);
    expect(app.calls).toEqual(["drag", "maximize"]);
    app.dispose();
  });

  test("a standalone double click also toggles maximize", async () => {
    const app = fixture();
    app.dispatch("dblclick", "drag", 2);
    expect(app.calls).toEqual(["maximize"]);
    app.dispose();
  });

  test("resize events update restore state and maximized windows reject edge dragging", async () => {
    vi.useFakeTimers();
    const app = fixture();
    await settle();
    app.dispatch("mousedown", "resize");
    expect(app.calls).toEqual(["resize:SouthEast"]);
    app.setMaximized(true);
    app.resize();
    await vi.advanceTimersByTimeAsync(RESIZE_SETTLE_MS);
    app.dispatch("mousedown", "resize");
    expect(app.calls).toEqual(["resize:SouthEast"]);
    expect(app.states).toEqual([false, true]);
    app.dispose();
    expect(app.stop).toHaveBeenCalledOnce();
    app.dispatch("click", "close");
    expect(app.calls).toEqual(["resize:SouthEast"]);
  });

  test("a burst of resizes asks the window once, after it settles, and redraws the controls only when the state changed", async () => {
    vi.useFakeTimers();
    const app = fixture();
    await settle();
    const readState = vi.spyOn(app.window, "isMaximized");
    for (let i = 0; i < 30; i++) {
      app.resize(); // dragging an edge
      await vi.advanceTimersByTimeAsync(16);
    }
    expect(readState).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(RESIZE_SETTLE_MS);
    expect(readState).toHaveBeenCalledTimes(1);
    expect(app.states).toEqual([false]); // the same state: nothing to redraw
    app.setMaximized(true);
    app.resize();
    await vi.advanceTimersByTimeAsync(RESIZE_SETTLE_MS);
    expect(readState).toHaveBeenCalledTimes(2);
    expect(app.states).toEqual([false, true]);
    app.resize();
    await vi.advanceTimersByTimeAsync(RESIZE_SETTLE_MS);
    expect(readState).toHaveBeenCalledTimes(3);
    expect(app.states).toEqual([false, true]); // still maximized: not redrawn
    expect(app.calls).toEqual([]);
    app.dispose();
  });

  test("the button that changes the state redraws the controls at once, and the resizes it causes add nothing", async () => {
    vi.useFakeTimers();
    const app = fixture();
    await settle();
    app.dispatch("click", "maximize", 0);
    await settle();
    expect(app.states).toEqual([false, true]);
    app.resize(); // the window's own resize after maximizing
    await vi.advanceTimersByTimeAsync(RESIZE_SETTLE_MS);
    expect(app.states).toEqual([false, true]);
    app.dispose();
  });

  test("a resize that is still settling when the window chrome goes away reads nothing", async () => {
    vi.useFakeTimers();
    const app = fixture();
    await settle();
    const readState = vi.spyOn(app.window, "isMaximized");
    app.resize();
    app.dispose();
    await vi.advanceTimersByTimeAsync(RESIZE_SETTLE_MS * 2);
    expect(readState).not.toHaveBeenCalled();
  });

  test("failed window requests are reported without leaving an unhandled rejection", async () => {
    const app = fixture();
    const error = new Error("window unavailable");
    app.window.close = async () => { throw error; };
    app.dispatch("click", "close");
    await settle();
    expect(app.errors).toEqual([error]);
    app.dispose();
  });

  test("a resize subscription completed after disposal is immediately removed", async () => {
    const app = fixture();
    app.dispose();
    await settle();
    expect(app.stop).toHaveBeenCalledOnce();
    expect(app.states).toEqual([]);
  });
});
