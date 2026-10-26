import { fileKind, sentFileHTML } from "./attachments";
import { NAME, eventLineHTML } from "./event-line";
import { handoffLineHTML } from "./handoff-line";
// A turn of the conversation as the 拉斐爾 page shows it: the person's words on the right, with a
// their actions below each bubble; Raphael on the left, with his face and each public message
// in order. Events are small chips, questions mount inline, and pending work shows three dots.
// Raw steps remain available to older detail callers, outside the main conversation flow.
//
// The functions make HTML from plain data; `TimelineState` says what is open and which steps are new,
// so that the page can draw again at any time and only what changed moves. Every word that came from
// the core goes through `esc`.

import { t } from "./i18n";
import type { MessageKey } from "./i18n/zh-TW";

import { plainReplies, userLine } from "../shared/anime";
import { TYPED, VOICE, hasDetail, highlightHTML, sourceOf } from "./conversation";
import { isBotRename, isSettledDraftTurn, settledDraftHTML } from "./member-draft";
import { messageHTML } from "./message-content";
import { quoteHTML } from "./message-quote";
import { previewCardsHTML } from "./link-previews";
import { formatClock, formatUsd } from "./format";
import { SERVICE_ICONS, brandStyle, hasLogo, serviceIconOf, serviceIconSVG, serviceLogoHTML } from "./service-icons";
import type { AgentStep, AgentTask, AgentTurn, MainEvent, MediaPayload, MessageQuote, TurnAttachment } from "./types";
import { btn, esc, ico, iconBtn } from "./ui";

/** The small turning ring of something under way. */
const SPINNER = `<span class="spin" aria-hidden="true"></span>`;

// ---------- words ----------

/** A message of the tables below, none of which takes a value. */
const plain = (key: MessageKey): string => (t as (key: MessageKey) => string)(key);

/** The server the core gives Raphael's own lookups. */
const RAPHAEL = "Raphael";

const BUILTIN_TITLES: Record<string, MessageKey> = {
  weather: "agent.tool.weather",
  web_search: "agent.tool.webSearch",
  local_time: "agent.tool.time",
  find_tools: "agent.tool.findTools",
};
const COMMAND_TITLES: Record<string, MessageKey> = {
  open: "agent.cmd.open",
  volume_up: "agent.cmd.volumeUp",
  volume_down: "agent.cmd.volumeDown",
  volume_mute: "agent.cmd.volumeMute",
  media_play_pause: "agent.cmd.playPause",
  media_next: "agent.cmd.next",
  media_previous: "agent.cmd.previous",
  lock_screen: "agent.cmd.lock",
  copy_text: "agent.cmd.copy",
};
/** The titles of what Raphael did on the owner's PC (a step of kind `computer`, its `tool`). */
const COMPUTER_TITLES: Record<string, MessageKey> = {
  apps: "agent.computer.apps",
  look: "agent.computer.look",
  open: "agent.computer.open",
  focus: "agent.computer.focus",
  click: "agent.computer.click",
  type: "agent.computer.type",
  keys: "agent.computer.keys",
  scroll: "agent.computer.scroll",
};
const REASONS: Record<string, MessageKey> = {
  password: "agent.reason.password",
  handoff: "agent.reason.handoff",
  refused_app: "agent.reason.refused_app",
  interrupted: "agent.reason.interrupted",
  forbidden: "agent.reason.forbidden",
  bad_arguments: "agent.reason.bad_arguments",
  auth: "agent.reason.auth",
  missing_secret: "agent.reason.missing_secret",
  denied: "agent.reason.denied",
  timeout: "agent.reason.timeout",
  network: "agent.reason.network",
  not_found: "agent.reason.not_found",
  blocked: "agent.reason.blocked",
  gone: "agent.reason.gone",
  tool_error: "agent.reason.tool_error",
  protocol: "agent.reason.protocol",
  cancelled: "agent.reason.cancelled",
  not_made: "agent.reason.not_made",
  credits: "agent.reason.credits",
  budget: "agent.reason.budget",
  no_key: "agent.reason.no_key",
  error: "agent.reason.error",
};
/** The states of a task that did not finish: it can be run again. */
const RERUNNABLE: ReadonlySet<string> = new Set(["interrupted", "cancelled", "limit", "failed"]);
const TASK_ENDS: Record<string, MessageKey> = {
  cancelled: "agent.taskCancelled",
  limit: "agent.taskLimit",
  failed: "agent.taskFailed",
  interrupted: "agent.taskInterrupted",
};

/**
 * The words that, first in a tool's name, mean it only looks: `_READ_WORDS` of the core's
 * `integrations/toolsets.py`, which groups the tools of 「連接」 the same way (a test of the core keeps
 * the two lists one).
 */
export const READ_WORDS: ReadonlySet<string> = new Set([
  "get", "list", "search", "find", "read", "fetch", "query", "lookup", "describe", "show",
  "view", "check", "count", "retrieve", "browse", "inspect", "export", "download", "preview",
  "summarize", "who", "whoami", "status", "info", "history", "resolve", "explain", "analyze",
]);
/** A word of a tool's name: split at `_`, `-`, `.` and a change of case, as toolsets.py splits it. */
const WORD = /[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])/g;

/**
 * Whether a service's tool only reads or may change something, by its name as toolsets.py guesses
 * when a server says nothing: `read` when its first word is one of `READ_WORDS`, `write` for any
 * other. The service's own name at the start (`notion-search`) is passed over first. A step keeps no
 * more than the tool's name, so this is a guess for its words only: it decides nothing.
 */
export function toolAccess(name: string, service = ""): "read" | "write" {
  let rest = name;
  const prefix = service.trim().toLowerCase();
  if (prefix) {
    for (const sep of ["-", "_", "."]) if (rest.toLowerCase().startsWith(prefix + sep) && rest.length > prefix.length + 1) rest = rest.slice(prefix.length + 1);
  }
  const first = rest.match(WORD)?.[0]?.toLowerCase();
  return first !== undefined && READ_WORDS.has(first) ? "read" : "write";
}

/**
 * What a step did, in the language of the window. Raphael's own lookups and the commands for this PC
 * have words of their own; a service's tool, whose name is the server's and in its language, is
 * 「Notion：讀取」 or 「Notion：修改」 by whether it only reads (`toolAccess`). Its name is the step's
 * tooltip (`stepToolTip`).
 */
