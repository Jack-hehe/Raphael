import { describe, expect, test, vi } from "vitest";
import { emptyNavKeyState, installNavInput, reduceNavKey, stepOfButton, type NavKeyInput } from "./nav-input";
import { installWindowHotkeys } from "./window-hotkeys";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn(), Channel: class {} }));

describe("the side buttons of the mouse", () => {
  test("the back button goes back and the forward button goes forward", () => {
    expect(stepOfButton(3)).toBe("back");
    expect(stepOfButton(4)).toBe("forward");
  });

  test("the other buttons ask for nothing", () => {
    for (const button of [0, 1, 2, 5, 6, -1]) expect(stepOfButton(button)).toBeNull();
  });
});

/** A key as the window hears it; `altKey` is set by the helpers below when an Alt is down. */
const key = (code: string, init: Partial<NavKeyInput> = {}): NavKeyInput => ({
  code, type: "keydown", repeat: false, altKey: false, ctrlKey: false, shiftKey: false, metaKey: false, handled: false, ...init,
});
const altDown = (code: "AltLeft" | "AltRight"): NavKeyInput => key(code, { altKey: true });
const altUp = (code: "AltLeft" | "AltRight", stillAlt = false): NavKeyInput => key(code, { type: "keyup", altKey: stillAlt });
/** An arrow key pressed while an Alt is down. */
const arrow = (code: "ArrowLeft" | "ArrowRight", init: Partial<NavKeyInput> = {}): NavKeyInput => key(code, { altKey: true, ...init });

/** Plays the keys one after another, and notes what each one asked for. */
function play(...events: NavKeyInput[]): { step: string | null; preventDefault: boolean }[] {
  let state = emptyNavKeyState();
  return events.map((event) => {
    const { state: next, step, preventDefault } = reduceNavKey(state, event);
    state = next;
    return { step, preventDefault };
  });
}

describe("Alt and the arrow keys", () => {
  test("the left Alt with Left goes back, and with Right goes forward", () => {
    const result = play(altDown("AltLeft"), arrow("ArrowLeft"), arrow("ArrowRight"));
    expect(result.map((r) => r.step)).toEqual([null, "back", "forward"]);
    expect(result.map((r) => r.preventDefault)).toEqual([false, true, true]);
  });

  test("the Alt key itself is never taken", () => {
    const [down] = play(altDown("AltLeft"));
    expect(down).toEqual({ step: null, preventDefault: false });
    expect(play(altDown("AltLeft"), altUp("AltLeft"))).toEqual([
      { step: null, preventDefault: false },
      { step: null, preventDefault: false },
    ]);
  });

  test("the right Alt is left to the dictation key: no step, and nothing is taken", () => {
    const result = play(altDown("AltRight"), arrow("ArrowLeft"), arrow("ArrowRight"), altUp("AltRight"));
    for (const r of result) expect(r).toEqual({ step: null, preventDefault: false });
  });

  test("with both Alt keys down, the right one is in use: no step", () => {
    const result = play(altDown("AltLeft"), altDown("AltRight"), arrow("ArrowLeft"));
    expect(result[2]).toEqual({ step: null, preventDefault: false });
    // the right Alt let go, the left one is still down
    const after = play(altDown("AltLeft"), altDown("AltRight"), altUp("AltRight", true), arrow("ArrowLeft"));
    expect(after[3].step).toBe("back");
  });

  test("AltGr, which is a right Alt with a Ctrl, is no left Alt", () => {
    const result = play(key("ControlLeft", { ctrlKey: true }), altDown("AltRight"), arrow("ArrowLeft", { ctrlKey: true }));
    expect(result[2]).toEqual({ step: null, preventDefault: false });
  });

  test("with Ctrl, Shift or the Windows key as well it is another shortcut", () => {
    for (const extra of [{ ctrlKey: true }, { shiftKey: true }, { metaKey: true }]) {
      const result = play(altDown("AltLeft"), arrow("ArrowLeft", extra));
      expect(result[1]).toEqual({ step: null, preventDefault: false });
    }
  });

  test("an arrow key without Alt is an ordinary key", () => {
    for (const code of ["ArrowLeft", "ArrowRight"] as const) {
      expect(play(key(code))[0]).toEqual({ step: null, preventDefault: false });
    }
  });

  test("other keys with the left Alt ask for nothing", () => {
    const result = play(altDown("AltLeft"), arrow("ArrowLeft", { code: "ArrowUp" }), key("KeyV", { altKey: true }), key("Space", { altKey: true }));
    for (const r of result) expect(r).toEqual({ step: null, preventDefault: false });
  });

  test("holding the keys steps once, and still keeps WebView2 from stepping", () => {
    const result = play(altDown("AltLeft"), arrow("ArrowLeft"), arrow("ArrowLeft", { repeat: true }), arrow("ArrowLeft", { repeat: true }));
    expect(result.map((r) => r.step)).toEqual([null, "back", null, null]);
    expect(result.map((r) => r.preventDefault)).toEqual([false, true, true, true]);
  });

  test("pressing the arrow again while Alt is held steps again", () => {
    const result = play(altDown("AltLeft"), arrow("ArrowLeft"), arrow("ArrowLeft", { type: "keyup" }), arrow("ArrowLeft"));
    expect(result.map((r) => r.step)).toEqual([null, "back", null, "back"]);
  });

  test("a key that something else took first is not a step", () => {
    const result = play(altDown("AltLeft"), arrow("ArrowLeft", { handled: true }));
    expect(result[1]).toEqual({ step: null, preventDefault: false });
  });

  test("the release of the left Alt after a step is swallowed, once", () => {
    const result = play(altDown("AltLeft"), arrow("ArrowRight"), arrow("ArrowRight", { type: "keyup" }), altUp("AltLeft"), altDown("AltLeft"), altUp("AltLeft"));
    expect(result.map((r) => r.preventDefault)).toEqual([false, true, false, true, false, false]);
  });

  test("an Alt release the window never heard is put right by the next key", () => {
    // the left Alt is believed down, but the key says no Alt is
    const { state, step, preventDefault } = reduceNavKey({ altLeft: true, altRight: false, swallowAltUp: false }, key("ArrowLeft"));
    expect(step).toBeNull();
    expect(preventDefault).toBe(false);
    expect(state.altLeft).toBe(false);
  });
});

