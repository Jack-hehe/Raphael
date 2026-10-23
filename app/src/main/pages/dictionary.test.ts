import { describe, expect, test } from "vitest";
import type { DictionaryEntry } from "../types";
import { isAuto, matches } from "./dictionary";

const entry = (word: string, source: DictionaryEntry["source"], misheard: string[] = []): DictionaryEntry => ({ id: 1, word, misheard, source });

describe("the dictionary filter", () => {
  test("a learned word counts as automatic; the other sources are typed", () => {
    expect(isAuto(entry("MCP", "learned"))).toBe(true);
    expect(isAuto(entry("Tauri", "manual"))).toBe(false);
    expect(isAuto(entry("Codex", "suggested"))).toBe(false);
  });

  test("the tabs and the search", () => {
    const auto = entry("Kokoro", "learned");
    const typed = entry("拉斐爾", "manual", ["拉菲爾"]);
    expect(matches(auto, "auto", "")).toBe(true);
    expect(matches(typed, "auto", "")).toBe(false);
    expect(matches(typed, "manual", "")).toBe(true);
    expect(matches(auto, "manual", "")).toBe(false);
    expect(matches(auto, "all", "koko")).toBe(true);
    expect(matches(typed, "all", "菲")).toBe(true);
    expect(matches(typed, "all", "xyz")).toBe(false);
  });
});
