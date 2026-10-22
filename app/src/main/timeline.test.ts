import { afterEach, describe, expect, test, vi } from "vitest";
import { setLocale } from "./i18n";
import { groupSessions, sessionDays } from "./conversation";
import {
  formatDuration,
  listMove,
  markSeen,
  readableTool,
  reasonText,
  requestStop,
  sessionListHTML,
  stepTitle,
  stepsHTML,
  stepsOpen,
  summaryText,
  timelineHTML,
  turnHTML,
  wantsRefresh,
  workMs,
  emptyTimelineState,
} from "./timeline";
import type { AgentStep, AgentTask, AgentTurn } from "./types";

afterEach(() => setLocale("zh-TW"));

const at = (iso: string): number => Date.parse(iso);
const T0 = at("2026-10-20T09:00:00+08:00");

const step = (over: Partial<AgentStep> = {}): AgentStep => ({
  kind: "tool", tool: "weather", server: "Raphael", outcome: "ok", ms: 400, reason: null, at_ms: T0 + 1_000, ...over,
});

function turn(id: number, more: Partial<AgentTurn> = {}): AgentTurn {
  return {
    id, created_at_ms: T0, question: "台北天氣如何，順便記到行事曆", route: "answer", prefix: "答", text: "台北現在 24 度。",
    detail: null, status: "done", session_id: `press-${id}`, source: "voice", cost_usd: 0.0012, model: "openai/gpt-6-luna",
    steps: [], task: null, ...more,
  };
}

const THREE = [
  step({ tool: "weather", ms: 420, at_ms: T0 + 900 }),
  step({ tool: "web_search", ms: 1_800, at_ms: T0 + 2_300 }),
  step({ tool: "create-event", server: "Google Calendar", outcome: "error", reason: "auth", ms: 600, at_ms: T0 + 2_900 }),
];

/** What a sighted reader sees: the text outside the tags, without what only a screen reader hears. */
const visible = (html: string): string =>
  html.replace(/<span class="sr">.*?<\/span>/g, "").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();

const running = (over: Partial<AgentTask> = {}): AgentTask => ({ id: 41, state: "running", started_at_ms: T0, finished_at_ms: null, ...over });

describe("a step in words", () => {
  test("Raphael's own lookups, commands and a service's tools get readable names", () => {
    expect(stepTitle(step({ tool: "weather" }))).toBe("查天氣");
    expect(stepTitle(step({ tool: "web_search" }))).toBe("搜尋網路");
    expect(stepTitle(step({ kind: "command", tool: "volume_mute", server: null }))).toBe("靜音");
    expect(stepTitle(step({ tool: "notion-create_page", server: "Notion" }))).toBe("Create page");
    expect(stepTitle(step({ tool: null, server: null, outcome: "blocked" }))).toBe("沒有提供的工具");
    expect(readableTool("search_issues")).toBe("Search issues");
    setLocale("en");
    expect(stepTitle(step({ tool: "weather" }))).toBe("Weather");
  });

  test("a failure says why in a few words, from its kind; an unknown kind is a plain error", () => {
    expect(reasonText(step({ outcome: "error", reason: "auth" }))).toBe("需要重新登入");
    expect(reasonText(step({ outcome: "error", reason: "bad_arguments" }))).toBe("參數錯誤");
    expect(reasonText(step({ outcome: "timeout", reason: null }))).toBe("服務無回應");
    expect(reasonText(step({ outcome: "error", reason: "something new" }))).toBe("發生錯誤");
    expect(reasonText(step())).toBe("");
  });

  test("durations read as people say them", () => {
    expect(formatDuration(420)).toBe("0.4 秒");
    expect(formatDuration(2_340)).toBe("2.3 秒");
    expect(formatDuration(42_000)).toBe("42 秒");
    expect(formatDuration(72_000)).toBe("1 分 12 秒");
    expect(formatDuration(12)).toBe("<0.1 秒"); // a key press: never 0.0
    expect(formatDuration(-5)).toBe("<0.1 秒");
    expect(formatDuration(60)).toBe("0.1 秒");
  });
});

