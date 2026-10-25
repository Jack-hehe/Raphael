/// <reference types="node" />
import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, describe, expect, test } from "vitest";
import { setLocale } from "../i18n";
import { TYPED_MAX_CHARS, draftText, greetingLine, sendsOnEnter, typedQuestion } from "./agent";

afterEach(() => setLocale("zh-TW"));

describe("the composer", () => {
  test("Enter sends; Shift+Enter is a new line; nothing is sent while an input method composes", () => {
    const key = (over: Partial<KeyboardEvent>) => ({ key: "Enter", shiftKey: false, isComposing: false, keyCode: 13, ...over });
    expect(sendsOnEnter(key({}))).toBe(true);
    expect(sendsOnEnter(key({ shiftKey: true }))).toBe(false);
    expect(sendsOnEnter(key({ isComposing: true }))).toBe(false);
    expect(sendsOnEnter(key({ keyCode: 229 }))).toBe(false); // the IME's own Enter
    expect(sendsOnEnter(key({ key: "a" }))).toBe(false);
  });

  test("question words stay separate from files; empty drafts and overly long words cannot send", () => {
    expect(typedQuestion("  明天會下雨嗎？ ")).toBe("明天會下雨嗎？");
    expect(typedQuestion("幫我整理", true)).toBe("幫我整理");
    expect(typedQuestion("", true)).toBe("");
    expect(typedQuestion(" \n ")).toBeNull();
    expect(typedQuestion("字".repeat(TYPED_MAX_CHARS))).not.toBeNull();
    expect(typedQuestion("字".repeat(TYPED_MAX_CHARS + 1))).toBeNull();
    // the app's own limit is the same (commands.rs)
    const commands = readFileSync(path.resolve(import.meta.dirname, "..", "..", "..", "src-tauri", "src", "commands.rs"), "utf8");
    expect(commands).toContain(`pub const TYPED_MAX_CHARS: usize = ${TYPED_MAX_CHARS.toLocaleString("en").replace(/,/g, "_")};`);
  });
});

describe("a draft from 「試試看」", () => {
  test("is the words after `draft:`, cut to what a question may hold; a tab or nothing is no draft", () => {
    expect(draftText("draft:幫我列出 GitHub 上指派給我的 issue")).toBe("幫我列出 GitHub 上指派給我的 issue");
    expect(draftText("draft:a: b")).toBe("a: b");
    expect(draftText("draft:")).toBe("");
    expect(draftText(`draft:${"字".repeat(TYPED_MAX_CHARS + 5)}`)).toHaveLength(TYPED_MAX_CHARS);
    expect(draftText("agent")).toBeNull();
    expect(draftText(undefined)).toBeNull();
  });

  test("the settings hand it over as the page's argument, and the page puts it in the field without sending it", () => {
    const settings = readFileSync(path.resolve(import.meta.dirname, "settings.ts"), "utf8");
    expect(settings).toContain('tryPrompt: (text) => app.go("agent", `draft:${text}`)');
    const page = readFileSync(path.resolve(import.meta.dirname, "agent.ts"), "utf8");
    const show = /show\(arg\) \{([\s\S]*?)\n    \},/.exec(page)![1];
    expect(show).toContain("putDraft(draft)");
    expect(show).not.toMatch(/send\(|requestSubmit/);
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