export function stepTitle(step: AgentStep): string {
  if (step.kind === "model") return t("agent.stepThinking");
  if (!step.tool) return t("agent.stepUnknown");
  if (step.kind === "command") return plain(COMMAND_TITLES[step.tool] ?? "agent.cmd.other");
  if (step.kind === "computer") return plain(COMPUTER_TITLES[step.tool] ?? "agent.computer.other");
  if (step.server === RAPHAEL && BUILTIN_TITLES[step.tool]) return plain(BUILTIN_TITLES[step.tool]);
  const service = !step.server || step.server === RAPHAEL ? t("agent.title") : step.server;
  return t(toolAccess(step.tool, step.server ?? "") === "read" ? "agent.tool.read" : "agent.tool.write", { p0: service });
}

/** The tool's own name, for the tooltip of a step whose words do not name it (a service's tool); blank for one that has words of its own. */
export function stepToolTip(step: AgentStep): string {
  if (step.kind === "model" || !step.tool) return "";
  if (step.kind === "command") return COMMAND_TITLES[step.tool] ? "" : step.tool;
  // an app Raphael operated: the app's name, which the title leaves out
  if (step.kind === "computer") return step.server ?? "";
  return step.server === RAPHAEL && BUILTIN_TITLES[step.tool] ? "" : step.tool;
}

/** Where a step happened: the service, Raphael, or this PC. */
export function stepPlace(step: AgentStep): string {
  if (step.kind === "command" || step.kind === "computer") return t("agent.thisPc");
  if (step.kind === "model" || step.server === RAPHAEL || !step.server) return t("agent.title");
  return step.server;
}

/** Why a step failed, in a few words; empty for one that went well. */
export function reasonText(step: AgentStep): string {
  if (step.outcome === "ok") return "";
  // On the owner's PC a refusal is Raphael not being allowed to touch an app; `denied` elsewhere is a login that was refused.
  if (step.kind === "computer" && step.reason === "denied") return t("agent.reason.notAllowed");
  const key = REASONS[step.reason ?? ""] ?? (step.outcome === "timeout" ? REASONS.timeout : step.outcome === "blocked" ? REASONS.blocked : REASONS.error);
  return plain(key);
}

/** `0.4 秒`, `12 秒`, `1 分 05 秒`. */
export function formatDuration(ms: number): string {
  const safe = Math.max(0, ms);
  if (safe < 50) return t("agent.seconds", { p0: "<0.1" }); // a key press or a lookup in memory: no 0.0
  if (safe < 10_000) return t("agent.seconds", { p0: (Math.round(safe / 100) / 10).toFixed(1) });
  const seconds = Math.round(safe / 1000);
  if (seconds < 60) return t("agent.seconds", { p0: seconds });
  return t("agent.minutes", { p0: Math.floor(seconds / 60), p1: String(seconds % 60).padStart(2, "0") });
}

/** A model's name without its maker: `openai/gpt-6-luna` -> `gpt-6-luna`. */
export const shortModel = (model: string): string => model.slice(model.lastIndexOf("/") + 1);

// ---------- what a turn did ----------

/** The steps a reader is shown: everything but the model calls that went well, which are the thinking between them. */
export const shownSteps = (turn: AgentTurn): AgentStep[] => (turn.steps ?? []).filter((step) => (step.kind !== "model" || step.outcome !== "ok") && !bookkeeping(step));

/**
 * Steps that are no step to the reader: a background task looking through the tools it was given (no
 * service is called), and Raphael looking at the windows or the screen of the PC (nothing is done to
 * them). Only when they went well: a failed one is shown.
 */
const bookkeeping = (step: AgentStep): boolean =>
  step.outcome === "ok" &&
  ((step.server === RAPHAEL && step.tool === "find_tools") ||
    // looking at the windows or at the screen of an app is not something Raphael did to it
    (step.kind === "computer" && (step.tool === "apps" || step.tool === "look")));

export const isLive = (task: AgentTask | null | undefined): boolean => task?.state === "running" || task?.state === "queued";

/** How long a turn's work took: a task from its start to its end (or to now), else the span of its steps. */
export function workMs(turn: AgentTurn, nowMs: number): number {
  const task = turn.task;
  if (task?.started_at_ms) return (task.finished_at_ms ?? nowMs) - task.started_at_ms;
  const steps = turn.steps ?? [];
  if (steps.length === 0) return 0;
  const start = Math.min(...steps.map((step) => step.at_ms - step.ms));
  const end = Math.max(...steps.map((step) => step.at_ms));
  return Math.max(end - start, 0);
}

/** A step's own key, by which the page knows which steps it has drawn before. */
export const stepKey = (turn: AgentTurn, index: number): string => `${turn.id}:${index}`;

/** What the page keeps between draws. */
export interface TimelineState {
  /** Turns whose steps the reader opened or closed: their choice beats the default. */
  open: Map<number, boolean>;
  /** Turns whose thinking block the reader opened or closed: their choice beats the default. */
  thinkingOpen: Map<number, boolean>;
  /** The steps drawn before; any other is new and comes in with a movement. */
  seen: Set<string>;
  /** Tasks the reader asked to stop, which have not stopped yet. */
  stopping: Set<number>;
  /** Tasks the reader asked to run again; each is run again once. */
  rerunning: Set<number>;
  /** Tasks that were seen under way: the draw that finds one ended shows its check coming in. */
  live: Set<number>;
  /** Tasks whose end this draw shows for the first time (`noteTaskEnds`): their check pops in. */
  ended: Set<number>;
  nowMs: number;
  expandedMessages: Set<string>;
}

export const emptyTimelineState = (): TimelineState => ({
  open: new Map(),
  thinkingOpen: new Map(),
  seen: new Set(),
  stopping: new Set(),
  rerunning: new Set(),
  live: new Set(),
  ended: new Set(),
  nowMs: Date.now(),
  expandedMessages: new Set(),
});

/** Whether a turn's steps are open: as the reader left them, else open while its task runs and closed once it is done. */
export const stepsOpen = (turn: AgentTurn, state: TimelineState): boolean => state.open.get(turn.id) ?? isLive(turn.task);

