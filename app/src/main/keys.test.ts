import { describe, expect, test } from "vitest";
import { KeyRecorder, bindingClash, bindingFromCodes, bindingLabels, canonicalKey, keyLabel, sameBinding, validateCodes } from "./keys";

describe("labels", () => {
  test("the keys named in the brief", () => {
    expect(keyLabel("AltRight")).toBe("右 Alt");
    expect(keyLabel("AltLeft")).toBe("左 Alt");
    expect(keyLabel("Space")).toBe("Space");
    expect(keyLabel("KeyV")).toBe("V");
  });

  test("other keys", () => {
    expect(keyLabel("ControlLeft")).toBe("Ctrl");
    expect(keyLabel("ControlRight")).toBe("Ctrl");
    expect(keyLabel("ShiftLeft")).toBe("Shift");
    expect(keyLabel("MetaLeft")).toBe("Win");
    expect(keyLabel("Escape")).toBe("Esc");
    expect(keyLabel("Enter")).toBe("Enter");
    expect(keyLabel("Digit5")).toBe("5");
    expect(keyLabel("F9")).toBe("F9");
    expect(keyLabel("ArrowUp")).toBe("↑");
    expect(keyLabel("Numpad5")).toBe("Num 5");
    expect(keyLabel("Backquote")).toBe("`");
  });

  test("the spelling of the default bindings", () => {
    // the settings file says "AltRight+V", the recorder says "AltRight+KeyV"
    expect(bindingLabels("AltRight+V")).toEqual(["右 Alt", "V"]);
    expect(bindingLabels("AltRight+KeyV")).toEqual(["右 Alt", "V"]);
    expect(bindingLabels("AltRight")).toEqual(["右 Alt"]);
  });

  test("a key without a name is shown as it is", () => {
    expect(keyLabel("IntlBackslash")).toBe("IntlBackslash");
  });
});

describe("the binding that is saved", () => {
  test("modifiers first, then the key, whatever the order they were pressed in", () => {
    expect(bindingFromCodes(["KeyV", "AltRight"])).toBe("AltRight+KeyV");
    expect(bindingFromCodes(["AltRight"])).toBe("AltRight");
    expect(bindingFromCodes(["ControlLeft", "KeyD", "AltLeft"])).toBe("ControlLeft+AltLeft+KeyD");
  });
});

describe("which recordings are offered (the same rules as the Rust side)", () => {
  test.each([
    ["AltRight"],
    ["AltRight", "KeyV"],
    ["AltRight", "Space"],
    ["ControlLeft", "AltLeft", "KeyD"],
    ["MetaLeft", "KeyJ"],
    ["F9"],
    ["ShiftLeft", "F9"],
  ])("%s is fine", (...codes) => {
    expect(validateCodes(codes)).toBeNull();
  });

  test.each([
    ["KeyV"],
    ["Space"],
    ["Digit1"],
    ["AltLeft"],
    ["ShiftLeft"],
    ["ControlRight"],
    ["MetaLeft"],
    ["ShiftLeft", "KeyV"],
    ["ControlLeft", "ShiftLeft"],
    ["AltRight", "ShiftRight"],
    ["Escape"],
    ["AltRight", "Escape"],
    ["KeyA", "KeyB"],
    ["AltRight", "KeyA", "KeyB"],
  ])("%s is refused, and the reason is given", (...codes) => {
    const reason = validateCodes(codes);
    expect(reason).toEqual(expect.any(String));
    expect(reason).not.toBe("");
  });

  test.each([["AltLeft"], ["ShiftRight"], ["ControlLeft"], ["MetaRight"]])(
    "%s on its own is refused: only Right Alt may be alone",
    (...codes) => {
      expect(validateCodes(codes)).toBe(LONE_MODIFIER);
    },
  );
});

/** The same words as `validate_binding` in device_settings.rs. */
const LONE_MODIFIER = "只有右 Alt 可以單獨當快捷鍵；左 Alt、Ctrl、Shift、Win 要搭配其他按鍵";

