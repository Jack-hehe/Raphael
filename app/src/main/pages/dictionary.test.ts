import { describe, expect, test } from "vitest";
import type { DictionaryEntry } from "../types";
import { setLocale } from "../i18n";
import { sel } from "../ui";
import { DICTIONARY_FILTERS, isAuto, matches } from "./dictionary";

const entry = (word: string, source?: DictionaryEntry["source"], misheard: string[] = []): DictionaryEntry => ({ id: 1, word, misheard, source });

describe("the dictionary filter", () => {
  test("learned and auto sources count as automatic; a missing source is typed", () => {
    expect(isAuto(entry("MCP", "learned"))).toBe(true);
    expect(isAuto(entry("MCP", "auto"))).toBe(true);
    expect(isAuto(entry("Tauri", "manual"))).toBe(false);
    expect(isAuto(entry("Codex", "suggested"))).toBe(false);
    expect(isAuto(entry("n8n"))).toBe(false);
  });

  test.each(["learned", "auto"] as const)("the tabs and the search for source %s", (source) => {
    const auto = entry("Kokoro", source);
    const typed = entry("拉斐爾", undefined, ["拉菲爾"]);
    expect(matches(auto, "auto", "")).toBe(true);
    expect(matches(typed, "auto", "")).toBe(false);
    expect(matches(typed, "manual", "")).toBe(true);
    expect(matches(auto, "manual", "")).toBe(false);
    expect(matches(auto, "all", "koko")).toBe(true);
    expect(matches(typed, "all", "菲")).toBe(true);
    expect(matches(typed, "all", "xyz")).toBe(false);
  });
});

describe("the dictionary filter dropdown", () => {
  test("offers all, learned and typed, in that order, as menu choices", () => {
    expect(DICTIONARY_FILTERS.map((f) => f.value)).toEqual(["all", "auto", "manual"]);
  });

  test("shows the chosen filter's label in the current language, never a row of buttons", () => {
    setLocale("en");
    const html = sel(DICTIONARY_FILTERS, "all", "Filter", ' data-k="filter"');
    expect(html).toContain('class="sel"');
    expect(html).toContain('data-k="filter"');
    expect(html).toContain("<span>All</span>");
    expect(html).not.toContain("aria-pressed");
    setLocale("zh-TW");
  });
});
