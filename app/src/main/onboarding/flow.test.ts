import { describe, expect, test } from "vitest";
import { ABILITIES, type AbilityId } from "./abilities";
import {
  type FlowEvent,
  type FlowState,
  type Readiness,
  canAdvance,
  canSkip,
  currentLesson,
  initialState,
  lessonsFor,
  lessonsOf,
  needsFor,
  reduce,
  segments,
  stepsFor,
} from "./flow";

const NOTHING: Readiness = { key: false, connections: false, telegram: false };
const run = (state: FlowState, ...events: FlowEvent[]): FlowState => events.reduce(reduce, state);
const first = (ready: Partial<Readiness> = {}): FlowState => initialState("first", { ...NOTHING, ...ready });
/** The guide on the checklist with exactly these abilities ticked. */
function ticking(ids: AbilityId[], ready: Partial<Readiness> = {}): FlowState {
  let state = run(first(ready), { type: "NEXT" });
  for (const id of state.abilities) state = reduce(state, { type: "TICK", id });
  for (const id of ids) state = reduce(state, { type: "TICK", id });
  return state;
}

describe("the registry", () => {
  test("every lesson belongs to an ability and the five of them are all taught", () => {
    expect(ABILITIES.flatMap((a) => a.lessons).sort()).toEqual(["act", "ask", "cleanup", "dictate", "key"]);
  });
});

describe("abilities to steps", () => {
  test("the everyday abilities are ticked on a first run, and the extras are not", () => {
    expect(first().abilities).toEqual(["dictate", "cleanup", "ask", "act"]);
  });

  test("they need a key and a microphone, and then practice", () => {
    expect(stepsFor(first())).toEqual(["welcome", "abilities", "key", "mic", "practice", "done"]);
  });

  test("a setup step comes only for what a ticked ability needs, in a fixed order", () => {
    expect(needsFor(["phone", "connect", "dictate"])).toEqual(["key", "mic", "connections", "telegram"]);
    expect(needsFor(["dictate"])).toEqual(["mic"]);
    expect(needsFor([])).toEqual([]);
  });

  test("connecting a service adds its step and no lesson", () => {
    const state = ticking(["connect"]);
    expect(stepsFor(state)).toEqual(["welcome", "abilities", "key", "connections", "done"]);
    expect(lessonsOf(state)).toEqual([]);
  });

  test("nothing ticked is a guide of two screens and the end", () => {
    expect(stepsFor(ticking([]))).toEqual(["welcome", "abilities", "done"]);
  });

  test("dictation alone teaches the key and the first dictation, without asking for a key", () => {
    const state = ticking(["dictate"]);
    expect(stepsFor(state)).toEqual(["welcome", "abilities", "mic", "practice", "done"]);
    expect(lessonsOf(state)).toEqual(["key", "dictate"]);
  });
});

describe("abilities to lessons", () => {
  test("with a key all five, in order", () => {
    expect(lessonsFor(first().abilities, true)).toEqual(["key", "dictate", "cleanup", "ask", "act"]);
  });

  test("without a key the lessons that use Raphael's AI are left out", () => {
    expect(lessonsFor(first().abilities, false)).toEqual(["key", "dictate"]);
  });

  test("an unticked ability takes its lesson away", () => {
    expect(lessonsFor(["dictate", "act"], true)).toEqual(["key", "dictate", "act"]);
  });

  test("a skipped key hides the lessons, a saved one brings them back", () => {
    let state = run(first(), { type: "NEXT" }, { type: "NEXT" });
    expect(state.step).toBe("key");
    state = reduce(state, { type: "SKIP" });
    expect(lessonsOf(state)).toEqual(["key", "dictate"]);
    state = reduce(state, { type: "SETUP", need: "key", state: "done" });
    expect(lessonsOf(state)).toHaveLength(5);
  });
});