/**
 * Before a draw: a task seen under way that has ended since is `ended` for this draw (its check
 * comes in with a small pop), and the ones under way now are remembered for the next.
 */
export function noteTaskEnds(turns: readonly AgentTurn[], state: TimelineState): void {
  state.ended.clear();
  for (const turn of turns) {
    const task = turn.task;
    if (!task) continue;
    if (isLive(task)) state.live.add(task.id);
    else if (state.live.delete(task.id)) state.ended.add(task.id);
  }
}

/** The picture of a step: the service's mark, Raphael's own for a lookup, the PC for a command. It names the place in its label and tooltip. */
function stepIcon(step: AgentStep): string {
  const place = esc(stepPlace(step));
  const named = `role="img" aria-label="${place}" title="${place}"`;
  if (step.kind === "command" || step.kind === "computer") return `<span class="step-ic" ${named}>${ico("monitor")}</span>`;
  if (step.kind === "model") return `<span class="step-ic" ${named}>${ico("sparkle")}</span>`;
  if (!step.server || step.server === RAPHAEL) {
    const own = step.tool === "weather" ? ico("cloud") : step.tool === "web_search" ? ico("globe") : step.tool === "local_time" ? ico("clock") : ico("search");
    return `<span class="step-ic" ${named}>${own}</span>`;
  }
  const mark = serviceIconOf({ name: step.server });
  if (mark && hasLogo(mark)) return `<span class="step-ic logo-art" ${named}>${serviceLogoHTML(mark)}</span>`;
  return mark
    ? `<span class="step-ic logo-mono"${brandStyle(mark)} ${named}>${serviceIconSVG(mark)}</span>`
    : `<span class="step-ic" ${named}>${ico("plug")}</span>`;
}

/** One step: what, where, how long, and whether it worked (a mark and a word, never the colour alone). */
export function stepHTML(step: AgentStep, isNew: boolean): string {
  const ok = step.outcome === "ok";
  const why = reasonText(step);
  const tip = stepToolTip(step);
  return (
    `<li class="step ${ok ? "is-ok" : "is-bad"}${isNew ? " is-new" : ""}">` +
    stepIcon(step) +
    `<span class="step-main"><span class="step-t"${tip ? ` title="${esc(tip)}"` : ""}>${esc(stepTitle(step))}</span>` +
    (ok ? "" : `<span class="step-why">${esc(why)}</span>`) +
    `</span>` +
    `<span class="step-end"><span class="step-ms num">${esc(formatDuration(step.ms))}</span>` +
    `<span class="step-mark" role="img" aria-label="${esc(ok ? t("agent.ok") : why)}">${ok ? ico("check") : ico("x")}</span></span>` +
    `</li>`
  );
}

/** The step a task is at now: its words with light passing over them, the time since it began. */
function liveStepHTML(task: AgentTask, nowMs: number): string {
  const since = task.started_at_ms;
  const words = task.state === "queued" ? t("agent.queued") : t("agent.thinking");
  return (
    `<li class="step is-live">` +
    `<span class="step-ic">${SPINNER}</span>` +
    `<span class="step-main"><span class="step-t shimmer">${esc(words)}</span></span>` +
    `<span class="step-end">${since ? `<span class="step-ms num" data-since="${since}">${esc(formatDuration(nowMs - since))}</span>` : ""}</span>` +
    `</li>`
  );
}

/** How many things a turn did, in words: 「1 個動作」, 「3 個動作」. */
const actionsText = (count: number): string => (count === 1 ? t("agent.stepsDoneOne") : t("agent.stepsDone", { p0: count }));

/** The words of a summary: how many things were done, how long it took, how many failed; or that a task is under way. */
export function summaryText(turn: AgentTurn, nowMs: number): { lead: string; time: string; failed: number } {
  const steps = shownSteps(turn).filter((step) => step.kind !== "model");
  const failed = shownSteps(turn).filter((step) => step.outcome !== "ok").length;
  const task = turn.task;
  const done = actionsText(steps.length);
  let lead = done;
  if (isLive(task)) lead = task!.state === "queued" ? t("agent.queued") : t("agent.working");
  else if (task?.state === "waiting") lead = t("agent.taskWaiting"); // its question is the gold card
  else if (task && TASK_ENDS[task.state]) lead = `${plain(TASK_ENDS[task.state])} · ${done}`;
  const ms = task?.state === "waiting" ? 0 : workMs(turn, nowMs); // the time it waits is not its own
  return { lead, time: ms > 0 || isLive(task) ? formatDuration(ms) : "", failed };
}

/** The list of steps under a summary, which slides open; with the step a task is at last. */
function stepsBodyHTML(turn: AgentTurn, state: TimelineState, open: boolean): string {
  const live = isLive(turn.task);
  const rows = shownSteps(turn).map((step, index) => stepHTML(step, !state.seen.has(stepKey(turn, index)))).join("") + (live ? liveStepHTML(turn.task!, state.nowMs) : "");
  return (
    `<div class="steps-body" id="steps-${esc(turn.id)}"${open ? "" : " inert"}><div class="steps-clip">` +
    `<ol class="steps-list" aria-label="${esc(t("agent.stepsList"))}">${rows}</ol>` +
    `</div></div>`
  );
}

/** The one line of a turn that did something: 「› 3 個動作 · 2.4 秒」, and the steps when it is opened. */
function stepsLineHTML(turn: AgentTurn, state: TimelineState): string {
  const open = stepsOpen(turn, state);
  const { lead, time, failed } = summaryText(turn, state.nowMs);
  const bad = failed ? t("agent.stepsFailed", { p0: failed }) : "";
  const words = [lead, time, bad].filter(Boolean).join(" · ");
  const summary = btn(
    `<span class="steps-chev">${ico("right")}</span><span>${esc(lead)}</span>` +
      (time ? `<span class="num">· ${esc(time)}</span>` : "") +
      (bad ? `<span class="steps-bad">· ${esc(bad)}</span>` : ""),
    "steps-sum",
    ` data-act="steps" aria-expanded="${open}" aria-controls="steps-${esc(turn.id)}" aria-label="${esc(words)}"`,
  );
  return `<div class="steps" data-open="${open}">${summary}${stepsBodyHTML(turn, state, open)}</div>`;
}

