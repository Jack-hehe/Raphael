// The order of the guide, as pure rules: which steps and which lessons there are for what a person
// ticked, what 「下一步」, 「返回」 and 「跳過」 do, and how far along the bar is. Nothing here touches
// the window or the core (mount.ts does); `reduce` takes a state and an event and gives the next
// state, so every rule can be tested without a browser.
//
// The steps are worked out, not written down: welcome, the checklist of abilities, one setup step
// for each thing the ticked abilities need (in a fixed order), the practice if any ticked ability
// has a lesson, and the end. Unticking an ability takes its setup and lessons out of the way and
// never undoes what was saved.

import { ABILITIES, type AbilityId, KEY_LESSONS, LESSON_ORDER, type LessonId, NEED_ORDER, type Need } from "./abilities";

/** `first`: the first run, which saves `onboarded` at the end. `rerun`: from the settings, which does not. */
export type Mode = "first" | "rerun";

export type StepId = "welcome" | "abilities" | Need | "practice" | "done";

export type SetupState = "todo" | "done" | "skipped";
export type LessonState = "idle" | "ok" | "skipped";

/** What is already set up when the guide opens. */
export interface Readiness {
  key: boolean;
  connections: boolean;
  telegram: boolean;
}

export interface FlowState {
  mode: Mode;
  /** The ticked abilities, in the order of the list. */
  abilities: AbilityId[];
  step: StepId;
  /** Which of `lessonsFor(...)` is on screen while `step` is `practice`. */
  lesson: number;
  setup: Record<Need, SetupState>;
  lessons: Record<LessonId, LessonState>;
}

export type FlowEvent =
  | { type: "NEXT" }
  | { type: "BACK" }
  | { type: "SKIP" }
  | { type: "TICK"; id: AbilityId }
  | { type: "SETUP"; need: Need; state: SetupState }
  | { type: "LESSON_OK" }
  | { type: "GOTO"; step: StepId };

const inListOrder = (ids: Iterable<AbilityId>): AbilityId[] => {
  const set = new Set(ids);
  return ABILITIES.filter((a) => set.has(a.id)).map((a) => a.id);
};

/**
 * The state the guide opens in. On a first run the everyday abilities are ticked; on a rerun the
 * abilities that are already set up are ticked too, so that the guide starts from what is there.
 */
export function initialState(mode: Mode, ready: Readiness): FlowState {
  const ticked = new Set<AbilityId>(ABILITIES.filter((a) => a.byDefault).map((a) => a.id));
  if (mode === "rerun") {
    if (ready.key && ready.connections) ticked.add("connect");
    if (ready.key && ready.telegram) ticked.add("phone");
  }
  return {
    mode,
    abilities: inListOrder(ticked),
    step: "welcome",
    lesson: 0,
    setup: {
      key: ready.key ? "done" : "todo",
      mic: "todo",
      connections: ready.connections ? "done" : "todo",
      telegram: ready.telegram ? "done" : "todo",
    },
    lessons: { key: "idle", dictate: "idle", cleanup: "idle", ask: "idle", act: "idle" },
  };
}

/**
 * What is set up became known after the guide opened. Only what the person has not decided is filled
 * in: ticks that were changed, skips, lesson results and where the guide is stay as they are, and a
 * setup that was already marked (done or skipped) is not taken back.
 */
export function applyReadiness(state: FlowState, ready: Readiness, ticksChanged: boolean): FlowState {
  const fresh = initialState(state.mode, ready);
  const known = (need: Need): SetupState => (state.setup[need] === "todo" ? fresh.setup[need] : state.setup[need]);
  return {
    ...state,
    abilities: ticksChanged ? state.abilities : fresh.abilities,
    setup: { key: known("key"), mic: state.setup.mic, connections: known("connections"), telegram: known("telegram") },
  };
}

const ticked = (abilities: readonly AbilityId[]) => ABILITIES.filter((a) => abilities.includes(a.id));

/** What the ticked abilities need, in the order of the setup steps. */
export const needsFor = (abilities: readonly AbilityId[]): Need[] => {
  const needs = new Set(ticked(abilities).flatMap((a) => a.needs));
  return NEED_ORDER.filter((need) => needs.has(need));
};

/** The lessons of the ticked abilities, in their order; without a key, those that need Raphael's AI are left out. */
export const lessonsFor = (abilities: readonly AbilityId[], hasKey: boolean): LessonId[] => {
  const wanted = new Set(ticked(abilities).flatMap((a) => a.lessons));
  return LESSON_ORDER.filter((id) => wanted.has(id) && (hasKey || !KEY_LESSONS.includes(id)));
};

