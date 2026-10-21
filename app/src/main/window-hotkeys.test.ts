import { describe, expect, test, vi } from "vitest";
import { emptyHotkeyState, reduceHotkey, installWindowHotkeys, setWindowHotkeysPaused, type KeyInput } from "./window-hotkeys";
import { hotkeysPause } from "./api";

const { invoke } = vi.hoisted(() => ({ invoke: vi.fn() }));
vi.mock("@tauri-apps/api/core", () => ({ invoke, Channel: class {} }));

const defaults = { dictation_keys: ["AltRight"], paste_last_keys: ["AltRight+V"] };
const key = (code: string, type: "keydown" | "keyup" = "keydown", repeat = false): KeyInput =>
  ({ code, type, repeat, altGraph: false });

describe("focused-window hotkeys", () => {
  test("Right Alt down/up reaches dictation without a hook event", () => {
    const down = reduceHotkey(emptyHotkeyState(), key("AltRight"), defaults);
    expect(down.actions).toEqual(["dictation_down"]);
    expect(down.preventDefault).toBe(true);
    const up = reduceHotkey(down.state, key("AltRight", "keyup"), defaults);
    expect(up.actions).toEqual(["dictation_up"]);
    expect(up.preventDefault).toBe(true);
  });

  test("AltRight+V pastes once and swallows its release and repeats", () => {
    const alt = reduceHotkey(emptyHotkeyState(), key("AltRight"), defaults);
    const paste = reduceHotkey(alt.state, key("KeyV"), defaults);
    expect(paste.actions).toEqual(["paste_last"]);
    const repeat = reduceHotkey(paste.state, key("KeyV", "keydown", true), defaults);
    expect(repeat.actions).toEqual([]);
    expect(repeat.preventDefault).toBe(true);
    const up = reduceHotkey(repeat.state, key("KeyV", "keyup"), defaults);
    expect(up.actions).toEqual([]);
    expect(up.preventDefault).toBe(true);
  });

  test("ignores dictation repeat and orphan key-up", () => {
    expect(reduceHotkey(emptyHotkeyState(), key("AltRight", "keyup"), defaults).actions).toEqual([]);
    const down = reduceHotkey(emptyHotkeyState(), key("AltRight"), defaults);
    expect(reduceHotkey(down.state, key("AltRight", "keydown", true), defaults).actions).toEqual([]);
  });

  test("Escape cancels once while leaving idle UI Escape behavior available", () => {
    const cancel = reduceHotkey(emptyHotkeyState(), key("Escape"), defaults);
    expect(cancel.actions).toEqual(["cancel"]);
    expect(cancel.preventDefault).toBe(false);
    expect(reduceHotkey(cancel.state, key("Escape", "keydown", true), defaults).actions).toEqual([]);
    expect(reduceHotkey(cancel.state, key("Escape", "keyup"), defaults).actions).toEqual([]);
  });

  test("recording a binding forwards and suppresses nothing", () => {
    for (const code of ["AltRight", "KeyV", "Escape"]) {
      const result = reduceHotkey(emptyHotkeyState(), key(code), defaults, true);
      expect(result.actions).toEqual([]);
      expect(result.preventDefault).toBe(false);
    }
  });

  test("uses configured bindings, including chords, sides, function keys and release of a modifier first", () => {
    const custom = { dictation_keys: ["ControlRight+Space", "F9"], paste_last_keys: ["ControlRight+KeyV"] };
    expect(reduceHotkey(emptyHotkeyState(), key("AltRight"), custom).actions).toEqual([]);
    const ctrl = reduceHotkey(emptyHotkeyState(), key("ControlRight"), custom);
    const down = reduceHotkey(ctrl.state, key("Space"), custom);
    expect(down.actions).toEqual(["dictation_down"]);
    expect(reduceHotkey(down.state, key("ControlRight", "keyup"), custom).actions).toEqual(["dictation_up"]);
    const left = reduceHotkey(emptyHotkeyState(), key("ControlLeft"), custom);
    expect(reduceHotkey(left.state, key("Space"), custom).actions).toEqual([]);
    expect(reduceHotkey(ctrl.state, key("KeyV"), custom).actions).toEqual(["paste_last"]);
    expect(reduceHotkey(emptyHotkeyState(), key("F9"), custom).actions).toEqual(["dictation_down"]);
  });

  test("rejects extra modifiers and ordinary typing", () => {
    const shift = reduceHotkey(emptyHotkeyState(), key("ShiftLeft"), defaults);
    expect(reduceHotkey(shift.state, key("AltRight"), defaults).actions).toEqual([]);
    expect(reduceHotkey(emptyHotkeyState(), key("KeyV"), defaults).actions).toEqual([]);
  });

  test("suppresses Alt prefixes of configured chords without starting dictation", () => {
    const custom = { dictation_keys: ["AltRight+Space"], paste_last_keys: [] };
    const alt = reduceHotkey(emptyHotkeyState(), key("AltRight"), custom);
    expect(alt.actions).toEqual([]);
    expect(alt.preventDefault).toBe(true);
    const space = reduceHotkey(alt.state, key("Space"), custom);
    expect(space.actions).toEqual(["dictation_down"]);
    expect(reduceHotkey(space.state, key("AltRight", "keyup"), custom).preventDefault).toBe(true);
  });

  test("Right Alt+Space asks Raphael, after Right Alt went down, as the hook reports it", () => {
    const withAgent = { ...defaults, agent_keys: ["AltRight+Space"] };
    const alt = reduceHotkey(emptyHotkeyState(), key("AltRight"), withAgent);
    expect(alt.actions).toEqual(["dictation_down"]);
    const space = reduceHotkey(alt.state, key("Space"), withAgent);
    expect(space.actions).toEqual(["agent_down"]);
    expect(space.preventDefault).toBe(true);
    // the key repeats and the release of Space are swallowed; the release of either key ends the question
    expect(reduceHotkey(space.state, key("Space", "keydown", true), withAgent).actions).toEqual([]);
    const spaceUp = reduceHotkey(space.state, key("Space", "keyup"), withAgent);
    expect(spaceUp.actions).toEqual(["agent_up"]);
    expect(spaceUp.preventDefault).toBe(true);
    const altUp = reduceHotkey(spaceUp.state, key("AltRight", "keyup"), withAgent);
    expect(altUp.actions).toEqual(["dictation_up"]);
  });

  test("letting go of Right Alt first also ends the question once", () => {
    const withAgent = { ...defaults, agent_keys: ["AltRight+Space"] };
    const alt = reduceHotkey(emptyHotkeyState(), key("AltRight"), withAgent);
    const space = reduceHotkey(alt.state, key("Space"), withAgent);
    const altUp = reduceHotkey(space.state, key("AltRight", "keyup"), withAgent);
    expect(altUp.actions).toEqual(["dictation_up", "agent_up"]);
    expect(reduceHotkey(altUp.state, key("Space", "keyup"), withAgent).actions).toEqual([]);
  });

  test("Space first, then Right Alt, asks Raphael too, as the hook reports it", () => {
    const withAgent = { ...defaults, agent_keys: ["AltRight+Space"] };
    const space = reduceHotkey(emptyHotkeyState(), key("Space"), withAgent);
    expect(space.actions).toEqual([]);
    expect(space.preventDefault).toBe(false);
    const alt = reduceHotkey(space.state, key("AltRight"), withAgent);
    expect(alt.actions).toEqual(["dictation_down", "agent_down"]);
    expect(alt.preventDefault).toBe(true);
    // the page saw the Space go down, so it sees it come up; that release ends the question
    const spaceUp = reduceHotkey(alt.state, key("Space", "keyup"), withAgent);
    expect(spaceUp.actions).toEqual(["agent_up"]);
    expect(spaceUp.preventDefault).toBe(false);
    expect(reduceHotkey(spaceUp.state, key("AltRight", "keyup"), withAgent).actions).toEqual(["dictation_up"]);
  });

  test("Space alone, and a window without a question key, do not ask Raphael", () => {
    const withAgent = { ...defaults, agent_keys: ["AltRight+Space"] };
    expect(reduceHotkey(emptyHotkeyState(), key("Space"), withAgent).actions).toEqual([]);
    const alt = reduceHotkey(emptyHotkeyState(), key("AltRight"), defaults);
    expect(reduceHotkey(alt.state, key("Space"), defaults).actions).toEqual([]);
  });

  test("a question key of its own: an Alt prefix is swallowed like the other chords", () => {
    const own = { dictation_keys: ["F9"], paste_last_keys: [], agent_keys: ["AltRight+Space"] };
    const alt = reduceHotkey(emptyHotkeyState(), key("AltRight"), own);
    expect(alt.actions).toEqual([]);
    expect(alt.preventDefault).toBe(true);
    expect(reduceHotkey(alt.state, key("Space"), own).actions).toEqual(["agent_down"]);
  });

  test("ignores Windows' phantom left Ctrl for AltGr", () => {
    const ctrl = reduceHotkey(emptyHotkeyState(), key("ControlLeft"), defaults);
    const alt = reduceHotkey(ctrl.state, { ...key("AltRight"), altGraph: true }, defaults);
    expect(alt.actions).toEqual(["dictation_down"]);
    expect(reduceHotkey(alt.state, { ...key("KeyV"), altGraph: true }, defaults).actions).toEqual(["paste_last"]);
  });
});