/** A target the listeners are installed on, and the steps that were asked for. */
function setup() {
  const target = new EventTarget();
  const steps: string[] = [];
  const stop = installNavInput(target, (step) => steps.push(step));
  return { target, steps, stop };
}

/** Sends an event the way the browser would; the init fields become properties of the event. */
function fire(target: EventTarget, type: string, init: Record<string, unknown> = {}): Event {
  const event = new Event(type, { cancelable: true });
  Object.assign(event, init);
  target.dispatchEvent(event);
  return event;
}

const keyInit = (code: string, init: Record<string, unknown> = {}) =>
  ({ code, repeat: false, altKey: false, ctrlKey: false, shiftKey: false, metaKey: false, ...init });

describe("the buttons on the window", () => {
  test("the back and forward buttons are taken from WebView2 on press, release and click", () => {
    const { target } = setup();
    for (const button of [3, 4]) {
      for (const type of ["mousedown", "mouseup", "auxclick"]) {
        expect(fire(target, type, { button }).defaultPrevented, `${type} ${button}`).toBe(true);
      }
    }
  });

  test("each press steps once, with the press; its release and click only stay out of WebView2's hands", () => {
    const { target, steps } = setup();
    fire(target, "mousedown", { button: 3 });
    expect(steps).toEqual(["back"]);
    fire(target, "mouseup", { button: 3 });
    fire(target, "auxclick", { button: 3 });
    expect(steps).toEqual(["back"]);
    fire(target, "mousedown", { button: 4 });
    fire(target, "mouseup", { button: 4 });
    fire(target, "auxclick", { button: 4 });
    expect(steps).toEqual(["back", "forward"]);
  });

  test("a release that began elsewhere is no request", () => {
    const { target, steps } = setup();
    fire(target, "mouseup", { button: 3 });
    fire(target, "auxclick", { button: 4 });
    expect(steps).toEqual([]);
  });

  test("the left, middle and right buttons are left alone", () => {
    const { target, steps } = setup();
    for (const button of [0, 1, 2]) {
      for (const type of ["mousedown", "mouseup", "auxclick"]) {
        expect(fire(target, type, { button }).defaultPrevented, `${type} ${button}`).toBe(false);
      }
    }
    expect(steps).toEqual([]);
  });

});

