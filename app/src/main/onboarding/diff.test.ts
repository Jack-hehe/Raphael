import { describe, expect, test } from "vitest";
import { changeCount, textDiff } from "./diff";

const joined = (pieces: { text: string }[]): string => pieces.map((p) => p.text).join("");

describe("textDiff", () => {
  test("the pieces put back together are the two texts", () => {
    const spoken = "我明天……你明天我們約下午兩點半，然後我們開會好不好？";
    const cleaned = "我們明天約下午三點開會，好不好？";
    const diff = textDiff(spoken, cleaned);
    expect(joined(diff.spoken)).toBe(spoken);
    expect(joined(diff.cleaned)).toBe(cleaned);
  });

  test("marks what was taken away in the spoken text, and what is new in the cleaned one", () => {
    const diff = textDiff("so uh let's meet", "so let's meet");
    expect(diff.spoken.filter((p) => p.changed).map((p) => p.text.trim()).filter(Boolean)).toEqual(["uh"]);
    expect(diff.cleaned.every((p) => !p.changed)).toBe(true);
  });

  test("a changed word is struck in one and marked in the other", () => {
    const diff = textDiff("at two thirty", "at three");
    expect(diff.spoken.some((p) => p.changed && p.text.includes("two"))).toBe(true);
    expect(diff.cleaned.some((p) => p.changed && p.text.includes("three"))).toBe(true);
  });

  test("the same text has nothing marked", () => {
    const diff = textDiff("明天開會。", "明天開會。");
    expect(diff.spoken).toEqual([{ text: "明天開會。", changed: false }]);
    expect(changeCount(diff)).toBe(0);
  });

  test("an empty side is all of the other", () => {
    expect(textDiff("", "好").cleaned).toEqual([{ text: "好", changed: true }]);
    expect(textDiff("好", "").spoken).toEqual([{ text: "好", changed: true }]);
  });

  test("neighbouring changes are one piece", () => {
    const diff = textDiff("abc 呃那個 def", "abc def");
    expect(diff.spoken.filter((p) => p.changed && p.text.trim())).toHaveLength(1);
    expect(changeCount(diff)).toBe(1);
  });

  test("a very long text is returned as it is instead of being compared", () => {
    const long = "字".repeat(800);
    const diff = textDiff(long, long + "。");
    expect(diff.spoken).toEqual([{ text: long, changed: false }]);
  });
});