test("capture listener forwards commands, pauses synchronously, releases on blur and disposes", async () => {
  const target = new EventTarget();
  const send = vi.fn().mockResolvedValue(undefined);
  const remove = installWindowHotkeys(() => defaults, target, send);
  const dispatch = (code: string, type = "keydown") => {
    const event = new Event(type, { cancelable: true });
    Object.assign(event, { code, repeat: false, getModifierState: () => false });
    target.dispatchEvent(event);
    return event;
  };
  expect(dispatch("AltRight").defaultPrevented).toBe(true);
  target.dispatchEvent(new Event("blur"));
  await Promise.resolve();
  expect(send.mock.calls).toEqual([["dictation_down"], ["dictation_up"]]);
  setWindowHotkeysPaused(true);
  expect(dispatch("AltRight").defaultPrevented).toBe(false);
  dispatch("KeyV");
  dispatch("Escape");
  expect(send).toHaveBeenCalledTimes(2);
  setWindowHotkeysPaused(false);
  dispatch("AltRight", "keyup");
  expect(send).toHaveBeenCalledTimes(2);
  dispatch("AltRight");
  await Promise.resolve();
  expect(send).toHaveBeenLastCalledWith("dictation_down");
  remove();
  dispatch("Escape");
  expect(send).toHaveBeenCalledTimes(3);
});