/**
 * A background task: a card whose head says how it stands (a turning ring and the time while it
 * runs, a check once it is done) and opens its steps, with 「停止」 while it runs and 「重新執行」 for
 * one that did not finish.
 */
function taskCardHTML(turn: AgentTurn, state: TimelineState): string {
  const task = turn.task!;
  const live = isLive(task);
  const open = stepsOpen(turn, state);
  const { lead, time, failed } = summaryText(turn, state.nowMs);
  const bad = failed ? t("agent.stepsFailed", { p0: failed }) : "";
  const words = [lead, time, bad].filter(Boolean).join(" · ");
  const ended = state.ended.has(task.id);
  // A task that waits for the owner's yes is not under way, but not over either: it can be stopped.
  const waiting = task.state === "waiting";
  const mark = live
    ? task.state === "queued" ? ico("clock") : SPINNER
    : waiting ? ico("clock")
    : task.state === "done" ? `<span class="task-check${ended ? " is-pop" : ""}">${ico("check")}</span>` : ico("stop");
  const since = live && task.started_at_ms ? ` data-since="${task.started_at_ms}"` : "";
  const summary = btn(
    `<span class="task-mark">${mark}</span><span class="task-lead">${esc(lead)}</span>` +
      (time ? `<span class="task-time num"${since}>${esc(time)}</span>` : "") +
      (bad ? `<span class="steps-bad">${esc(bad)}</span>` : "") +
      `<span class="steps-chev">${ico("down")}</span>`,
    "task-sum",
    ` data-act="steps" aria-expanded="${open}" aria-controls="steps-${esc(turn.id)}" aria-label="${esc(words)}"`,
  );
  const stopping = (live || waiting) && state.stopping.has(task.id);
  const stop = live || waiting
    ? `<button type="button" class="btn sm stop" data-act="stop" data-task="${esc(task.id)}"${stopping ? " disabled" : ""}>${esc(stopping ? t("agent.stopping") : t("agent.stop"))}</button>`
    : "";
  const again = RERUNNABLE.has(task.state)
    ? `<button type="button" class="btn sm rerun" data-act="rerun" data-task="${esc(task.id)}"${state.rerunning.has(task.id) ? " disabled" : ""}>${esc(t("agent.rerun"))}</button>`
    : "";
  return (
    `<div class="steps task${live ? " is-live" : ""}" data-open="${open}">` +
    `<div class="task-head">${summary}${stop}${again}</div>` +
    stepsBodyHTML(turn, state, open) +
    `</div>`
  );
}

/** What Raphael did for a turn: the card of its task, the line of its steps, or nothing for a turn that did nothing. */
export function stepsHTML(turn: AgentTurn, state: TimelineState): string {
  if (turn.task) return taskCardHTML(turn, state);
  return shownSteps(turn).length === 0 ? "" : stepsLineHTML(turn, state);
}

/** Whether an event of the app changes what the conversations show: a turn, or a task's step. */
export const wantsRefresh = (event: MainEvent): boolean => event.type === "AgentTurn" || event.type === "WorkerChanged";

/**
 * 「停止」: the task is marked as stopping at once, so its button says so, and the core is asked to
 * stop it. When that fails, or there was nothing to stop, the mark comes off again; otherwise it stays
 * until the task is seen to have ended.
 */
export async function requestStop(
  state: TimelineState,
  taskId: number,
  cancel: (taskId: number) => Promise<{ cancelled: boolean }>,
): Promise<{ cancelled: boolean; error?: unknown }> {
  state.stopping.add(taskId);
  try {
    const { cancelled } = await cancel(taskId);
    if (!cancelled) state.stopping.delete(taskId);
    return { cancelled };
  } catch (error) {
    state.stopping.delete(taskId);
    return { cancelled: false, error };
  }
}

/**
 * Takes the mark off the tasks that are not among `turns` any more: after the history is cleared
 * the core numbers tasks from 1 again, and a new task must not be taken for the old one.
 */
export function forgetReruns(state: TimelineState, turns: readonly AgentTurn[]): void {
  const shown = new Set(turns.flatMap((turn) => (turn.task ? [turn.task.id] : [])));
  for (const id of [...state.rerunning]) if (!shown.has(id)) state.rerunning.delete(id);
}

/**
 * 「重新執行」: the task is marked at once, so its button is off, and the core starts it again as a
 * new task. When that fails the mark comes off; otherwise it stays, so that one task is not started
 * twice.
 */
export async function requestRerun(
  state: TimelineState,
  taskId: number,
  rerun: (taskId: number) => Promise<{ task_id: number }>,
): Promise<{ started: boolean; error?: unknown }> {
  state.rerunning.add(taskId);
  try {
    await rerun(taskId);
    return { started: true };
  } catch (error) {
    state.rerunning.delete(taskId);
    return { started: false, error };
  }
}

/** Marks every step of `turns` as drawn, so that the next draw moves only the ones that come after. */
export function markSeen(turns: readonly AgentTurn[], state: TimelineState): void {
  for (const turn of turns) shownSteps(turn).forEach((_, index) => state.seen.add(stepKey(turn, index)));
}

// ---------- a turn ----------

/** The picture of where a turn came from: a microphone, Telegram's mark, or a keyboard for one typed here. */
export const sourceIcon = (source: string): string =>
  source === VOICE
    ? ico("mic")
    : source === "telegram"
      ? `<svg class="i tg-i" viewBox="0 0 24 24" aria-hidden="true"><path d="${SERVICE_ICONS.telegram.path}"/></svg>`
      : source === TYPED
        ? ico("keyboard")
        : ico("send");

/** Where a turn came from, in words. */
export function sourceName(source: string): string {
  if (source === VOICE) return t("agent.sourceVoice");
  if (source === "telegram") return t("agent.sourceTelegram");
  if (source === TYPED) return t("agent.sourceTyped");
  return source.charAt(0).toUpperCase() + source.slice(1);
}

/** What a turn cost and what worded it, for the tooltip of Raphael's face: whether it waits, the model and the cost. */
export function answerMeta(turn: AgentTurn): string {
  const parts: string[] = [];
  if (turn.status === "confirm") parts.push(t("agent.waiting"));
  if (turn.model) parts.push(shortModel(turn.model));
  if (turn.cost_usd && turn.cost_usd > 0) parts.push(formatUsd(turn.cost_usd));
  return parts.join(" · ");
}

