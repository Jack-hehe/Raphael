/// <reference types="node" />
import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, describe, expect, test } from "vitest";
import { setLocale } from "../i18n";
import { COMPOSER_PLACEHOLDER, TYPED_MAX_CHARS, greetingLine, sendsOnEnter, typedQuestion } from "./agent";

afterEach(() => setLocale("zh-TW"));

describe("the composer", () => {
  test("the placeholder is the same words in every language of the window", () => {
    expect(COMPOSER_PLACEHOLDER).toBe("Ask Raphael");
  });

  test("Enter sends; Shift+Enter is a new line; nothing is sent while an input method composes", () => {
    const key = (over: Partial<KeyboardEvent>) => ({ key: "Enter", shiftKey: false, isComposing: false, keyCode: 13, ...over });
    expect(sendsOnEnter(key({}))).toBe(true);
    expect(sendsOnEnter(key({ shiftKey: true }))).toBe(false);
    expect(sendsOnEnter(key({ isComposing: true }))).toBe(false);
    expect(sendsOnEnter(key({ keyCode: 229 }))).toBe(false); // the IME's own Enter
    expect(sendsOnEnter(key({ key: "a" }))).toBe(false);
  });

  test("a question is the words, then an attached file's name in 「」 and its text; nothing or too much is no question", () => {
    expect(typedQuestion("  明天會下雨嗎？ ", null)).toBe("明天會下雨嗎？");
    expect(typedQuestion("幫我整理", { name: "notes.md", text: "# 待辦\n- 買牛奶\n" })).toBe("幫我整理\n\n「notes.md」\n# 待辦\n- 買牛奶");
    expect(typedQuestion("", { name: "a.txt", text: "x" })).toBe("「a.txt」\nx");
    expect(typedQuestion(" \n ", null)).toBeNull();
    expect(typedQuestion("字".repeat(TYPED_MAX_CHARS), null)).not.toBeNull();
    expect(typedQuestion("字".repeat(TYPED_MAX_CHARS + 1), null)).toBeNull();
    // the app's own limit is the same (commands.rs)
    const commands = readFileSync(path.resolve(import.meta.dirname, "..", "..", "..", "src-tauri", "src", "commands.rs"), "utf8");
    expect(commands).toContain(`pub const TYPED_MAX_CHARS: usize = ${TYPED_MAX_CHARS.toLocaleString("en").replace(/,/g, "_")};`);
  });
});

describe("the empty page", () => {
  test("Raphael's line is the overlay's greeting to a question, in the language of the window, without its last stop", () => {
    const notices = readFileSync(path.resolve(import.meta.dirname, "..", "..", "..", "src-tauri", "src", "notices.rs"), "utf8");
    const block = /pub const GREETING: Notice = Notice \{([^}]*)\}/.exec(notices)![1];
    const greeting = (field: string): string => new RegExp(`${field}: "([^"]+)"`).exec(block)![1];
    for (const [locale, field] of [["zh-TW", "zh"], ["en", "en"], ["ja", "ja"]] as const) {
      setLocale(locale);
      expect(greetingLine(), locale).toBe(greeting(field).replace(/[。.]》$/, "》"));
    }
    setLocale("zh-TW");
    expect(greetingLine()).toBe("《告。主人，請下達指示》");
  });
});
