import { describe, expect, test } from "vitest";
import type { ModelPhase } from "../models";
import { HEARD_LEVEL, HEARD_MS, heardAfter, limitAction, lookUp, modelPercent } from "./helpers";

describe("limitAction", () => {
  test("the limit as it is stays as it is", () => {
    expect(limitAction("10", 10)).toEqual({ kind: "keep" });
    expect(limitAction(" 10.0 ", 10)).toEqual({ kind: "keep" });
  });

  test("another number is saved", () => {
    expect(limitAction("25", 10)).toEqual({ kind: "save", value: 25 });
    expect(limitAction("0", 10)).toEqual({ kind: "save", value: 0 });
  });

  test("what is not a number of 0 or more is refused", () => {
    for (const text of ["", "abc", "-5", "1e999"]) expect(limitAction(text, 10)).toEqual({ kind: "invalid" });
  });
});

describe("heardAfter", () => {
  test("a level above the line has to last before it counts", () => {
    let state = heardAfter(null, HEARD_LEVEL + 0.1, 1_000);
    expect(state).toEqual({ since: 1_000, heard: false });
    state = heardAfter(state.since, 0.4, 1_000 + HEARD_MS - 1);
    expect(state.heard).toBe(false);
    state = heardAfter(state.since, 0.4, 1_000 + HEARD_MS);
    expect(state).toEqual({ since: 1_000, heard: true });
  });

  test("a reading under the line starts over", () => {
    const state = heardAfter(1_000, HEARD_LEVEL, 1_200);
    expect(state).toEqual({ since: null, heard: false });
    expect(heardAfter(state.since, 0.5, 1_250)).toEqual({ since: 1_250, heard: false });
  });
});

describe("modelPercent", () => {
  test("is the share downloaded, and full once fetched", () => {
    expect(modelPercent({ kind: "downloading", done: 250, total: 1000 })).toBe(25);
    expect(modelPercent({ kind: "installing" })).toBe(100);
    expect(modelPercent({ kind: "ready" })).toBe(100);
  });

  test("is unknown before the sizes are", () => {
    const unknown: ModelPhase[] = [{ kind: "idle" }, { kind: "checking" }, { kind: "downloading", done: 0, total: 0 }, { kind: "failed", reason: "x" }];
    for (const phase of unknown) expect(modelPercent(phase)).toBeNull();
  });
});

describe("lookUp", () => {
  const waits: number[] = [];
  const wait = async (ms: number): Promise<void> => void waits.push(ms);

  test("answers at once when the first look has it", async () => {
    waits.length = 0;
    expect(await lookUp(async () => 5, (n) => n > 3, wait)).toBe(5);
    expect(waits).toEqual([]);
  });

  test("looks again after each pause until it is there", async () => {
    waits.length = 0;
    const seen = [1, 1, 9];
    expect(await lookUp(async () => seen.shift()!, (n) => n > 3, wait)).toBe(9);
    expect(waits).toEqual([700, 1500]);
  });

  test("an old answer counts as not there yet, and the last look gives up", async () => {
    waits.length = 0;
    expect(await lookUp(async () => 1, (n) => n > 3, wait)).toBeNull();
    expect(waits).toEqual([700, 1500]);
  });

  test("a core that does not answer is asked again, and never throws", async () => {
    waits.length = 0;
    let calls = 0;
    const value = await lookUp(
      async () => {
        if (++calls < 3) throw new Error("core is not running");
        return 7;
      },
      () => true,
      wait,
    );
    expect(value).toBe(7);
    expect(await lookUp(async () => { throw new Error("down"); }, () => true, wait)).toBeNull();
  });
});