test("losing focus during a question lets go of it", async () => {
  const target = new EventTarget();
  const send = vi.fn().mockResolvedValue(undefined);
  const remove = installWindowHotkeys(() => ({ ...defaults, agent_keys: ["F10"] }), target, send);
  const event = new Event("keydown", { cancelable: true });
  Object.assign(event, { code: "F10", repeat: false, getModifierState: () => false });
  target.dispatchEvent(event);
  target.dispatchEvent(new Event("blur"));
  await Promise.resolve();
  expect(send.mock.calls).toEqual([["agent_down"], ["agent_up"]]);
  remove();
});

test("recorder API pauses capture before IPC resolves and stays paused until resume resolves", async () => {
  const target = new EventTarget();
  const send = vi.fn().mockResolvedValue(undefined);
  const remove = installWindowHotkeys(() => defaults, target, send);
  const press = () => {
    const event = new Event("keydown", { cancelable: true });
    Object.assign(event, { code: "AltRight", repeat: false, getModifierState: () => false });
    target.dispatchEvent(event);
    return event.defaultPrevented;
  };
  let finish!: () => void;
  invoke.mockImplementation(() => new Promise<void>((resolve) => { finish = resolve; }));
  const pause = hotkeysPause(true);
  expect(press()).toBe(false);
  finish();
  await pause;
  expect(press()).toBe(false);
  const resume = hotkeysPause(false);
  expect(press()).toBe(false);
  expect(send).not.toHaveBeenCalled();
  finish();
  await resume;
  expect(press()).toBe(true);
  expect(send).toHaveBeenCalledWith("dictation_down");
  remove();
});

test("failed recorder IPC restores the previous capture pause", async () => {
  setWindowHotkeysPaused(false);
  invoke.mockRejectedValueOnce("service is unavailable");
  await expect(hotkeysPause(true)).rejects.toBe("service is unavailable");
  expect(setWindowHotkeysPaused(false)).toBe(false);
  setWindowHotkeysPaused(true);
  invoke.mockRejectedValueOnce("service is unavailable");
  await expect(hotkeysPause(false)).rejects.toBe("service is unavailable");
  expect(setWindowHotkeysPaused(false)).toBe(true);
});