export const hasKey = (state: Pick<FlowState, "setup">): boolean => state.setup.key === "done";

export const lessonsOf = (state: FlowState): LessonId[] => lessonsFor(state.abilities, hasKey(state));

/** The steps there are, in order. */
export function stepsFor(state: Pick<FlowState, "abilities" | "setup">): StepId[] {
  const practice = lessonsFor(state.abilities, hasKey(state)).length > 0;
  return ["welcome", "abilities", ...needsFor(state.abilities), ...(practice ? (["practice"] as const) : []), "done"];
}

/** The lesson on screen, if the practice is. */
export const currentLesson = (state: FlowState): LessonId | null => (state.step === "practice" ? (lessonsOf(state)[state.lesson] ?? null) : null);

const SKIPPABLE: readonly StepId[] = ["key", "connections", "telegram"];

/** Whether 「下一步」 does anything now: a step that needs something waits for it or for 「跳過」. */
export function canAdvance(state: FlowState): boolean {
  switch (state.step) {
    case "key":
    case "connections":
    case "telegram":
      return state.setup[state.step] !== "todo";
    case "practice": {
      const lesson = currentLesson(state);
      return lesson === null || state.lessons[lesson] !== "idle";
    }
    default:
      return true;
  }
}

/** Whether 「跳過」 is offered: on a step that waits for something outside, and on a lesson that is not done. */
export function canSkip(state: FlowState): boolean {
  if (SKIPPABLE.includes(state.step)) return state.setup[state.step as Need] === "todo";
  return state.step === "practice" && !canAdvance(state);
}

function advance(state: FlowState): FlowState {
  if (state.step === "practice") {
    const last = lessonsOf(state).length - 1;
    if (state.lesson < last) return { ...state, lesson: state.lesson + 1 };
  }
  const steps = stepsFor(state);
  const next = steps[steps.indexOf(state.step) + 1];
  if (!next) return state;
  return { ...state, step: next, lesson: 0 };
}

function retreat(state: FlowState): FlowState {
  if (state.step === "practice" && state.lesson > 0) return { ...state, lesson: state.lesson - 1 };
  const steps = stepsFor(state);
  const before = steps[steps.indexOf(state.step) - 1];
  if (!before) return state;
  const lesson = before === "practice" ? Math.max(0, lessonsOf(state).length - 1) : 0;
  return { ...state, step: before, lesson };
}

export function reduce(state: FlowState, event: FlowEvent): FlowState {
  switch (event.type) {
    case "NEXT":
      return canAdvance(state) ? advance(state) : state;
    case "BACK":
      return retreat(state);
    case "SKIP": {
      if (!canSkip(state)) return state;
      if (state.step === "practice") {
        const lesson = currentLesson(state);
        const marked = lesson ? { ...state, lessons: { ...state.lessons, [lesson]: "skipped" as const } } : state;
        return advance(marked);
      }
      return advance({ ...state, setup: { ...state.setup, [state.step]: "skipped" } });
    }
    case "TICK": {
      if (state.step !== "abilities") return state;
      const on = state.abilities.includes(event.id);
      return { ...state, abilities: inListOrder(on ? state.abilities.filter((id) => id !== event.id) : [...state.abilities, event.id]) };
    }
    case "SETUP":
      return { ...state, setup: { ...state.setup, [event.need]: event.state } };
    case "LESSON_OK": {
      const lesson = currentLesson(state);
      return lesson ? { ...state, lessons: { ...state.lessons, [lesson]: "ok" } } : state;
    }
    case "GOTO":
      return stepsFor(state).includes(event.step) ? { ...state, step: event.step, lesson: 0 } : state;
  }
}

/**
 * The bar at the top: one segment for each step after the welcome (the practice has one for each
 * lesson), and which of them the guide is at. The welcome is before the first (`current` -1) and
 * the end after the last.
 */
export function segments(state: FlowState): { total: number; current: number } {
  const lessons = lessonsOf(state).length;
  const steps = stepsFor(state).slice(1, -1);
  let total = 0;
  let current = -1;
  for (const step of steps) {
    const width = step === "practice" ? lessons : 1;
    if (step === state.step) current = total + (step === "practice" ? Math.min(state.lesson, width - 1) : 0);
    total += width;
  }
  if (state.step === "done") current = total;
  return { total, current };
}