describe("running a task that did not finish again", () => {
  const ended = (state: string): AgentTurn => turn(5, { task: running({ id: 52, state, finished_at_ms: T0 + 5_000 }), steps: [step()] });

  test("a real bordered button for a task that was interrupted, stopped, out of a limit or failed", () => {
    for (const state of ["interrupted", "cancelled", "limit", "failed"]) {
      const html = stepsHTML(ended(state), emptyTimelineState());
      expect(html, state).toContain('<button type="button" class="btn sm rerun" data-act="rerun" data-task="52">重新執行</button>');
    }
  });

  test("none for a task that is done, one that is under way, or a turn with no task", () => {
    for (const task of [running({ id: 52, state: "done", finished_at_ms: T0 + 5_000 }), running({ id: 52 }), running({ id: 52, state: "queued" }), null]) {
      expect(stepsHTML(turn(5, { task, steps: [step()] }), emptyTimelineState())).not.toContain("rerun");
    }
  });

  test("in the language of the window", () => {
    setLocale("en");
    expect(stepsHTML(ended("limit"), emptyTimelineState())).toContain(">Run again</button>");
    setLocale("ja");
    expect(stepsHTML(ended("limit"), emptyTimelineState())).toContain(">もう一度実行</button>");
  });

  test("the button of a task being started again is off", () => {
    const state = emptyTimelineState();
    state.rerunning.add(52);
    expect(stepsHTML(ended("failed"), state)).toContain('data-task="52" disabled>');
  });
});

