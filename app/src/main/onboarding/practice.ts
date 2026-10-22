// The practice: how each of the five lessons knows that the person did it. A lesson is a small
// state machine that is fed what the window hears (the app's events, the newest history item and the newest turn of the conversations) and says where it is. It is
// pure: the clock is given, nothing is read from the core here (mount.ts does that), so the
// success rules can be tested with a made-up series of events.
//
// What counts as done:
//   key      toggle mode: the dictation key pressed and let go; hold mode: held for 0.6 s or more
//   dictate  a dictation that reached the window (`Dictation pasted`), or a newer history item
//   cleanup  a history item newer than the lesson, cleaned; one that fell back is told, not counted
//   ask      a turn of the conversations newer than the lesson, with a reply
//   act      the same, routed as a command and done; a refusal or an answer to a question is not
// What was there before the lesson began never counts: the lesson remembers the newest ids on entry.

import { t } from "../i18n";

import type { AgentTurn, DeviceSettings, HistoryItem, MainEvent } from "../types";
import type { LessonId } from "./abilities";

/** How long the key is held in hold mode before the lesson counts it. */
export const HOLD_MS = 600;
/** How long the combination of the question key may be seen without the app hearing it before something is wrong. */
export const CONFLICT_MS = 3000;

export type LessonPhase = "idle" | "pressed" | "listening" | "processing" | "ok" | "failed";

/** Why a lesson did not count what happened. */
export type Miss = "tooShort" | "fallback" | "error" | "cancelled" | "notCommand" | "refused" | "noText";

export interface Lesson {
  id: LessonId;
  /** When the lesson began, on the clock of the window (ms). */
  startedMs: number;
  /** The newest history item and turn there were then: anything with an id up to these is old. */
  baselineItem: number | null;
  baselineTurn: number | null;
  phase: LessonPhase;
  miss: Miss | null;
  /** The key lesson: when the key went down. */
  downAt: number | null;
  /** The history item the lesson is about (cleanup, and the dictation's timing). */
  item: HistoryItem | null;
  /** The turn the lesson is about (ask, act). */
  turn: AgentTurn | null;
  /** The combination of the question key was seen by the window at this time, and has not been heard by the app. */
  comboAt: number | null;
  /** The app heard the question key at least once. */
  agentHeard: boolean;
  /** A dictation or question began after the lesson did. What an earlier one finishes is not this lesson's. */
  started: boolean;
}

export type PracticeEvent =
  | { type: "main"; event: MainEvent; now: number }
  /** The newest history item, read after a dictation (or null: none yet). */
  | { type: "item"; item: HistoryItem | null }
  /** The newest turn of the conversations, read after one was written. */
  | { type: "turn"; turn: AgentTurn | null }
  /** The window saw the whole combination of the question key go down. */
  | { type: "combo"; now: number };

export interface PracticeContext {
  mode: DeviceSettings["dictation_mode"];
}

/** A lesson as it begins. `baselineItem` and `baselineTurn` are what the core had then (null: nothing, or unknown). */
export function startLesson(id: LessonId, now: number, baselineItem: number | null = null, baselineTurn: number | null = null): Lesson {
  return { id, startedMs: now, baselineItem, baselineTurn, phase: "idle", miss: null, downAt: null, item: null, turn: null, comboAt: null, agentHeard: false, started: false };
}

const done = (lesson: Lesson): boolean => lesson.phase === "ok";

/** Whether `id` is newer than what the lesson found when it began (a time stands in when it found nothing). */
const newer = (id: number, createdMs: number, baseline: number | null, startedMs: number): boolean =>
  baseline === null ? createdMs >= startedMs : id > baseline;

/** The newest history item is the lesson's own if it is newer than the lesson. */
export const isNewItem = (lesson: Lesson, item: HistoryItem | null): item is HistoryItem =>
  item !== null && newer(item.id, item.created_at_ms, lesson.baselineItem, lesson.startedMs);

/** The newest turn is the lesson's own if it came from the person's voice and is newer than the lesson. */
export const isNewTurn = (lesson: Lesson, turn: AgentTurn | null): turn is AgentTurn =>
  turn !== null && (turn.source ?? "voice") === "voice" && newer(turn.id, turn.created_at_ms, lesson.baselineTurn, lesson.startedMs);

const ownItem = (lesson: Lesson, item: HistoryItem | null): HistoryItem | null => (isNewItem(lesson, item) ? item : null);
const ownTurn = (lesson: Lesson, turn: AgentTurn | null): AgentTurn | null => (isNewTurn(lesson, turn) ? turn : null);