/** How a turn is drawn besides its data. */
export interface TurnLook {
  revealing?: (turnId: number) => boolean;
  regenerateTurnId?: number;
  /** The face of who answers (Raphael's, avatar.ts, or the member's): `loop` while at work on it. */
  face: (loop: boolean) => string;
  /** The words being looked for, marked wherever they are; blank for none. */
  query?: string;
  /** The exchanges between members that are open, by turn id (their line opens on the messages). */
  openExchanges?: ReadonlySet<number>;
  /** The name of member `id` (0 is Raphael), for the line of an exchange. */
  memberName?: (id: number) => string;
  /** The small face of member `id` on the line of an exchange (Raphael's bare emblem for 0). */
  memberFace?: (id: number) => string;
}

/** Whether a turn is an exchange between two members, not the owner's own words. */
export const isExchange = (turn: AgentTurn): boolean => turn.route === "exchange" || sourceOf(turn) === "member";

/**
 * The line of a routine's run above its answer: 「例行任務 <title>」, the title being the run's
 * `question` (the core writes the routine's title there).
 */
export function routineLineHTML(turn: AgentTurn): string {
  const title = turn.question.trim();
  return `<div class="sys">${ico("repeat")}<span>${esc(t("members.routineLine"))}</span>${title ? `<b>${esc(title)}</b>` : ""}</div>`;
}

/**
 * An exchange between two members, as one line: 「N 則訊息往來：<name>」 (the question and the answer,
 * as many as there are), which opens on them: what was asked in 「」, and what was answered. They
 * are drawn only while it is open. `name` is the member on the other side.
 */
export function exchangeHTML(turn: AgentTurn, name: string, open: boolean, face = "", tag: "li" | "div" = "li"): string {
  const id = esc(turn.id);
  const messages = [turn.question.trim() ? userLine(turn.question.trim()) : "", turn.text.trim()].filter(Boolean);
  // the line ends in a colon that leads to the name; with no name it ends without one
  const said = t("members.exchange", { p0: messages.length });
  const label = name ? said : said.replace(/[：:]\s*$/, "");
  return (
    `<${tag} class="${tag === "li" ? "tn " : ""}tn-x" data-id="${id}">` +
    btn(
      `${face}<span>${esc(label)}</span>${name ? `<b>${esc(name)}</b>` : ""}<span class="steps-chev">${ico("right")}</span>`,
      "sys sys-x",
      ` data-act="exchange" aria-expanded="${open}" aria-controls="tn-x-${id}"`,
    ) +
    (open ? `<ol class="x-list" id="tn-x-${id}">` + messages.map((m) => `<li>${messageHTML(m)}</li>`).join("") + `</ol>` : "") +
    `</${tag}>`
  );
}

/**
 * Visual media share a grid; audio and documents stack underneath it.
 */
export function filesHTML(files: readonly TurnAttachment[] | undefined): string {
  if (!files || files.length === 0) return "";
  const visual = files.filter(file => ["image", "video"].includes(fileKind(file.mime)) && (file.id || file.preview));
  const rest = files.filter(file => !visual.includes(file));
  return `<div class="me-files">${visual.length ? `<div class="attachment-grid${visual.length > 1 ? " is-multiple" : ""}">${visual.map(sentFileHTML).join("")}</div>` : ""}${rest.map(sentFileHTML).join("")}</div>`;
}

/** Three dots while the next public line is pending. */
const typingHTML = (): string => `<p class="ans thinking"><span class="think" role="img" aria-label="${esc(t("agent.thinking"))}"><i></i><i></i><i></i></span></p>`;

/** Background task controls without raw tool labels or a collapsed steps box. */
function taskActionsHTML(turn: AgentTurn, state: TimelineState): string {
  const task = turn.task;
  if (!task) return "";
  const live = isLive(task) || task.state === "waiting";
  const label = task.state === "waiting" ? t("agent.taskWaiting") : isLive(task) ? t("agent.working") : plain(TASK_ENDS[task.state] ?? "agent.ok");
  return `<div class="turn-event task-actions"><span>${esc(label)}</span>` +
    (live ? btn(esc(t("agent.stop")), "sm stop", ` data-act="stop" data-task="${esc(task.id)}"${state.stopping.has(task.id) ? " disabled" : ""}`) : "") +
    (RERUNNABLE.has(task.state) ? btn(esc(t("agent.rerun")), "sm rerun", ` data-act="rerun" data-task="${esc(task.id)}"${state.rerunning.has(task.id) ? " disabled" : ""}`) : "") + `</div>`;
}

/** Media stands above the owner's bubble, whose quote precedes the words. */
function bubbleHTML(words: string, files: readonly TurnAttachment[] | undefined, quote?: MessageQuote | null): string {
  const bubble = words || quote ? `<div class="bubble">${quote ? quoteHTML(quote) : ""}${words}</div>` : "";
  return files?.length ? `<div class="owner-content">${filesHTML(files)}${bubble}</div>` : bubble;
}

export function messageActionsHTML(time: number, regenerate = false, owner?: "edit" | "cancel", actionable = true): string {
  const action = (icon: Parameters<typeof iconBtn>[0], label: string, act: string): string =>
    iconBtn(icon, label, ` data-act="${act}"`).replace(" title=", " data-tooltip=");
  return `<div class="message-actions"><time tabindex="0" data-tooltip="${esc(new Date(time).toLocaleString())}" datetime="${esc(new Date(time).toISOString())}">${esc(formatClock(time))}</time>` +
    (actionable ? action("reply", t("chat.reply"), "reply-message") : "") +
    action("copy", t("chat.copy"), "copy-message") +
    (owner ? action(owner === "edit" ? "pencil" : "x", t(owner === "edit" ? "chat.edit" : "common.cancel"), `${owner}-message`) : "") +
    (!owner && actionable ? action("more", t("chat.more"), "more-message").replace('data-act="more-message"', `data-act="more-message" data-regenerate="${regenerate}" aria-haspopup="menu"`) : "") + `</div>`;
}

