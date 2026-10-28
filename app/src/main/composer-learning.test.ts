// @vitest-environment happy-dom
import { afterEach, expect, test, vi } from "vitest";
const api = vi.hoisted(() => ({ core: vi.fn() }));
vi.mock("./api", () => api);
import { ComposerLearning, bindComposerLearning, composerCorrections, learnComposerCorrections } from "./composer-learning";
import { setLocale } from "./i18n";
const before = "Please use Open Mouse Bot", after = "Please use OpenMausBot";
const correction = [{ word: "OpenMausBot", misheard: "Open Mouse Bot" }];
afterEach(() => { vi.useRealTimers(); vi.clearAllMocks(); document.body.innerHTML = ""; });

test("a retyped proper noun in a short dictation is one whole correction", () => {
  expect(composerCorrections(before, after)).toEqual(correction);
  expect(composerCorrections("open maus bot", "OpenMausBot")).toEqual([{ word: "OpenMausBot", misheard: "open maus bot" }]);
  expect(composerCorrections("Allice", "Alice")).toEqual([{ word: "Alice", misheard: "Allice" }]);
  expect(composerCorrections("Please ask Allice to check today's release.", "Please ask Alice to check today's release.")).toEqual([{ word: "Alice", misheard: "Allice" }]);
});
// Built at run time: written out whole, GitHub push protection takes it for a real Stripe key.
const FAKE_STRIPE_KEY = ["sk", "live", "1234567890".repeat(4).slice(0, 39)].join("_");
test("append-only typing, one letter, whole rewrites and secret-like tokens are refused", () => {
  for (const [a, b] of [[before, before + " tomorrow"], ["a", "b"], ["Please cancel this meeting", "Send everyone the revised schedule"], ["Please use password", `Please use ${FAKE_STRIPE_KEY}`]]) {
    expect(composerCorrections(a, b)).toEqual([]);
  }
});
test("Chinese word boundaries and Japanese kana names keep the corrected term whole", () => {
  const text = "今天請用拉菲爾來整理昨天的會議紀錄並且寄給大家確認。";
  expect(composerCorrections(text, text.replace("拉菲爾", "拉斐爾"))).toEqual([{ word: "拉斐爾", misheard: "拉菲爾" }]);
  const japanese = "今日はラファエルに昨日の議事録を整理して送ってもらいます。";
  expect(composerCorrections(japanese, japanese.replace("ラファエル", "ラフィエル"))).toEqual([{ word: "ラフィエル", misheard: "ラファエル" }]);
});
test("only a known dictation seeds a draft; it is consumed once, cleared on navigation and expires", () => {
  vi.useFakeTimers();
  const input = document.createElement("textarea"), learning = bindComposerLearning(input);
  input.value = after;
  expect(learning.take(after)).toEqual([]);
  input.dispatchEvent(new CustomEvent("dictation-pasted", { detail: "another field" }));
  expect(learning.take(after)).toEqual([]);
  input.value = before;
  input.dispatchEvent(new CustomEvent("dictation-pasted", { detail: before }));
  expect(learning.take(after)).toEqual(correction);
  expect(learning.take(after)).toEqual([]);
  learning.seed(before); learning.clear(); expect(learning.take(after)).toEqual([]);
  learning.seed(before); vi.advanceTimersByTime(60_000); expect(learning.take(after)).toEqual([]);
  learning.seed(before + " ".repeat(20_000)); expect(learning.snapshot()).toBeUndefined();
});
test("changes to text typed outside the dictated span never become corrections", () => {
  const learning = new ComposerLearning();
  learning.seed("Typed Allice. " + before, before);
  expect(learning.take("Typed Alice. " + after)).toEqual([]);
  learning.seed("Typed Allice. " + before, before);
  expect(learning.take("Typed Allice. " + after)).toEqual(correction);
});
test("pending-send retry evidence is erased at the original deadline", () => {
  vi.useFakeTimers();
  const learning = new ComposerLearning(); learning.seed(before);
  const retry = learning.snapshot();
  expect(learning.take(after)).toEqual(correction);
  vi.advanceTimersByTime(30_000); learning.restore(retry);
  expect(learning.take(after)).toEqual(correction);
  vi.advanceTimersByTime(30_000);
  expect(retry?.text).toBe("");
  learning.restore(retry); expect(learning.take(after)).toEqual([]);
});
test("only core-confirmed learned terms receive one localized notice, without a confirmation", async () => {
  setLocale("en");
  api.core.mockResolvedValue({ learned: ["OpenMausBot"] });
  await learnComposerCorrections(correction);
  expect(api.core).toHaveBeenCalledWith("dictionary.learn", { corrections: correction });
  expect(document.querySelectorAll(".toast")).toHaveLength(1);
  expect(document.querySelector(".toast")?.textContent).toContain("Added OpenMausBot to the dictionary.");
  api.core.mockResolvedValue({ learned: [] }); await learnComposerCorrections(correction);
  api.core.mockRejectedValue(new Error("offline")); await learnComposerCorrections(correction);
  expect(document.querySelectorAll(".toast")).toHaveLength(1);
});

test.each([
  ["zh-TW", "已將「OpenMausBot」加入字典。"],
  ["ja", "「OpenMausBot」を辞書に追加しました。"],
] as const)("the %s notice renders real localized text", async (locale, message) => {
  setLocale(locale);
  api.core.mockResolvedValue({ learned: ["OpenMausBot"] });
  await learnComposerCorrections(correction);
  expect(document.querySelector(".toast")?.textContent).toBe(message);
});