describe("recording", () => {
  test("a combination is done when every key is up", () => {
    const r = new KeyRecorder();
    expect(r.keyDown("AltRight")).toEqual({ type: "progress", codes: ["AltRight"] });
    expect(r.keyDown("KeyV")).toEqual({ type: "progress", codes: ["AltRight", "KeyV"] });
    expect(r.keyUp("KeyV")).toEqual({ type: "progress", codes: ["AltRight", "KeyV"] });
    expect(r.keyUp("AltRight")).toEqual({ type: "done", codes: ["AltRight", "KeyV"] });
  });

  test("a key on its own", () => {
    const r = new KeyRecorder();
    r.keyDown("AltRight");
    expect(r.keyUp("AltRight")).toEqual({ type: "done", codes: ["AltRight"] });
  });

  test("Esc with nothing held cancels", () => {
    const r = new KeyRecorder();
    expect(r.keyDown("Escape")).toEqual({ type: "cancel" });
  });

  test("Esc while another key is held is a key like any other", () => {
    const r = new KeyRecorder();
    r.keyDown("AltRight");
    expect(r.keyDown("Escape")).toEqual({ type: "progress", codes: ["AltRight", "Escape"] });
  });

  test("auto-repeat changes nothing", () => {
    const r = new KeyRecorder();
    r.keyDown("AltRight");
    expect(r.keyDown("AltRight")).toEqual({ type: "progress", codes: ["AltRight"] });
    expect(r.keyUp("AltRight")).toEqual({ type: "done", codes: ["AltRight"] });
  });

  test("a key that was never pressed in the recording is ignored when it comes up", () => {
    // the Enter that pressed the button was pressed before the recording began
    const r = new KeyRecorder();
    expect(r.keyUp("Enter")).toBeNull();
    r.keyDown("AltRight");
    expect(r.keyUp("Enter")).toBeNull();
  });

  test("a key released and pressed again within the combination counts once", () => {
    const r = new KeyRecorder();
    r.keyDown("AltRight");
    r.keyDown("KeyV");
    r.keyUp("KeyV");
    r.keyDown("KeyV");
    expect(r.keyUp("KeyV")).toEqual({ type: "progress", codes: ["AltRight", "KeyV"] });
  });

  test("AltGr is Right Alt: the Ctrl that Windows adds for it is dropped", () => {
    const r = new KeyRecorder();
    r.keyDown("ControlLeft");
    expect(r.keyDown("AltRight", { altGraph: true })).toEqual({ type: "progress", codes: ["AltRight"] });
    // Windows lets go of the made-up Ctrl as well
    expect(r.keyUp("ControlLeft")).toBeNull();
    expect(r.keyUp("AltRight")).toEqual({ type: "done", codes: ["AltRight"] });
  });

  test("a Ctrl pressed on purpose stays when Right Alt is not AltGr", () => {
    const r = new KeyRecorder();
    r.keyDown("ControlLeft");
    expect(r.keyDown("AltRight")).toEqual({ type: "progress", codes: ["ControlLeft", "AltRight"] });
  });
});

describe("bindings that are the same", () => {
  test("the settings file's V and the recorder's KeyV are one key, whatever the case and order", () => {
    expect(canonicalKey("V")).toBe("keyv");
    expect(canonicalKey("KeyV")).toBe("keyv");
    expect(canonicalKey("1")).toBe("digit1");
    expect(canonicalKey("CtrlLeft")).toBe("controlleft");
    expect(sameBinding("AltRight+V", "AltRight+KeyV")).toBe(true);
    expect(sameBinding("KeyV+AltRight", "altright+v")).toBe(true);
    expect(sameBinding("AltRight", "AltRight+Space")).toBe(false);
    expect(sameBinding("ControlLeft+Space", "ControlRight+Space")).toBe(false);
  });

  test("a clash names the action that has the binding", () => {
    const taken = [["Dictate", ["AltRight"]], ["Paste last", ["AltRight+V"]]] as const;
    expect(bindingClash("AltRight+KeyV", taken)).toBe("Paste last");
    expect(bindingClash("AltRight", taken)).toBe("Dictate");
    expect(bindingClash("AltRight+Space", taken)).toBeNull();
    expect(bindingClash("F9", [])).toBeNull();
  });
});
