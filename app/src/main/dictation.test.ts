import { describe, expect, test } from "vitest";
import {
  DICTATION_MODES,
  DEFAULT_DICTATION_MODE,
  type DictationMode,
  homeHint,
  historyEmptyHint,
  isDictationMode,
  keylineAction,
  modeDescription,
  tryBoxText,
} from "./dictation";
import { esc, sel } from "./ui";

const MODES: DictationMode[] = ["toggle", "hold"];

describe("the choices", () => {
  test("two modes, toggle first, with the words of the settings page", () => {
    expect(DICTATION_MODES).toEqual([
      { value: "toggle", label: "按一下開始／結束", desc: modeDescription("toggle") },
      { value: "hold", label: "按住說話", desc: modeDescription("hold") },
    ]);
  });

  test("toggle is the default, like device_settings.rs", () => {
    expect(DEFAULT_DICTATION_MODE).toBe("toggle");
  });

  test("only the two modes are modes", () => {
    expect(isDictationMode("toggle")).toBe(true);
    expect(isDictationMode("hold")).toBe(true);
    for (const other of ["tap", "auto", "Hold", "", undefined, null, 1, true, {}]) {
      expect(isDictationMode(other)).toBe(false);
    }
  });

  test("the dropdown shows only the current mode", () => {
    const html = sel(DICTATION_MODES, "hold", "聽寫方式", ' data-k="mode"');
    expect(html).toContain('data-k="mode"');
    expect(html).toContain("<span>按住說話</span>");
    expect(html).not.toContain("按一下開始／結束");
  });
});

/**
 * Each text explains the mode it is for, not the other one. `ends` is false for a text that only
 * says how to start (the empty history), which has nothing about letting go.
 */
function explainsOnlyItsMode(mode: DictationMode, text: string, ends = true): void {
  expect(text).not.toContain("輕按");
  if (mode === "toggle") {
    expect(text).toContain("按一下");
    expect(text).not.toContain("按住");
    expect(text).not.toContain("放開");
  } else {
    expect(text).toContain("按住");
    if (ends) expect(text).toContain("放開");
    expect(text).not.toContain("按一下");
  }
}

describe("the copy that explains the key", () => {
  test("the settings row says what the chosen mode does", () => {
    for (const mode of MODES) explainsOnlyItsMode(mode, modeDescription(mode));
  });

  test("the home hint follows the mode", () => {
    for (const mode of MODES) explainsOnlyItsMode(mode, homeHint(mode));
    expect(homeHint("toggle")).toBe("按一下開始，再按一下貼上");
    expect(homeHint("hold")).toBe("按住說話，放開就貼上");
  });

  test("the empty history names the key and follows the mode", () => {
    for (const mode of MODES) {
      const text = historyEmptyHint(mode, "右 Alt");
      explainsOnlyItsMode(mode, text, false);
      expect(text).toContain("右 Alt");
      expect(text).toContain("結果會出現在這裡");
    }
    expect(historyEmptyHint("toggle", "右 Alt")).toBe("按一下右 Alt 開始說話，結果會出現在這裡。");
    expect(historyEmptyHint("hold", "右 Alt")).toBe("按住右 Alt 說話，結果會出現在這裡。");
  });

  test("the first-run guide's try box names the keys and follows the mode", () => {
    for (const mode of MODES) {
      const text = tryBoxText(mode, "右 Alt 或 F9");
      explainsOnlyItsMode(mode, text);
      expect(text).toContain("右 Alt 或 F9");
      expect(text).toContain("貼上整理好的文字");
    }
    expect(tryBoxText("toggle", "右 Alt")).toBe(
      "把游標放在下面的框裡，按一下右 Alt 開始說話，再按一下就會貼上整理好的文字。之後在任何程式裡都一樣。",
    );
    expect(tryBoxText("hold", "右 Alt")).toBe(
      "把游標放在下面的框裡，按住右 Alt 說話，放開就會貼上整理好的文字。之後在任何程式裡都一樣。",
    );
  });

  test("the guide's key line says how the key is used", () => {
    for (const mode of MODES) explainsOnlyItsMode(mode, keylineAction(mode));
    expect(keylineAction("toggle")).toBe("按一下開始聽寫，再按一下結束");
    expect(keylineAction("hold")).toBe("按住聽寫，放開結束");
  });

  test("the texts are plain: a page escapes them where it puts them in HTML", () => {
    const tricky = '<b>"&</b>';
    expect(esc(tryBoxText("toggle", tricky))).not.toContain("<b>");
    expect(esc(historyEmptyHint("hold", tricky))).toContain("&lt;b&gt;");
    // and the texts themselves have nothing in them that HTML would need escaped
    for (const mode of MODES) {
      for (const text of [modeDescription(mode), homeHint(mode), keylineAction(mode)]) {
        expect(esc(text)).toBe(text);
      }
    }
  });
});