function messageBlockHTML(turn: AgentTurn, key: string, text: string, time: number, query: string, state: TimelineState, reply: boolean, regenerate = false): string {
  const expanded = state.expandedMessages.has(`${turn.id}:${key}`);
  return `<div class="chat-message${expanded ? " is-expanded" : ""}" data-message="${esc(key)}"${reply ? ' data-expandable="true"' : ""}>` +
    `<div class="ans${reply ? "" : " progress"}"><div class="message-text">${messageHTML(text, query, reply)}</div></div>` +
    (reply ? btn(esc(t(expanded ? "chat.showLess" : "chat.showAll")), "sm message-expand", ` data-act="expand-message" aria-expanded="${expanded}"${text.split("\n").length > 30 || text.length > 3000 || expanded ? "" : " hidden"}`) : "") +
    messageActionsHTML(time, regenerate, undefined, reply) + (reply ? previewCardsHTML(text) : "") + `</div>`;
}

/** Validates an http(s) URL; returns null for any other scheme or invalid URL. */
export function safeHttpUrl(raw: unknown): string | null {
  if (typeof raw !== "string") return null;
  const trimmed = raw.trim();
  if (!trimmed) return null;
  try {
    const parsed = new URL(trimmed);
    if (parsed.protocol === "http:" || parsed.protocol === "https:") return parsed.href;
  } catch {
    // not a valid URL
  }
  return null;
}

/** Validates an image/thumbnail source: http(s) or a safe data:image URI. */
export function safeMediaSrc(raw: unknown, allowDataImage = false): string | null {
  const http = safeHttpUrl(raw);
  if (http) return http;
  if (allowDataImage && typeof raw === "string") {
    const trimmed = raw.trim();
    if (/^data:image\/(?:png|jpeg|jpg|webp|gif|svg\+xml);/i.test(trimmed)) return trimmed;
  }
  return null;
}

/** Extracts or validates a YouTube embed URL for an inline iframe player. */
export function safeYoutubeEmbedUrl(embed: unknown, fallbackUrl?: unknown): string | null {
  for (const candidate of [embed, fallbackUrl]) {
    const valid = safeHttpUrl(candidate);
    if (!valid) continue;
    try {
      const u = new URL(valid);
      const host = u.hostname.toLowerCase();
      if (
        (host === "www.youtube.com" || host === "youtube.com" || host === "www.youtube-nocookie.com" || host === "youtube-nocookie.com") &&
        u.pathname.startsWith("/embed/")
      ) {
        return valid;
      }
      if (host === "youtu.be" && u.pathname.length > 1) {
        const id = u.pathname.slice(1).split("/")[0];
        if (id && /^[\w-]{6,20}$/.test(id)) return `https://www.youtube.com/embed/${id}`;
      }
      if ((host === "www.youtube.com" || host === "youtube.com") && u.pathname === "/watch") {
        const id = u.searchParams.get("v");
        if (id && /^[\w-]{6,20}$/.test(id)) return `https://www.youtube.com/embed/${id}`;
      }
    } catch {
      // ignore
    }
  }
  return null;
}

/** Parses a `media` message's JSON string; returns null when malformed. */
export function parseMediaPayload(raw: string): MediaPayload | null {
  try {
    const parsed = JSON.parse(raw) as Partial<MediaPayload> | null;
    if (!parsed || typeof parsed !== "object") return null;
    if (parsed.type !== "link" && parsed.type !== "image" && parsed.type !== "video" && parsed.type !== "pdf") return null;
    if (typeof parsed.url !== "string" || !parsed.url.trim()) return null;
    return {
      type: parsed.type,
      url: parsed.url.trim(),
      title: typeof parsed.title === "string" ? parsed.title.trim() : "",
      site: typeof parsed.site === "string" ? parsed.site.trim() : "",
      description: typeof parsed.description === "string" ? parsed.description.trim() : "",
      thumbnail: typeof parsed.thumbnail === "string" && parsed.thumbnail.trim() ? parsed.thumbnail.trim() : null,
      embed: typeof parsed.embed === "string" && parsed.embed.trim() ? parsed.embed.trim() : null,
    };
  } catch {
    return null;
  }
}

function meetingEvent(text: string): { room_id: number; name: string } | null {
  try {
    const event = JSON.parse(text);
    return event?.type === "meeting" && Number.isSafeInteger(event.room_id) && event.room_id > 0 && typeof event.name === "string" ? event : null;
  } catch { return null; }
}

/** One public line of Raphael's work (《告。…》 on the desktop) as the window shows it: plain. */
const plainLine = (text: string): string => plainReplies([text])[0];

/** Keep all work behind one closed chip; running turns show their latest public update. */
function workSectionHTML(turn: AgentTurn, state: TimelineState, query: string, exchanges = ""): string {
  const messages = (turn.messages ?? []).filter(m => m.kind !== "reply" && m.kind !== "media" && !meetingEvent(m.text) && m.text.trim())
    .map(m => ({ ...m, text: plainLine(m.text) }));
  if (!messages.length && !exchanges && !(turn.steps?.length) && turn.status !== "running" && !isLive(turn.task)) return "";
  const live = turn.status === "running" || isLive(turn.task);
  const firstReply = (turn.messages ?? []).find(m => m.kind === "reply" && m.at_ms >= turn.created_at_ms)?.at_ms;
  const end = live ? state.nowMs : turn.task?.finished_at_ms ?? firstReply ?? Math.max(turn.created_at_ms, ...messages.map(m => m.at_ms), ...(turn.steps ?? []).map(step => step.at_ms + step.ms));
  const seconds = Math.max(0, Math.round((end - turn.created_at_ms) / 1000));
  const open = state.thinkingOpen.get(turn.id) ?? false;
  const latest = [...messages].reverse().find(m => m.kind === "progress" || m.kind === "thinking");
  const label = live
    ? `<span class="work-latest">${esc(latest?.text ?? t("agent.thinking"))}</span><span class="work-time">${esc(t("chat.workingFor", { p0: seconds }))}</span>`
    : `<span>${esc(t("chat.workedFor", { p0: seconds }))}</span>`;
  const indicator = live ? `<span class="think" role="img" aria-label="${esc(t("agent.thinking"))}"><i></i><i></i><i></i></span>` : ico("check");
  const body = messages.map(m => `<div class="chat-message" data-message="${m.seq}"><div class="${m.kind === "event" ? "turn-event" : "work-progress"}">${messageHTML(m.text, query)}</div></div>`).join("") + exchanges +
    ((turn.steps?.length ?? 0) > 0 ? `<ol class="step-list">${shownSteps(turn).map(step => stepHTML(step, false)).join("")}</ol>` : "");
  return `<div class="work-section think-box${open ? " is-open" : ""}${live ? " is-live" : ""}" data-thinking-turn="${turn.id}">` +
    btn(`${indicator}${label}<span class="work-chevron">${ico("right")}</span>`, "work-toggle", ` data-act="thinking" aria-expanded="${open}" aria-controls="work-${turn.id}"`) +
    `<div class="work-body think-body" id="work-${turn.id}"${open ? "" : " hidden"}>${body}</div></div>`;
}

