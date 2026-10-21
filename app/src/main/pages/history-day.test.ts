import { describe, expect, test } from "vitest";
import { dayBounds } from "../format";
import { historyQuery } from "./history";

describe("a day of the history", () => {
  test("is from its midnight to the next one, in local time", () => {
    const [from, to] = dayBounds("2026-10-14");
    expect(from).toBe(new Date(2026, 9, 14).getTime());
    expect(to).toBe(new Date(2026, 9, 15).getTime());
    expect(to - from).toBe(86_400_000); // Taipei has no clock changes
    expect(new Date(from).getHours()).toBe(0);
  });

  test("the last day of a month and of a year are followed by the first of the next", () => {
    expect(dayBounds("2026-10-31")[1]).toBe(new Date(2026, 10, 1).getTime());
    expect(dayBounds("2026-12-31")[1]).toBe(new Date(2027, 0, 1).getTime());
  });

  test("the history is asked for that stretch only", () => {
    const [from, to] = dayBounds("2026-10-14");
    expect(historyQuery("", "2026-10-14", null)).toEqual({ limit: 50, after_ms: from, before_ms: to });
  });

  test("a search keeps the day, and more of the day starts under the last item shown", () => {
    const [from] = dayBounds("2026-10-14");
    const cursor = from + 3_600_000;
    expect(historyQuery("meeting", "2026-10-14", cursor)).toEqual({ limit: 50, query: "meeting", after_ms: from, before_ms: cursor });
  });

  test("without a day the history is asked for as ever", () => {
    expect(historyQuery("", null, null)).toEqual({ limit: 50 });
    expect(historyQuery("hello", null, 1234)).toEqual({ limit: 50, query: "hello", before_ms: 1234 });
  });
});