describe("the keys on the window", () => {
  test("the left Alt with the arrow keys steps, and takes the keys from WebView2", () => {
    const { target, steps } = setup();
    expect(fire(target, "keydown", keyInit("AltLeft", { altKey: true })).defaultPrevented).toBe(false);
    expect(fire(target, "keydown", keyInit("ArrowLeft", { altKey: true })).defaultPrevented).toBe(true);
    fire(target, "keyup", keyInit("ArrowLeft", { altKey: true }));
    expect(fire(target, "keydown", keyInit("ArrowRight", { altKey: true })).defaultPrevented).toBe(true);
    expect(steps).toEqual(["back", "forward"]);
    // the Alt that comes up after the steps does not count as a tap of Alt alone
    expect(fire(target, "keyup", keyInit("AltLeft")).defaultPrevented).toBe(true);
    expect(fire(target, "keydown", keyInit("AltLeft", { altKey: true })).defaultPrevented).toBe(false);
    expect(fire(target, "keyup", keyInit("AltLeft")).defaultPrevented).toBe(false);
  });

  test("the right Alt with the arrow keys does nothing, and is not touched", () => {
    const { target, steps } = setup();
    expect(fire(target, "keydown", keyInit("AltRight", { altKey: true })).defaultPrevented).toBe(false);
    expect(fire(target, "keydown", keyInit("ArrowLeft", { altKey: true })).defaultPrevented).toBe(false);
    expect(fire(target, "keydown", keyInit("ArrowRight", { altKey: true })).defaultPrevented).toBe(false);
    expect(fire(target, "keyup", keyInit("AltRight")).defaultPrevented).toBe(false);
    expect(steps).toEqual([]);
  });

  test("a window that lost the focus forgets which Alt was down", () => {
    const { target, steps } = setup();
    fire(target, "keydown", keyInit("AltLeft", { altKey: true }));
    target.dispatchEvent(new Event("blur"));
    fire(target, "keydown", keyInit("ArrowLeft", { altKey: true }));
    expect(steps).toEqual([]);
  });

  test("a key that a listener before it took is not a step", () => {
    const target = new EventTarget();
    const steps: string[] = [];
    target.addEventListener("keydown", (event) => event.preventDefault()); // a menu, say
    installNavInput(target, (step) => steps.push(step));
    fire(target, "keydown", keyInit("AltLeft", { altKey: true }));
    fire(target, "keydown", keyInit("ArrowLeft", { altKey: true }));
    expect(steps).toEqual([]);
  });

});

test("stopping the listeners hands the buttons and keys back", () => {
  const { target, steps, stop } = setup();
  stop();
  expect(fire(target, "mousedown", { button: 3 }).defaultPrevented).toBe(false);
  expect(fire(target, "mouseup", { button: 3 }).defaultPrevented).toBe(false);
  fire(target, "keydown", keyInit("AltLeft", { altKey: true }));
  expect(fire(target, "keydown", keyInit("ArrowLeft", { altKey: true })).defaultPrevented).toBe(false);
  expect(steps).toEqual([]);
});

test("beside the window's hotkeys, the right Alt still starts and ends a dictation and the left Alt steps", async () => {
  const target = new EventTarget();
  const sent = vi.fn().mockResolvedValue(undefined);
  const steps: string[] = [];
  const bindings = { dictation_keys: ["AltRight"], paste_last_keys: ["AltRight+V"] };
  installWindowHotkeys(() => bindings, target, sent);
  installNavInput(target, (step) => steps.push(step));
  const press = (type: string, code: string, altKey: boolean) =>
    fire(target, type, { ...keyInit(code, { altKey }), getModifierState: () => false });

  // the dictation key is swallowed by the hotkeys, and the navigation neither steps nor lets go of it
  expect(press("keydown", "AltRight", true).defaultPrevented).toBe(true);
  expect(press("keydown", "ArrowLeft", true).defaultPrevented).toBe(false);
  expect(press("keyup", "ArrowLeft", true).defaultPrevented).toBe(false);
  expect(press("keyup", "AltRight", false).defaultPrevented).toBe(true);
  await Promise.resolve();
  expect(sent.mock.calls).toEqual([["dictation_down"], ["dictation_up"]]);
  expect(steps).toEqual([]);

  // the left Alt is not the dictation key: its chord steps, and no hotkey is sent
  press("keydown", "AltLeft", true);
  expect(press("keydown", "ArrowLeft", true).defaultPrevented).toBe(true);
  press("keyup", "AltLeft", false);
  await Promise.resolve();
  expect(steps).toEqual(["back"]);
  expect(sent).toHaveBeenCalledTimes(2);
});
