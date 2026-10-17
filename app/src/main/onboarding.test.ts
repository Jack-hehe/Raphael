import { describe, expect, test } from "vitest";
import {
  STEP_COUNT,
  type Step,
  canAdvance,
  dictationKeysText,
  limitAction,
  micChoices,
  next,
  prev,
  primaryLabel,
  stepCaption,
  tryTiming,
} from "./onboarding";

const none = { keySaved: false, skipKey: false };

test("key step needs a saved key or an explicit skip", () => {
  expect(next(1, { keySaved: false, skipKey: false })).toBe(1);
  expect(next(1, { keySaved: true, skipKey: false })).toBe(2);
  expect(next(1, { keySaved: false, skipKey: true })).toBe(2);
  expect(next(3, { keySaved: true, skipKey: false })).toBe("done");
});

describe("next", () => {
  test("the first step goes on to the key step without asking for anything", () => {
    expect(next(0, none)).toBe(1);
  });

  test("the microphone step goes on to the try step whatever the key did", () => {
    expect(next(2, none)).toBe(3);
    expect(next(2, { keySaved: true, skipKey: true })).toBe(3);
  });

  test("the last step finishes", () => {
    expect(next(3, none)).toBe("done");
  });

  test("both a saved key and a skip are fine together", () => {
    expect(next(1, { keySaved: true, skipKey: true })).toBe(2);
  });
});

describe("prev", () => {
  test("goes back one step and stops at the first", () => {
    expect(prev(3)).toBe(2);
    expect(prev(2)).toBe(1);
    expect(prev(1)).toBe(0);
    expect(prev(0)).toBe(0);
  });
});

describe("canAdvance", () => {
  test("is false only where next stays on the step", () => {
    expect(canAdvance(1, none)).toBe(false);
    expect(canAdvance(1, { keySaved: true, skipKey: false })).toBe(true);
    expect(canAdvance(1, { keySaved: false, skipKey: true })).toBe(true);
    for (const step of [0, 2, 3] as Step[]) expect(canAdvance(step, none)).toBe(true);
  });
});

describe("the footer", () => {
  test("counts the steps from 1 of 4", () => {
    expect(STEP_COUNT).toBe(4);
    expect(stepCaption(0)).toBe("第 1 步，共 4 步");
    expect(stepCaption(3)).toBe("第 4 步，共 4 步");
  });

  test("the main button says what it does", () => {
    expect([0, 1, 2, 3].map((step) => primaryLabel(step as Step))).toEqual(["開始", "下一步", "下一步", "完成"]);
  });
});

describe("limitAction", () => {
  test("the limit as it is stays as it is", () => {
    expect(limitAction("10", 10)).toEqual({ kind: "keep" });
    expect(limitAction(" 10.0 ", 10)).toEqual({ kind: "keep" });
  });

  test("another number is saved", () => {
    expect(limitAction("25", 10)).toEqual({ kind: "save", value: 25 });
    expect(limitAction("7.5", 10)).toEqual({ kind: "save", value: 7.5 });
    expect(limitAction("0", 10)).toEqual({ kind: "save", value: 0 });
  });

  test("what is not a number of 0 or more is refused", () => {
    for (const text of ["", "  ", "abc", "-1", "1e3", "10$", "1,5"]) {
      expect(limitAction(text, 10)).toEqual({ kind: "invalid" });
    }
  });
});

describe("micChoices", () => {
  const devices = {
    devices: [
      { index: 0, name: "麥克風陣列（Realtek(R) Audio）", is_default: true },
      { index: 1, name: "耳機麥克風", is_default: false },
    ],
    selected: null,
  };

  test("the automatic choice comes first, then each microphone", () => {
    expect(micChoices(devices, null)).toEqual([
      { value: "", label: "自動偵測" },
      { value: "麥克風陣列（Realtek(R) Audio）", label: "麥克風陣列（Realtek(R) Audio）" },
      { value: "耳機麥克風", label: "耳機麥克風" },
    ]);
  });

  test("a saved microphone that is not plugged in is still offered, marked", () => {
    const choices = micChoices(devices, "USB 麥克風");
    expect(choices[choices.length - 1]).toEqual({ value: "USB 麥克風", label: "USB 麥克風（未連接）" });
    expect(choices).toHaveLength(4);
  });

  test("without a list of microphones only the automatic choice is there", () => {
    expect(micChoices(null, null)).toEqual([{ value: "", label: "自動偵測" }]);
  });
});

describe("tryTiming", () => {
  const last = { text: "你好", created_at_ms: 5_000, target_app: null, stt_ms: 800, llm_ms: 320 };

  test("tells how long a dictation made since the step opened took", () => {
    expect(tryTiming(last, 4_000)).toBe("辨識與整理花了 1.12 秒");
  });

  test("says nothing about a dictation from before the step opened", () => {
    expect(tryTiming(last, 6_000)).toBeNull();
  });

  test("says nothing when there is no dictation", () => {
    expect(tryTiming(null, 0)).toBeNull();
  });
});

describe("dictationKeysText", () => {
  test("names the keys the way the settings do", () => {
    expect(dictationKeysText(["AltRight"])).toBe("右 Alt");
    expect(dictationKeysText(["ControlLeft+AltLeft+KeyD"])).toBe("Ctrl + 左 Alt + D");
  });

  test("two hotkeys are told apart with 或", () => {
    expect(dictationKeysText(["AltRight", "F9"])).toBe("右 Alt 或 F9");
  });
});