describe("going on, back and skipping", () => {
  test("the welcome and the checklist go on freely", () => {
    const state = first();
    expect(canAdvance(state)).toBe(true);
    expect(run(state, { type: "NEXT" }).step).toBe("abilities");
  });

  test("the key step waits for a key or for 「跳過」", () => {
    const state = run(first(), { type: "NEXT" }, { type: "NEXT" });
    expect(state.step).toBe("key");
    expect(canAdvance(state)).toBe(false);
    expect(canSkip(state)).toBe(true);
    expect(reduce(state, { type: "NEXT" })).toBe(state);
    expect(run(state, { type: "SETUP", need: "key", state: "done" }, { type: "NEXT" }).step).toBe("mic");
    const skipped = reduce(state, { type: "SKIP" });
    expect(skipped.step).toBe("mic");
    expect(skipped.setup.key).toBe("skipped");
  });

  test("the microphone step never blocks", () => {
    const state = run(first({ key: true }), { type: "NEXT" }, { type: "NEXT" }, { type: "NEXT" });
    expect(state.step).toBe("mic");
    expect(canAdvance(state)).toBe(true);
    expect(canSkip(state)).toBe(false);
  });

  test("each lesson waits for its own success, and 「跳過」 leaves it undone but goes on", () => {
    let state = run(first({ key: true }), { type: "NEXT" }, { type: "NEXT" }, { type: "NEXT" }, { type: "NEXT" });
    expect(state.step).toBe("practice");
    expect(currentLesson(state)).toBe("key");
    expect(canAdvance(state)).toBe(false);
    expect(canSkip(state)).toBe(true);
    state = reduce(state, { type: "SKIP" });
    expect(currentLesson(state)).toBe("dictate");
    expect(state.lessons.key).toBe("skipped");
    state = run(state, { type: "LESSON_OK" });
    expect(state.lessons.dictate).toBe("ok");
    expect(canSkip(state)).toBe(false);
    state = reduce(state, { type: "NEXT" });
    expect(currentLesson(state)).toBe("cleanup");
  });

  test("the last lesson goes on to the end, and the end stays", () => {
    let state = ticking(["dictate"], { key: true });
    state = run(state, { type: "NEXT" }, { type: "NEXT" });
    expect(state.step).toBe("practice");
    state = run(state, { type: "LESSON_OK" }, { type: "NEXT" }, { type: "LESSON_OK" }, { type: "NEXT" });
    expect(state.step).toBe("done");
    expect(reduce(state, { type: "NEXT" })).toBe(state);
  });

  test("back goes through the lessons, then to the step before, and stops at the welcome", () => {
    let state = run(first({ key: true }), ...Array(4).fill({ type: "NEXT" }) as FlowEvent[]);
    state = run(state, { type: "SKIP" });
    expect(state.lesson).toBe(1);
    state = reduce(state, { type: "BACK" });
    expect(state.lesson).toBe(0);
    state = reduce(state, { type: "BACK" });
    expect(state.step).toBe("mic");
    state = run(state, { type: "NEXT" }, { type: "NEXT" }, { type: "SKIP" }, { type: "SKIP" }, { type: "SKIP" }, { type: "SKIP" });
    expect(state.step).toBe("done");
    const back = reduce(state, { type: "BACK" });
    expect(back.step).toBe("practice");
    expect(back.lesson).toBe(4);
    expect(run(first(), { type: "BACK" }).step).toBe("welcome");
  });
});

describe("unticking", () => {
  test("only works on the checklist, and keeps the list in order", () => {
    const state = run(first(), { type: "TICK", id: "connect" });
    expect(state.abilities).toEqual(["dictate", "cleanup", "ask", "act"]);
    const on = run(first(), { type: "NEXT" }, { type: "TICK", id: "phone" }, { type: "TICK", id: "connect" });
    expect(on.abilities).toEqual(["dictate", "cleanup", "ask", "act", "connect", "phone"]);
    expect(run(on, { type: "TICK", id: "connect" }).abilities).toEqual(["dictate", "cleanup", "ask", "act", "phone"]);
  });

  test("never undoes what was saved: a key that is stored stays done", () => {
    const state = ticking(["dictate"], { key: true });
    expect(state.setup.key).toBe("done");
    expect(stepsFor(state)).not.toContain("key");
    expect(lessonsOf(ticking(["dictate", "act"], { key: true }))).toEqual(["key", "dictate", "act"]);
  });
});

describe("running it again", () => {
  test("starts from what is set up: connected services are ticked and their steps are done", () => {
    const state = initialState("rerun", { key: true, connections: true, telegram: false });
    expect(state.abilities).toEqual(["dictate", "cleanup", "ask", "act", "connect"]);
    expect(state.setup).toEqual({ key: "done", mic: "todo", connections: "done", telegram: "todo" });
    expect(state.mode).toBe("rerun");
  });

  test("an extra that is not set up is not ticked, and one without a key is not either", () => {
    expect(initialState("rerun", { key: false, connections: true, telegram: true }).abilities).toEqual(["dictate", "cleanup", "ask", "act"]);
    expect(initialState("rerun", { key: true, connections: false, telegram: true }).abilities).toContain("phone");
  });

  test("a step that is already done goes on without waiting", () => {
    let state = initialState("rerun", { key: true, connections: false, telegram: false });
    state = run(state, { type: "NEXT" }, { type: "NEXT" });
    expect(state.step).toBe("key");
    expect(canAdvance(state)).toBe(true);
    expect(canSkip(state)).toBe(false);
  });

  test("the lessons start undone, even where they were done before", () => {
    const state = initialState("rerun", { key: true, connections: true, telegram: true });
    expect(Object.values(state.lessons).every((s) => s === "idle")).toBe(true);
  });
});

describe("the bar", () => {
  test("has a segment for each step after the welcome and for each lesson", () => {
    const state = first({ key: true });
    expect(segments(state)).toEqual({ total: 8, current: -1 });
    expect(segments(run(state, { type: "NEXT" }))).toEqual({ total: 8, current: 0 });
  });

  test("moves through the lessons and ends full", () => {
    let state = first({ key: true });
    state = run(state, ...Array(4).fill({ type: "NEXT" }) as FlowEvent[]);
    expect(segments(state).current).toBe(3);
    state = run(state, { type: "SKIP" });
    expect(segments(state).current).toBe(4);
    state = run(state, { type: "SKIP" }, { type: "SKIP" }, { type: "SKIP" }, { type: "SKIP" });
    expect(state.step).toBe("done");
    expect(segments(state)).toEqual({ total: 8, current: 8 });
  });
});