/** What a dictation key or a phase of a dictation does to the lesson. */
function onMain(lesson: Lesson, event: MainEvent, now: number, ctx: PracticeContext): Lesson {
  if (event.type === "Hotkey") {
    if (event.kind === "agent_down") return { ...lesson, agentHeard: true, comboAt: null };
    if (lesson.id !== "key") return lesson;
    if (event.kind === "dictation_down") return { ...lesson, phase: "pressed", downAt: now, miss: null };
    if (event.kind === "dictation_up" && lesson.downAt !== null) {
      if (ctx.mode === "toggle" || now - lesson.downAt >= HOLD_MS) return { ...lesson, phase: "ok", miss: null };
      return { ...lesson, phase: "idle", downAt: null, miss: "tooShort" };
    }
    return lesson;
  }
  if (event.type !== "Dictation" || lesson.id === "key") return lesson;
  // A dictation lesson hears dictations, a question lesson hears questions.
  const asks = lesson.id === "ask" || lesson.id === "act";
  if (event.agent !== asks) return lesson;
  if (event.phase === "listening") return { ...lesson, phase: "listening", miss: null, started: true };
  // The rest belongs to a session this lesson saw begin; one that began before it is not heard.
  if (!lesson.started) return lesson;
  switch (event.phase) {
    case "processing":
      return { ...lesson, phase: "processing", miss: null };
    case "pasted":
      // The text is in the box. The dictation lesson is done; the cleanup lesson waits for its history item.
      return lesson.id === "dictate" ? { ...lesson, phase: "ok", miss: null } : lesson;
    case "cancelled":
      return { ...lesson, phase: "idle", miss: "cancelled" };
    case "failed":
      return { ...lesson, phase: "failed", miss: "error" };
  }
}

/** The newest history item, for the two lessons that dictate. */
function onItem(lesson: Lesson, item: HistoryItem | null): Lesson {
  if (lesson.id !== "dictate" && lesson.id !== "cleanup") return lesson;
  const own = ownItem(lesson, item);
  if (!own || done(lesson) || !lesson.started) return lesson;
  if (lesson.id === "dictate") return { ...lesson, phase: "ok", miss: null, item: own };
  // Cleanup: it has to have been cleaned. A fallback is a miss to tell, not a success.
  if (own.fallback_reason !== null || !own.cleaned) return { ...lesson, phase: "failed", miss: "fallback", item: own };
  return { ...lesson, phase: "ok", miss: null, item: own };
}

/** The newest turn, for the two lessons that ask. */
function onTurn(lesson: Lesson, turn: AgentTurn | null): Lesson {
  if (lesson.id !== "ask" && lesson.id !== "act") return lesson;
  const own = ownTurn(lesson, turn);
  if (!own || done(lesson) || !lesson.started) return lesson;
  if (own.status === "confirm") return lesson; // Raphael waits for an answer; the turn is not over
  if (!own.text.trim()) return { ...lesson, phase: "failed", miss: "noText", turn: own };
  if (lesson.id === "ask") return { ...lesson, phase: "ok", miss: null, turn: own };
  if (own.route !== "command") return { ...lesson, phase: "failed", miss: "notCommand", turn: own };
  if (own.status !== "done" || own.prefix.trim() === t("onboarding.negativeWord")) return { ...lesson, phase: "failed", miss: "refused", turn: own };
  return { ...lesson, phase: "ok", miss: null, turn: own };
}

/** The lesson after one thing was heard. A lesson that is done stays done. */
export function feed(lesson: Lesson, event: PracticeEvent, ctx: PracticeContext): Lesson {
  if (done(lesson)) return lesson;
  switch (event.type) {
    case "main":
      return onMain(lesson, event.event, event.now, ctx);
    case "item":
      return onItem(lesson, event.item);
    case "turn":
      return onTurn(lesson, event.turn);
    case "combo":
      return lesson.agentHeard ? lesson : { ...lesson, comboAt: lesson.comboAt ?? event.now };
  }
}

/**
 * Whether the question key looks taken by another program: the window saw the whole combination go
 * down, and the app did not hear it within `CONFLICT_MS`. (The hook does not get the keys while the
 * window has the focus, so the window passes them on itself; if another program has registered the
 * same combination, they never come back as an event.)
 */
export function conflictSuspected(lesson: Lesson, now: number): boolean {
  return (lesson.id === "ask" || lesson.id === "act") && !lesson.agentHeard && lesson.comboAt !== null && now - lesson.comboAt >= CONFLICT_MS;
}

/** The lesson, put back at its start for another try. */
export const retry = (lesson: Lesson, now: number): Lesson => ({
  ...startLesson(lesson.id, now, lesson.item?.id ?? lesson.baselineItem, lesson.turn?.id ?? lesson.baselineTurn),
  agentHeard: lesson.agentHeard,
});

// ---------- the sentences ----------

/** What the lesson tells the person to say or do. */
export type Prompt = "keyToggle" | "keyHold" | "dictate" | "cleanup" | "ask" | "act";

export const promptFor = (id: LessonId, mode: DeviceSettings["dictation_mode"]): Prompt => (id === "key" ? (mode === "hold" ? "keyHold" : "keyToggle") : id);
