// One conversation as the 拉斐爾 page shows it: the list of conversations on the left, and on the
// right, for each question, what the person said, what Raphael did in between (a step for each tool,
// lookup or command, under a summary that opens) and what Raphael answered. A background task shows
// its steps as they come, with the time it has taken and a button that stops it.
//
// The functions make HTML from plain data; `TimelineState` says what is open and which steps are new,
// so that the page can draw again at any time and only what changed moves. Every word that came from
// the core goes through `esc`.

import { t } from "./i18n";
import type { MessageKey } from "./i18n/zh-TW";

import { replyLine, userLine } from "../shared/anime";
import { type Session, type SessionDay, VOICE, hasDetail, sessionBusy, sessionTitle } from "./conversation";
import { dayLabel, formatClock, formatUsd } from "./format";
import { brandStyle, monogram, serviceIconOf, serviceIconSVG } from "./service-icons";
import type { AgentStep, AgentTask, AgentTurn, MainEvent } from "./types";
import { btn, esc, ico } from "./ui";

// ---------- the pictures this page adds to the app's icons ----------

const svg = (body: string, cls = "i"): string => `<svg class="${cls}" viewBox="0 0 16 16" aria-hidden="true">${body}</svg>`;
const PATHS = {
  send: "M14 2 7.2 8.8M14 2l-4.3 12-2.5-5.2L2 6.3z",
  cloud: "M4.8 12.6h6.8a2.8 2.8 0 0 0 .4-5.6 4 4 0 0 0-7.6-1A3.3 3.3 0 0 0 4.8 12.6z",
  check: "M3.6 8.4 6.6 11.2 12.4 4.8",
  cross: "M5 5l6 6M11 5l-6 6",
  stop: "M5 5h6v6H5z",
} as const;
const pic = (name: keyof typeof PATHS): string => svg(`<path d="${PATHS[name]}"/>`);

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
const REASONS: Record<string, MessageKey> = {
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
const TASK_ENDS: Record<string, MessageKey> = {
  cancelled: "agent.taskCancelled",
  limit: "agent.taskLimit",
  failed: "agent.taskFailed",
  interrupted: "agent.taskInterrupted",
};

/**
 * A tool's name made readable, as the connections show it: the service's own name at the start taken
 * off, the dashes and underscores as spaces, a capital first.
 */
export function readableTool(name: string, service = ""): string {
  let rest = name;
  const prefix = service.trim().toLowerCase();
  if (prefix) {
    for (const sep of ["-", "_"]) if (rest.toLowerCase().startsWith(prefix + sep) && rest.length > prefix.length + 1) rest = rest.slice(prefix.length + 1);
  }
  const spaced = rest.replace(/[-_]+/g, " ").trim();
  return spaced ? spaced.charAt(0).toUpperCase() + spaced.slice(1) : name;
}

/** What a step did, in the language of the window. */
export function stepTitle(step: AgentStep): string {
  if (step.kind === "model") return t("agent.stepThinking");
  if (!step.tool) return t("agent.stepUnknown");
  if (step.kind === "command") return COMMAND_TITLES[step.tool] ? plain(COMMAND_TITLES[step.tool]) : readableTool(step.tool);
  if (step.server === RAPHAEL && BUILTIN_TITLES[step.tool]) return plain(BUILTIN_TITLES[step.tool]);
  return readableTool(step.tool, step.server ?? "");
}

/** Where a step happened: the service, Raphael, or this PC. */
export function stepPlace(step: AgentStep): string {
  if (step.kind === "command") return t("agent.thisPc");
  if (step.kind === "model" || step.server === RAPHAEL || !step.server) return t("agent.title");
  return step.server;
}

/** Why a step failed, in a few words; empty for one that went well. */
export function reasonText(step: AgentStep): string {
  if (step.outcome === "ok") return "";
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

/** A background task looking through the tools it was given: no service is called, so it is no step to the reader. */
const bookkeeping = (step: AgentStep): boolean => step.server === RAPHAEL && step.tool === "find_tools" && step.outcome === "ok";

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
  /** The steps drawn before; any other is new and comes in with a movement. */
  seen: Set<string>;
  /** Tasks the reader asked to stop, which have not stopped yet. */
  stopping: Set<number>;
  nowMs: number;
}

export const emptyTimelineState = (): TimelineState => ({ open: new Map(), seen: new Set(), stopping: new Set(), nowMs: Date.now() });

/** Whether a turn's steps are open: as the reader left them, else open while its task runs and closed once it is done. */
export const stepsOpen = (turn: AgentTurn, state: TimelineState): boolean => state.open.get(turn.id) ?? isLive(turn.task);

/** The picture of a step: the service's mark, Raphael's own for a lookup, the PC for a command. */
function stepIcon(step: AgentStep): string {
  if (step.kind === "command") return `<span class="step-ic">${ico("monitor")}</span>`;
  if (step.kind === "model") return `<span class="step-ic">${ico("sparkle")}</span>`;
  if (!step.server || step.server === RAPHAEL) {
    const own = step.tool === "weather" ? pic("cloud") : step.tool === "web_search" ? ico("globe") : step.tool === "local_time" ? ico("clock") : ico("search");
    return `<span class="step-ic">${own}</span>`;
  }
  const mark = serviceIconOf({ name: step.server });
  return mark
    ? `<span class="step-ic logo-mono"${brandStyle(mark)}>${serviceIconSVG(mark)}</span>`
    : `<span class="step-ic letter">${monogram(step.server)}</span>`;
}

/** One step: what, where, how long, and whether it worked (a mark and a word, never the colour alone). */
export function stepHTML(step: AgentStep, isNew: boolean): string {
  const ok = step.outcome === "ok";
  const why = reasonText(step);
  return (
    `<li class="step ${ok ? "is-ok" : "is-bad"}${isNew ? " is-new" : ""}">` +
    stepIcon(step) +
    `<span class="step-main"><span class="step-t">${esc(stepTitle(step))}</span><span class="step-s">${esc(stepPlace(step))}</span>` +
    (ok ? "" : `<span class="step-why">${esc(why)}</span>`) +
    `</span>` +
    `<span class="step-end"><span class="step-ms num">${esc(formatDuration(step.ms))}</span>` +
    `<span class="step-mark" role="img" aria-label="${esc(ok ? t("agent.ok") : why)}">${ok ? pic("check") : pic("cross")}</span></span>` +
    `</li>`
  );
}

/** The line that says a task is still at work: the thinking between steps, with the time since it began. */
function liveStepHTML(task: AgentTask, nowMs: number): string {
  const since = task.started_at_ms;
  return (
    `<li class="step is-live">` +
    `<span class="step-ic">${SPINNER}</span>` +
    `<span class="step-main"><span class="step-t shimmer">${esc(task.state === "queued" ? t("agent.queued") : t("agent.thinking"))}</span></span>` +
    `<span class="step-end">${since ? `<span class="step-ms num" data-since="${since}">${esc(formatDuration(nowMs - since))}</span>` : ""}</span>` +
    `</li>`
  );
}

/** The words of the summary: how many things were done, how long it took, how many failed; or that it is under way. */
export function summaryText(turn: AgentTurn, nowMs: number): { lead: string; time: string; failed: number } {
  const steps = shownSteps(turn).filter((step) => step.kind !== "model");
  const failed = shownSteps(turn).filter((step) => step.outcome !== "ok").length;
  const task = turn.task;
  const done = steps.length === 1 ? t("agent.stepsDoneOne") : t("agent.stepsDone", { p0: steps.length });
  let lead = done;
  if (isLive(task)) lead = task!.state === "queued" ? t("agent.queued") : t("agent.working");
  else if (task && TASK_ENDS[task.state]) lead = `${plain(TASK_ENDS[task.state])} · ${done}`;
  const ms = workMs(turn, nowMs);
  return { lead, time: ms > 0 || isLive(task) ? formatDuration(ms) : "", failed };
}

/** What Raphael did for a turn, under a summary that opens; nothing for a turn that did nothing. */
export function stepsHTML(turn: AgentTurn, state: TimelineState): string {
  const steps = shownSteps(turn);
  const task = turn.task ?? null;
  const live = isLive(task);
  if (steps.length === 0 && !task) return "";
  const open = stepsOpen(turn, state);
  const { lead, time, failed } = summaryText(turn, state.nowMs);
  const id = `steps-${turn.id}`;
  const since = live && task?.started_at_ms ? ` data-since="${task.started_at_ms}"` : "";
  const summary =
    `<button type="button" class="btn sm steps-sum" data-act="steps" aria-expanded="${open}" aria-controls="${id}">` +
    `<span class="steps-lead">${live ? SPINNER : ico("down")}<span>${esc(lead)}</span></span>` +
    (time ? `<span class="steps-time num"${since}>${esc(time)}</span>` : "") +
    (failed ? `<span class="steps-bad">${esc(t("agent.stepsFailed", { p0: failed }))}</span>` : "") +
    `</button>`;
  const stopping = live && state.stopping.has(task!.id);
  const stop = live
    ? btn(`${pic("stop")}<span>${esc(stopping ? t("agent.stopping") : t("agent.stop"))}</span>`, "sm stop", ` data-act="stop" data-task="${esc(task!.id)}"${stopping ? " disabled" : ""}`)
    : "";
  const rows = steps.map((step, index) => stepHTML(step, !state.seen.has(stepKey(turn, index)))).join("") + (live ? liveStepHTML(task!, state.nowMs) : "");
  return (
    `<div class="steps${live ? " is-live" : ""}" data-open="${open}">` +
    `<div class="steps-bar">${summary}${stop}</div>` +
    `<div class="steps-body" id="${id}"${open ? "" : " inert"}><div class="steps-clip">` +
    `<ol class="steps-list" aria-label="${esc(t("agent.stepsList"))}">${rows}</ol>` +
    `</div></div></div>`
  );
}

/** Whether an event of the app changes what the conversations show: a turn, or a task's step. */
export const wantsRefresh = (event: MainEvent): boolean => event.type === "AgentTurn" || event.type === "WorkerChanged";

/**
 * 「中止」: the task is marked as stopping at once, so its button says so, and the core is asked to
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

/** Marks every step of `turns` as drawn, so that the next draw moves only the ones that come after. */
export function markSeen(turns: readonly AgentTurn[], state: TimelineState): void {
  for (const turn of turns) shownSteps(turn).forEach((_, index) => state.seen.add(stepKey(turn, index)));
}

// ---------- a turn ----------

/** The quiet line under an answer: the model that worded it and what the turn cost. */
function answerMeta(turn: AgentTurn): string {
  const parts: string[] = [];
  if (turn.status === "confirm") parts.push(t("agent.waiting"));
  if (turn.model) parts.push(shortModel(turn.model));
  if (turn.cost_usd && turn.cost_usd > 0) parts.push(formatUsd(turn.cost_usd));
  return parts.length ? `<span class="tn-meta num">${parts.map(esc).join(" · ")}</span>` : "";
}

/** One question and its answer, with what was done in between. */
export function turnHTML(turn: AgentTurn, state: TimelineState, detailOpen: boolean): string {
  const id = esc(turn.id);
  const detail = hasDetail(turn);
  const asked = turn.question.trim() !== "";
  const foot =
    (detail ? btn(`${ico("down")}<span>${esc(t("agent.detail"))}</span>`, "sm tn-more", ` data-act="detail" aria-expanded="${detailOpen}" aria-controls="tn-detail-${id}"`) : "") + answerMeta(turn);
  return (
    `<li class="tn" data-id="${id}" data-status="${esc(turn.status)}"${isLive(turn.task) ? ' data-live="true"' : ""}>` +
    (asked
      ? `<div class="tn-q"><div class="tn-who"><span>${esc(t("agent.youSaid"))}</span><time class="num" datetime="${esc(new Date(turn.created_at_ms).toISOString())}">${esc(formatClock(turn.created_at_ms))}</time></div>` +
        `<div class="bubble">${esc(userLine(turn.question))}</div></div>`
      : "") +
    stepsHTML(turn, state) +
    `<div class="tn-a"><p class="ans">${esc(replyLine(turn.prefix, turn.text))}</p>` +
    (detail ? `<div class="tn-detail" id="tn-detail-${id}"${detailOpen ? "" : " hidden"}>${esc(turn.detail)}</div>` : "") +
    (foot ? `<div class="tn-foot">${foot}</div>` : "") +
    `</div></li>`
  );
}

// ---------- the list of conversations ----------

/** The picture of where a conversation came from. */
export const sourceIcon = (source: string): string => (source === VOICE ? ico("mic") : pic("send"));

/** Where a conversation came from, in words. */
export function sourceName(source: string): string {
  if (source === VOICE) return t("agent.sourceVoice");
  if (source === "telegram") return t("agent.sourceTelegram");
  return source.charAt(0).toUpperCase() + source.slice(1);
}

const turnCount = (n: number): string => (n === 1 ? t("agent.turnCountOne") : t("agent.turnCount", { p0: n }));

/** One conversation in the list: a real button, the current one marked, only it in the tab order. */
export function sessionButtonHTML(session: Session, current: boolean): string {
  const busy = sessionBusy(session);
  return (
    `<button type="button" class="sess" data-act="session" data-key="${esc(session.key)}" aria-current="${current}" tabindex="${current ? 0 : -1}">` +
    `<span class="sess-src" title="${esc(sourceName(session.source))}">${sourceIcon(session.source)}</span>` +
    `<span class="sess-main"><span class="sess-title">${esc(sessionTitle(session))}</span>` +
    `<span class="sess-meta"><span class="sr">${esc(sourceName(session.source))} · </span><span class="num">${esc(formatClock(session.endMs))}</span> · ${esc(turnCount(session.turns.length))}</span></span>` +
    (busy ? `<span class="sess-busy" role="img" aria-label="${esc(t("agent.working"))}"></span>` : "") +
    `</button>`
  );
}

/**
 * Where a key moves the focus in the list of `count` conversations from the one at `at`: the arrows
 * one up or down (stopping at the ends), Home and End to the ends. Null for any other key.
 */
export function listMove(key: string, at: number, count: number): number | null {
  const to = key === "ArrowDown" ? at + 1 : key === "ArrowUp" ? at - 1 : key === "Home" ? 0 : key === "End" ? count - 1 : null;
  return to === null || count === 0 ? null : Math.max(0, Math.min(count - 1, to));
}

/** The conversations under their days. */
export function sessionListHTML(days: readonly SessionDay[], currentKey: string | null): string {
  return days
    .map(
      (day) =>
        `<div class="sess-day"><h3>${esc(day.label)}</h3>` +
        day.sessions.map((session) => sessionButtonHTML(session, session.key === currentKey)).join("") +
        `</div>`,
    )
    .join("");
}

/** The head of a conversation: its first question, and where, when, how many and what it cost. */
export function sessionHeadHTML(session: Session, nowMs: number): string {
  const cost = session.turns.reduce((sum, turn) => sum + (turn.cost_usd ?? 0), 0);
  const when = `${dayLabel(session.startMs, nowMs)} ${formatClock(session.startMs)}`;
  const meta = [esc(when), esc(turnCount(session.turns.length)), ...(cost > 0 ? [esc(formatUsd(cost))] : [])];
  return (
    `<header class="tl-head"><h2 class="tl-title">${esc(sessionTitle(session))}</h2>` +
    `<div class="tl-meta"><span class="tl-src">${sourceIcon(session.source)}<span>${esc(sourceName(session.source))}</span></span>` +
    meta.map((part) => `<span class="dot-sep" aria-hidden="true">·</span><span class="num">${part}</span>`).join("") +
    `</div></header>`
  );
}

/** A whole conversation: its head and its turns, oldest first. */
export function timelineHTML(session: Session, state: TimelineState, details: ReadonlySet<number>): string {
  return (
    sessionHeadHTML(session, state.nowMs) +
    `<ol class="tl" aria-label="${esc(sessionTitle(session))}">${session.turns.map((turn) => turnHTML(turn, state, details.has(turn.id))).join("")}</ol>`
  );
}

/** The two bubbles of the empty page: drawn here so that they take the colours of the theme. */
export const EMPTY_ART =
  `<svg class="empty-art" viewBox="0 0 160 120" aria-hidden="true">` +
  `<path class="b2" d="M86 26h32a14 14 0 0 1 14 14v11a14 14 0 0 1-14 14h-4l-8 7v-7H86a14 14 0 0 1-14-14V40a14 14 0 0 1 14-14Z"/>` +
  `<path class="b1" d="M43 45h47a16 16 0 0 1 16 16v17a16 16 0 0 1-16 16H57l-12 9v-9h-2a16 16 0 0 1-16-16V61a16 16 0 0 1 16-16Z"/>` +
  `<g class="wave"><rect x="48" y="66" width="3" height="7" rx="1.5"/><rect x="56" y="62" width="3" height="15" rx="1.5"/>` +
  `<rect x="64" y="58" width="3" height="23" rx="1.5"/><rect x="72" y="63" width="3" height="13" rx="1.5"/><rect x="80" y="66" width="3" height="7" rx="1.5"/></g>` +
  `<circle class="s1" cx="39" cy="29" r="2"/><circle class="s2" cx="126" cy="85" r="2.5"/><circle class="s3" cx="117" cy="98" r="1.5"/>` +
  `</svg>`;