/** An inline media card (`link`, `image`, `video`, or `pdf`) with a safe `在網站開啟` action button. */
export function mediaCardHTML(payload: MediaPayload, query: string, seq?: number): string {
  const safeUrl = safeHttpUrl(payload.url);
  const thumb = safeMediaSrc(payload.thumbnail, true);
  const title = payload.title ?? "";
  const site = payload.site ?? "";
  const desc = payload.description ?? "";
  const seqAttr = seq !== undefined ? ` data-seq="${esc(seq)}"` : "";
  const openBtn = safeUrl
    ? `<button type="button" class="btn sm media-open" data-act="open-url" data-url="${esc(safeUrl)}">${ico("link")}<span>${esc(t("agent.openInSite"))}</span></button>`
    : "";
  const metaHTML =
    (site ? `<span class="media-site">${highlightHTML(site, query)}</span>` : "") +
    (title ? `<div class="media-title">${highlightHTML(title, query)}</div>` : "") +
    (desc ? `<div class="media-desc">${highlightHTML(desc, query)}</div>` : "");

  if (payload.type === "image") {
    const imgSrc = safeMediaSrc(payload.url, true) ?? thumb;
    const pic = imgSrc
      ? `<button type="button" class="btn media-img-btn" data-act="lightbox" data-src="${esc(imgSrc)}" data-title="${esc(title || site)}" aria-label="${esc(title || t("agent.viewImage"))}">` +
        `<img class="media-img" src="${esc(imgSrc)}" alt="${esc(title)}" loading="lazy"></button>`
      : "";
    return (
      `<div class="media-card media-image" data-media-type="image"${seqAttr}>` +
      pic +
      `<div class="media-bar"><div class="media-meta">${metaHTML}</div><div class="media-foot">${openBtn}</div></div>` +
      `</div>`
    );
  }

  if (payload.type === "video") {
    const ytEmbed = safeYoutubeEmbedUrl(payload.embed, payload.url);
    const player = ytEmbed
      ? `<div class="media-player"><iframe class="media-iframe" src="${esc(ytEmbed)}" title="${esc(title || site || "Video")}" loading="lazy" allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture" allowfullscreen></iframe></div>`
      : safeUrl
        ? `<div class="media-player"><video class="media-video" controls preload="${thumb ? "none" : "metadata"}"${thumb ? ` poster="${esc(thumb)}"` : ""} src="${esc(safeUrl)}"></video></div>`
        : "";
    return (
      `<div class="media-card media-video-card" data-media-type="video"${seqAttr}>` +
      player +
      `<div class="media-bar"><div class="media-meta">${metaHTML}</div><div class="media-foot">${openBtn}</div></div>` +
      `</div>`
    );
  }

  if (payload.type === "pdf") {
    const frame = safeUrl
      ? `<div class="media-pdf-wrap"><iframe class="media-pdf-frame" src="${esc(safeUrl)}" title="${esc(title || "PDF")}" loading="lazy"></iframe></div>`
      : "";
    const expandBtn = `<button type="button" class="btn sm media-expand" data-act="pdf-expand" aria-expanded="false">${esc(t("agent.expandPdf"))}</button>`;
    return (
      `<div class="media-card media-pdf" data-media-type="pdf"${seqAttr}>` +
      frame +
      `<div class="media-bar"><div class="media-meta">${metaHTML}</div><div class="media-foot">${expandBtn}${openBtn}</div></div>` +
      `</div>`
    );
  }

  return (
    `<div class="media-card media-link" data-media-type="link"${seqAttr}>` +
    `<div class="media-link-main">` +
    (thumb ? `<img class="media-thumb" src="${esc(thumb)}" alt="${esc(title || site)}" loading="lazy">` : "") +
    `<div class="media-meta">${metaHTML}</div>` +
    `<div class="media-foot">${openBtn}</div>` +
    `</div>` +
    `</div>`
  );
}

/** Renders all ordered items in `turn.messages` (thinking block, progress, media cards, event chips, replies). */
function turnMessagesHTML(turn: AgentTurn, state: TimelineState, query: string, regenerate: boolean, inner = ""): string {
  const ordered = [...(turn.messages ?? [])].sort((a, b) => a.seq - b.seq);
  const replies = ordered.filter(m => m.kind === "reply");
  const said = new Map(plainReplies(replies.map(m => m.text)).map((text, index) => [replies[index].seq, text.trim()]));
  const lastReply = [...replies].reverse().find(m => said.get(m.seq))?.seq;
  const work = workSectionHTML(turn, state, query, inner);
  const settledDraft = isSettledDraftTurn(turn);
  return work + ordered.filter(message => (message.kind === "reply" && !(settledDraft && said.get(message.seq) === turn.text.trim())) || message.kind === "media").map(message => {
    const text = message.kind === "reply" ? said.get(message.seq) ?? "" : message.text.trim();
    if (!text) return "";
    if (message.kind === "media") {
      const media = parseMediaPayload(text);
      return media ? `<div class="chat-message" data-message="${message.seq}">${mediaCardHTML(media, query, message.seq)}</div>` : "";
    }
    return `<div data-seq="${message.seq}">${messageBlockHTML(turn, String(message.seq), text, message.at_ms, query, state, true, regenerate && message.seq === lastReply)}</div>`;
  }).join("");
}

