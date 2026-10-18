import { describe, expect, it } from "vitest";
import {
  cleanupPriceShort,
  cleanupPriceText,
  formatPerMinute,
  formatPerMtok,
  priceOf,
  pricingNote,
  sttPriceShort,
  sttPriceText,
} from "./pricing";
import type { Pricing, SttPrice } from "./types";

const llm = (input: number | null, output: number | null) => ({ id: "a/b", name: "B", input_per_mtok: input, output_per_mtok: output });
const stt = (perMinute: number | null, input: number | null = null, output: number | null = null): SttPrice => ({
  id: "s/t",
  name: "T",
  per_minute: perMinute,
  input_per_mtok: input,
  output_per_mtok: output,
});

describe("formatPerMtok", () => {
  it("keeps two decimals for small round prices", () => {
    expect(formatPerMtok(0.1)).toBe("$0.10");
    expect(formatPerMtok(0.5)).toBe("$0.50");
    expect(formatPerMtok(2.5)).toBe("$2.50");
    expect(formatPerMtok(1)).toBe("$1.00");
  });
  it("shows up to three significant digits", () => {
    expect(formatPerMtok(0.075)).toBe("$0.075");
    expect(formatPerMtok(0.0375)).toBe("$0.0375");
    expect(formatPerMtok(1.234)).toBe("$1.23");
    expect(formatPerMtok(0.123456)).toBe("$0.123");
  });
  it("drops decimals that say nothing for larger prices", () => {
    expect(formatPerMtok(15)).toBe("$15");
    expect(formatPerMtok(12.5)).toBe("$12.5");
    expect(formatPerMtok(120.4)).toBe("$120");
  });
  it("shows a free model as $0", () => {
    expect(formatPerMtok(0)).toBe("$0");
  });
});

describe("formatPerMinute", () => {
  it("has four decimals under a cent", () => {
    expect(formatPerMinute(0.0019)).toBe("$0.0019");
    expect(formatPerMinute(0.006)).toBe("$0.0060");
    expect(formatPerMinute(0.0018)).toBe("$0.0018");
  });
  it("keeps two significant digits under a tenth of a cent", () => {
    expect(formatPerMinute(0.00045)).toBe("$0.00045");
    expect(formatPerMinute(0.0001998)).toBe("$0.00020");
  });
  it("from a cent on, reads like the token prices", () => {
    expect(formatPerMinute(0.01)).toBe("$0.01");
    expect(formatPerMinute(0.024)).toBe("$0.024");
    expect(formatPerMinute(0)).toBe("$0");
  });
});

describe("cleanup prices", () => {
  it("names input and output", () => {
    expect(cleanupPriceText(llm(0.1, 0.5))).toBe("輸入 $0.10 / 輸出 $0.50 每百萬 token");
    expect(cleanupPriceShort(llm(0.1, 0.5))).toBe("$0.10 / $0.50");
  });
  it("marks a missing half and says when nothing is known", () => {
    expect(cleanupPriceText(llm(0.1, null))).toBe("輸入 $0.10 / 輸出 ? 每百萬 token");
    expect(cleanupPriceText(llm(null, null))).toBe("價格未知");
    expect(cleanupPriceText(undefined)).toBe("價格未知");
    expect(cleanupPriceShort(undefined)).toBe("價格未知");
  });
});

describe("speech-to-text prices", () => {
  it("prefers the price per minute", () => {
    expect(sttPriceText(stt(0.0019, 1, 2))).toBe("每分鐘 $0.0019");
    expect(sttPriceShort(stt(0.0019))).toBe("每分鐘 $0.0019");
  });
  it("falls back to the token prices", () => {
    expect(sttPriceText(stt(null, 2.5, 10))).toBe("輸入 $2.50 / 輸出 $10 每百萬 token");
    expect(sttPriceShort(stt(null, 2.5, 10))).toBe("$2.50 / $10 每百萬 token");
  });
  it("says when nothing is known", () => {
    expect(sttPriceText(stt(null))).toBe("價格未知");
    expect(sttPriceShort(undefined)).toBe("價格未知");
  });
});

describe("pricingNote", () => {
  const now = new Date(2026, 9, 18, 12).getTime();
  const base: Pricing = { cleanup: [], stt: [], fetched_at_ms: now - 3600_000, source: "live" };
  it("says the prices are fresh", () => {
    expect(pricingNote(base, now)).toBe("價格來自 OpenRouter，今天更新。實際花費依 OpenRouter 選用的供應商而定。");
  });
  it("dates a cached copy", () => {
    const fetched = new Date(2026, 9, 15, 9).getTime();
    expect(pricingNote({ ...base, source: "cached", fetched_at_ms: fetched }, now)).toBe(
      "價格是 10 月 15 日從 OpenRouter 取得的，現在連不上，先顯示這份。實際花費依 OpenRouter 選用的供應商而定。",
    );
    expect(pricingNote({ ...base, source: "cached", fetched_at_ms: now - 86_400_000 }, now)).toMatch(/^價格是昨天從 OpenRouter/);
    expect(pricingNote({ ...base, source: "cached", fetched_at_ms: null }, now)).toContain("之前");
  });
  it("warns that built-in prices may be old", () => {
    expect(pricingNote({ ...base, source: "builtin", fetched_at_ms: null }, now)).toContain("內建");
    expect(pricingNote(base, now)).toContain("供應商");
  });
});

describe("priceOf", () => {
  it("finds a model by id", () => {
    expect(priceOf([llm(1, 2)], "a/b")?.input_per_mtok).toBe(1);
    expect(priceOf([llm(1, 2)], "x/y")).toBeUndefined();
    expect(priceOf(undefined, "a/b")).toBeUndefined();
  });
});