describe("what a turn did", () => {
  test("three steps, one failed: a closed summary with the count, the time and the failure", () => {
    const t1 = turn(1, { steps: THREE });
    const state = emptyTimelineState();
    const html = stepsHTML(t1, state);
    expect(html).toContain('data-open="false"');
    expect(html).toContain('aria-expanded="false" aria-controls="steps-1"');
    expect(html).toContain('id="steps-1" inert'); // closed: out of the way of the keyboard
    // the summary shows a mark, the count, the time and the failures; its words are its label
    const summary = /<button[^>]*steps-sum[^>]*>.*?<\/button>/.exec(html)![0];
    expect(visible(summary)).toBe("3 2.4 秒 1");
    expect(summary).toContain('aria-label="做了 3 件事 · 2.4 秒 · 1 件失敗"');
    expect(summaryText(t1, T0).time).toBe("2.4 秒"); // the first start (0.48 s) to the last end (2.9 s)
    expect(html.match(/class="step is-ok/g)).toHaveLength(2);
    expect(html).toContain("需要重新登入"); // a failure is said in words
    // the service is the picture's name, not a word beside the tool
    expect(html).toContain('role="img" aria-label="Google Calendar" title="Google Calendar"');
    expect(visible(html)).not.toContain("Google Calendar");
    expect(html).not.toContain('data-act="stop"');
  });

  test("a turn that did nothing has no summary at all", () => {
    expect(stepsHTML(turn(1), emptyTimelineState())).toBe("");
  });

  test("the model calls that went well are the thinking between steps: not rows, not counted", () => {
    const t1 = turn(1, { steps: [step({ kind: "model", tool: null, server: null }), step(), step({ kind: "model", tool: null, server: null, outcome: "error", reason: "network" })] });
    const html = stepsHTML(t1, emptyTimelineState());
    expect(html).toContain("做了 1 件事");
    expect(html.match(/<li class="step/g)).toHaveLength(2); // the lookup, and the thinking that failed
    expect(html).toContain("連不上服務");
  });

  test("a task looking through its own tools calls no service: no row", () => {
    const t1 = turn(1, { task: running({ state: "done", finished_at_ms: T0 + 9_000 }), steps: [step({ tool: "find_tools", ms: 3 }), step({ tool: "search_issues", server: "GitHub" })] });
    const html = stepsHTML(t1, emptyTimelineState());
    expect(html.match(/<li class="step/g)).toHaveLength(1);
    expect(html).toContain("Search issues");
  });

  test("the reader's choice beats the default, which is open while a task runs and closed once done", () => {
    const state = emptyTimelineState();
    expect(stepsOpen(turn(1, { steps: THREE }), state)).toBe(false);
    expect(stepsOpen(turn(2, { task: running() }), state)).toBe(true);
    state.open.set(1, true);
    state.open.set(2, false);
    expect(stepsOpen(turn(1, { steps: THREE }), state)).toBe(true);
    expect(stepsOpen(turn(2, { task: running() }), state)).toBe(false);
    expect(stepsHTML(turn(1, { steps: THREE }), state)).toContain('data-open="true"');
    expect(stepsHTML(turn(1, { steps: THREE }), state)).not.toContain(" inert");
  });

  test("a task under way: open, a turning summary, the time since it began, the thinking line and 中止", () => {
    const state = { ...emptyTimelineState(), nowMs: T0 + 42_000 };
    const html = stepsHTML(turn(3, { task: running(), steps: [step()] }), state);
    expect(html).toContain('data-open="true"');
    expect(html).toContain('aria-label="進行中 · 42 秒"');
    expect(html).toContain(`data-since="${T0}"`);
    expect(html).toContain('class="think" role="img" aria-label="思考中…"'); // a shimmer, no words
    expect(html).toContain('class="spin"');
    expect(html).toContain('<button type="button" class="btn sm icon stop" data-act="stop" data-task="41" aria-label="中止" title="中止">');
    expect(visible(html)).toBe("42 秒 查天氣 0.4 秒 42 秒");
  });

  test("a task that waits is queued, and one that was stopped says so with what it did", () => {
    expect(stepsHTML(turn(3, { task: running({ state: "queued", started_at_ms: null }) }), emptyTimelineState())).toContain("排隊中");
    const stopped = turn(3, { task: running({ state: "cancelled", finished_at_ms: T0 + 5_000 }), steps: [step()] });
    expect(summaryText(stopped, T0).lead).toBe("已中止 · 做了 1 件事");
    expect(workMs(stopped, T0 + 99_000)).toBe(5_000); // an ended task's time does not grow
  });

  test("a step that arrives is the only one that comes in moving", () => {
    const state = emptyTimelineState();
    const before = turn(3, { task: running(), steps: [step()] });
    markSeen([before], state);
    const after = turn(3, { task: running(), steps: [step(), step({ tool: "web_search" })] });
    const html = stepsHTML(after, state);
    expect(html.match(/is-new/g)).toHaveLength(1);
    expect(html).toMatch(/<li class="step is-ok is-new">.*搜尋網路/);
  });
});

describe("中止", () => {
  test("the button says it is stopping at once, and the task's id goes to the core", async () => {
    const state = emptyTimelineState();
    let finish!: (value: { cancelled: boolean }) => void;
    const cancel = vi.fn(() => new Promise<{ cancelled: boolean }>((resolve) => (finish = resolve)));
    const asked = requestStop(state, 41, cancel);
    expect(cancel).toHaveBeenCalledWith(41);
    const html = stepsHTML(turn(3, { task: running() }), state);
    expect(html).toContain('data-act="stop" data-task="41" aria-label="正在中止…" title="正在中止…" disabled>');
    finish({ cancelled: true });
    expect(await asked).toEqual({ cancelled: true });
    expect(state.stopping.has(41)).toBe(true); // until the task is seen to have ended
  });

  test("a stop that fails, or finds nothing to stop, gives the button back", async () => {
    const state = emptyTimelineState();
    const failed = await requestStop(state, 41, () => Promise.reject(new Error("core down")));
    expect(failed.cancelled).toBe(false);
    expect(failed.error).toBeInstanceOf(Error);
    expect(state.stopping.has(41)).toBe(false);
    await requestStop(state, 41, () => Promise.resolve({ cancelled: false }));
    expect(state.stopping.has(41)).toBe(false);
  });

  test("the page follows a turn or a task, and nothing else of the app", () => {
    expect(wantsRefresh({ type: "AgentTurn", turn_id: 3 })).toBe(true);
    expect(wantsRefresh({ type: "WorkerChanged", task_id: 41, state: "running", running: true })).toBe(true);
    expect(wantsRefresh({ type: "Level", value: 0.4 })).toBe(false);
  });
});

describe("a turn and a conversation", () => {
  test("the person on the right in 「」 with the time, Raphael's answer in 《》 with the model and cost", () => {
    const html = turnHTML(turn(5), emptyTimelineState(), false);
    expect(html).toContain('<div class="tn-who"><span class="sr">你說</span><time class="num"'); // the side says who
    expect(html).toContain(">09:00</time>");
    expect(html).toContain('<div class="bubble">「台北天氣如何，順便記到行事曆」</div>');
    expect(html).toContain('<p class="ans">《答。台北現在 24 度。》</p>');
    expect(html).toContain("gpt-6-luna · US$0.0012");
  });

  test("a detail is closed behind a bordered button that says whether it is open", () => {
    const closed = turnHTML(turn(5, { detail: "更多" }), emptyTimelineState(), false);
    expect(closed).toContain('class="btn sm icon tn-more" data-act="detail" aria-expanded="false" aria-controls="tn-detail-5" aria-label="詳細" title="詳細"');
    expect(closed).toContain('<div class="tn-detail" id="tn-detail-5" hidden>更多</div>');
    expect(turnHTML(turn(5, { detail: "更多" }), emptyTimelineState(), true)).toContain('<div class="tn-detail" id="tn-detail-5">更多</div>');
  });

  test("everything that came from outside is escaped, steps too", () => {
    const evil = '<img src=x onerror="alert(1)">';
    const html = turnHTML(turn(5, { question: evil, text: evil, detail: evil, model: evil, steps: [step({ tool: evil, server: evil, outcome: "error" })] }), emptyTimelineState(), true);
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;img");
  });

  test("the list: days, a source mark, the current one marked and the only one in the tab order", () => {
    const sessions = groupSessions([
      turn(3, { created_at_ms: T0 + 3_600_000, source: "telegram", session_id: "chat-1", question: "明天要帶傘嗎" }),
      turn(2, { created_at_ms: T0 + 60_000 }),
      turn(1),
    ]);
    const html = sessionListHTML(sessionDays(sessions, T0 + 3_700_000), sessions[1].key);
    expect(html).toContain("<h3>今天</h3>");
    expect(html.match(/<button type="button" class="sess"/g)).toHaveLength(2);
    expect(html).toContain('aria-current="true" tabindex="0"');
    expect(html.match(/tabindex="-1"/g)).toHaveLength(1);
    expect(html).toContain('title="Telegram"');
    expect(html).not.toContain("則"); // the time is enough
  });

  test("the arrow keys walk the list and stop at its ends; Home and End jump; other keys do nothing", () => {
    expect(listMove("ArrowDown", 0, 3)).toBe(1);
    expect(listMove("ArrowDown", 2, 3)).toBe(2);
    expect(listMove("ArrowUp", 0, 3)).toBe(0);
    expect(listMove("Home", 2, 3)).toBe(0);
    expect(listMove("End", 0, 3)).toBe(2);
    expect(listMove("Enter", 1, 3)).toBeNull(); // the button's own: it opens the conversation
    expect(listMove("ArrowDown", 0, 0)).toBeNull();
  });

  test("a conversation: its first question as the head, where and when, and its turns oldest first", () => {
    const [session] = groupSessions([turn(2, { created_at_ms: T0 + 60_000, question: "再一次" }), turn(1)]);
    const html = timelineHTML(session, { ...emptyTimelineState(), nowMs: T0 + 120_000 }, new Set());
    expect(html).toContain('<h2 class="tl-title">台北天氣如何，順便記到行事曆</h2>');
    expect(html).toContain("語音");
    expect(html.indexOf('data-id="1"')).toBeLessThan(html.indexOf('data-id="2"'));
  });
});