/**
 * The centred event lines of a turn, outside Raphael's messages: the work it handed over (each opens on
 * what the owner asked) and the meetings it opened, whose line opens the room.
 */
function turnEventsHTML(turn: AgentTurn, look: TurnLook, said: boolean): string {
  const request = said ? { author: t("rooms.owner"), text: turn.question.trim() } : undefined;
  const meetings = (turn.messages ?? []).filter(m => m.kind === "event").map(m => meetingEvent(m.text)).filter(event => event !== null);
  return (turn.handoffs ?? []).map(task => handoffLineHTML(task, look.memberFace?.(task.member_id) ?? "", request)).join("") +
    meetings.map(event => `<div class="conversation-event-line">${btn(eventLineHTML(t("rooms.meetingOpened", { p0: NAME }), ico("chat"), event.name), "event-open", ` data-meeting-room="${event.room_id}"`)}</div>`).join("");
}

/** The line of an exchange, named and faced after the member on its other side. */
function exchangeLine(turn: AgentTurn, look: TurnLook, tag: "li" | "div"): string {
  const peer = turn.peer_id ?? null;
  return exchangeHTML(
    turn,
    peer === null ? "" : (look.memberName?.(peer) ?? ""),
    look.openExchanges?.has(turn.id) ?? false,
    peer === null ? "" : (look.memberFace?.(peer) ?? ""),
    tag,
  );
}

/**
 * One question and its answer, with what was done in between; `inner` are the exchanges had while
 * it was answered, drawn before the answer.
 */
export function turnHTML(turn: AgentTurn, state: TimelineState, detailOpen: boolean, look: TurnLook, inner: readonly AgentTurn[] = []): string {
  if (isExchange(turn)) {
    // A request an asked member handed to the background (route "delegate", source "member") names
    // who asked in `peer_id`; one from before the core wrote it names no one, and its line then
    // says no name (not Raphael's) and shows no face.
    return exchangeLine(turn, look, "li");
  }
  const id = esc(turn.id);
  const query = look.query ?? "";
  const hasMsgs = (turn.messages?.length ?? 0) > 0;
  const settledDraft = isSettledDraftTurn(turn);
  const detail = !settledDraft && !hasMsgs && hasDetail(turn);
  // a routine's run says its routine in a line of its own, in place of the owner's words
  const routine = sourceOf(turn) === "routine";
  const said = turn.question.trim() !== "" && !routine;
  const asked = said || (turn.attachments?.length ?? 0) > 0;
  const live = turn.status === "running" || isLive(turn.task);
  const revealing = look.revealing?.(turn.id) ?? false;
  const meta = esc(answerMeta(turn));
  const more = detail
    ? `<div class="tn-foot">${btn(ico("down"), "sm icon tn-more", ` data-act="detail" aria-expanded="${detailOpen}" aria-controls="tn-detail-${id}" aria-label="${esc(t("agent.detail"))}" title="${esc(t("agent.detail"))}"`)}</div>`
    : "";
  const regenerate = !live && said && turn.id === look.regenerateTurnId;
  return (
    `<li class="tn" data-id="${id}" data-status="${esc(turn.status)}"${live ? ' data-live="true"' : ""}>` +
    (routine ? routineLineHTML(turn) : "") +
    (asked
      ? `<div class="me">` +
        `<div class="chat-message owner-message" data-message="question">` +
        bubbleHTML(said ? messageHTML(turn.question.trim(), query) : "", turn.attachments, turn.quote) + messageActionsHTML(turn.created_at_ms, false, live || turn.status === "confirm" || turn.task?.state === "waiting" ? "cancel" : "edit") + `</div>` +
        `</div>`
      : "") +
    turnEventsHTML(turn, look, said) +
    `<div class="ra${isBotRename(turn) ? " is-system-result" : ""}">` +
    (isBotRename(turn) ? "" : `<span class="ra-face"${meta ? ` title="${meta}"` : ""}>${look.face(live || revealing)}</span>`) + `<div class="ra-body">` +
    (hasMsgs
      ? turnMessagesHTML(turn, state, query, regenerate, inner.map((x) => exchangeLine(x, look, "div")).join(""))
      : workSectionHTML(turn, state, query, inner.map(x => exchangeLine(x, look, "div")).join("")) + ((turn.status === "running" && turn.text.trim() === "" || turn.status === "confirm" || settledDraft)
        ? "" : turn.text.trim() ? messageBlockHTML(turn, "reply", turn.text, turn.created_at_ms, query, state, true, regenerate) : "")) +
    (settledDraft ? settledDraftHTML(turn, detailOpen, query) : "") +
    (turn.status === "confirm" ? `<div class="turn-question" data-question-turn="${id}"></div>` : "") +
    (!live && revealing ? typingHTML() : "") +
    (detail ? `<div class="tn-detail" id="tn-detail-${id}"${detailOpen ? "" : " hidden"}>${messageHTML(turn.detail, query, true)}</div>` : "") +
    more +
    taskActionsHTML(turn, state) +
    `</div></div></li>`
  );
}

/** The heading of a day in the thread. */
export const dayHTML = (label: string): string => `<li class="day"><span>${esc(label)}</span></li>`;

/**
 * A turn that is not in the history yet: what the person typed (or, for a question said aloud,
 * a bubble with light passing over it until the words arrive) and Raphael thinking. A typed
 * question shows the chips of the files it was sent with.
 */
export function pendingTurnHTML(question: string | null, _source: string, look: TurnLook, files: readonly TurnAttachment[] = [], request?: { id: string; time: number; quote?: MessageQuote }): string {
  const words =
    question === null ? `<span class="shimmer">${esc(t("agent.hearing"))}</span>` : question.trim() === "" ? "" : messageHTML(question.trim());
  return (
    `<li class="tn is-pending"${request ? ` data-session="${esc(request.id)}"` : ""}>` +
    `<div class="me"><div class="chat-message owner-message" data-message="question">${bubbleHTML(words, files, request?.quote)}${request ? messageActionsHTML(request.time, false, "cancel") : ""}</div></div>` +
    `<div class="ra"><span class="ra-face">${look.face(true)}</span><div class="ra-body">` +
    typingHTML() +
    `</div></div></li>`
  );
}
