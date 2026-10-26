import { afterEach, describe, expect, test, vi } from "vitest";
import { setLocale } from "./i18n";
import { ico } from "./ui";
import {
  type TurnLook,
  dayHTML,
  forgetReruns,
  formatDuration,
  markSeen,
  noteTaskEnds,
  pendingTurnHTML,
  stepToolTip,
  stepPlace,
  stepHTML,
  shownSteps,
  toolAccess,
  reasonText,
  requestStop,
  sourceIcon,
  sourceName,
  stepTitle,
  stepsHTML,
  stepsOpen,
  summaryText,
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

test("a live turn renders ordered public bubbles and event chips without raw steps", () => {
  const html = turnHTML(turn(1, { status: "running", text: "", steps: THREE, messages: [
    { seq: 1, kind: "progress", text: "I will check the examples.", at_ms: T0 },
    { seq: 2, kind: "event", text: "Example saved", at_ms: T0 },
    { seq: 3, kind: "reply", text: "Your turn?", at_ms: T0 },
  ] }), emptyTimelineState(), false, { face: () => "", plain: true });
  expect(html.indexOf("I will check")).toBeLessThan(html.indexOf("Example saved"));
  expect(html.indexOf("Example saved")).toBeLessThan(html.indexOf("Your turn?"));
  expect(html).toContain('class="turn-event"');
  expect(html).toContain('class="work-progress"');
  expect(html).toContain('class="work-time"');
  expect(html.match(/<i><\/i>/g)).toHaveLength(3);
  expect(html).not.toContain("steps-line");
  expect(html).toContain("Google Calendar");
});

test("an old turn without messages still renders its answer once", () => {
  const html = turnHTML(turn(1), emptyTimelineState(), false, { face: () => "" });
  expect(html.match(/class="ans"/g)).toHaveLength(1);
  expect(html).toContain("《答。台北現在 24 度。》");
});

test.each([false, true])("a durable receipt renders outside assistant bubbles with public messages=%s", (hasMessages) => {
  const messages = hasMessages ? [{ seq: 1, kind: "reply" as const, text: "已轉交。", at_ms: T0 }] : [];
  const html = turnHTML(turn(1, { messages, handoffs: [{ task_id: 42, source_turn_id: 1, work_turn_id: 2,
    member_id: 3, member_name: "小晴", from_member_id: 0, stage_id: "report", mode: "work", state: "queued",
    reason: null, created_at_ms: T0, ready_at_ms: T0, dispatch_policy: "immediate", result: "", evidence: [] }] }),
    emptyTimelineState(), false, { face: () => "" });
  expect(html.match(/data-handoff="42"/g)).toHaveLength(1);
  expect(html.indexOf('class="handoff-event"')).toBeLessThan(html.indexOf('class="ra"'));
  expect(visible(html)).toContain("已轉交給 小晴");
});

test("an unknown tool connection has a plug with its accessible name, never an initial", () => {
  const html = stepHTML(step({ server: "Files", tool: "list" }), false);
  expect(html).toContain(`<span class="step-ic" role="img" aria-label="Files" title="Files">${ico("plug")}</span>`);
  expect(html).not.toContain(">F<");
  expect(html).not.toContain('class="step-ic letter"');
});

describe("a step in words", () => {
  test("Raphael's own lookups and commands have words of their own, in the language of the window", () => {
    expect(stepTitle(step({ tool: "weather" }))).toBe("查天氣");
    expect(stepTitle(step({ tool: "web_search" }))).toBe("搜尋網路");
    expect(stepTitle(step({ kind: "command", tool: "volume_mute", server: null }))).toBe("靜音");
    expect(stepTitle(step({ tool: null, server: null, outcome: "blocked" }))).toBe("沒有提供的工具");
    expect(stepToolTip(step({ tool: "weather" }))).toBe("");
    expect(stepToolTip(step({ kind: "command", tool: "volume_mute", server: null }))).toBe("");
    setLocale("en");
    expect(stepTitle(step({ tool: "weather" }))).toBe("Weather");
  });

  test("a service's tool is the service and whether it read or changed, never its English name", () => {
    const tools: [string, string, string][] = [
      ["notion-create_page", "Notion", "Notion：修改"],
      ["notion-search", "Notion", "Notion：讀取"],
      ["list_issues", "GitHub", "GitHub：讀取"],
      ["createPages", "Notion", "Notion：修改"],
      ["getMe", "Telegram", "Telegram：讀取"],
      ["create-event", "Google Calendar", "Google Calendar：修改"],
      ["frobnicate", "Odd", "Odd：修改"], // a name that is no known verb is counted as one that changes things
    ];
    for (const [tool, server, title] of tools) {
      expect(stepTitle(step({ tool, server })), tool).toBe(title);
      expect(stepToolTip(step({ tool, server })), tool).toBe(tool);
    }
    // a command or a lookup with no words of its own says so in the window's language, its name in the tooltip
    expect(stepTitle(step({ kind: "command", tool: "something_new", server: null }))).toBe("操作這台電腦");
    expect(stepToolTip(step({ kind: "command", tool: "something_new", server: null }))).toBe("something_new");
    expect(stepTitle(step({ tool: "get_news" }))).toBe("拉斐爾：讀取");
    setLocale("en");
    expect(stepTitle(step({ tool: "list_issues", server: "GitHub" }))).toBe("GitHub: read");
    expect(stepTitle(step({ tool: "create_issue", server: "GitHub" }))).toBe("GitHub: change");
    setLocale("ja");
    expect(stepTitle(step({ tool: "list_issues", server: "GitHub" }))).toBe("GitHub：読み取り");
  });

  test("a step's line shows the words and keeps the tool's own name only in its tooltip", () => {
    const html = stepsHTML(turn(9, { steps: [step({ tool: "list_issues", server: "GitHub" })] }), emptyTimelineState());
    expect(html).toContain('<span class="step-t" title="list_issues">GitHub：讀取</span>');
    expect(visible(html)).not.toContain("list_issues");
    expect(visible(html)).not.toMatch(/List issues/i);
  });

  test("read or change is told by the name's first word, as the connections group them", () => {
    expect(toolAccess("search_issues")).toBe("read");
    expect(toolAccess("fetchPage")).toBe("read");
    expect(toolAccess("HTTPGet")).toBe("write"); // the first word is HTTP
    expect(toolAccess("update_page")).toBe("write");
    expect(toolAccess("github.list_repos", "GitHub")).toBe("read");
    expect(toolAccess("")).toBe("write");
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

  test("a mark is dropped when its task is no longer shown, so a reused id starts fresh", () => {
    const state = emptyTimelineState();
    state.rerunning.add(52);
    state.rerunning.add(7);
    forgetReruns(state, [ended("failed")]);
    expect([...state.rerunning]).toEqual([52]);
    forgetReruns(state, []);
    expect(state.rerunning.size).toBe(0);
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
  test("three steps, one failed: one closed line with the count, the time and the failure", () => {
    const t1 = turn(1, { steps: THREE });
    const state = emptyTimelineState();
    const html = stepsHTML(t1, state);
    expect(html).toContain('data-open="false"');
    expect(html).toContain('aria-expanded="false" aria-controls="steps-1"');
    expect(html).toContain('id="steps-1" inert'); // closed: out of the way of the keyboard
    // 「› 3 個動作 · 2.4 秒」: a real button whose words are its label too
    const summary = /<button[^>]*steps-sum[^>]*>.*?<\/button>/.exec(html)![0];
    expect(summary).toContain('<button type="button" class="btn steps-sum"');
    expect(visible(summary)).toBe("3 個動作 · 2.4 秒 · 1 件失敗");
    expect(summary).toContain('aria-label="3 個動作 · 2.4 秒 · 1 件失敗"');
    expect(summaryText(t1, T0).time).toBe("2.4 秒"); // the first start (0.48 s) to the last end (2.9 s)
    expect(html.match(/class="step is-ok/g)).toHaveLength(2);
    expect(html).toContain("需要重新登入"); // a failure is said in words
    // the service is the picture's name, and its tool is said as the service and what it did
    expect(html).toContain('role="img" aria-label="Google Calendar" title="Google Calendar"');
    expect(visible(html)).toContain("Google Calendar：修改");
    expect(visible(html)).not.toContain("create-event");
    expect(html).not.toContain('data-act="stop"');
    expect(html).not.toContain('class="steps task');
  });

  test("one thing done is one action, in each language", () => {
    expect(visible(stepsHTML(turn(1, { steps: [step()] }), emptyTimelineState()))).toMatch(/^1 個動作 · 0.4 秒/);
    setLocale("en");
    expect(visible(stepsHTML(turn(1, { steps: THREE }), emptyTimelineState()))).toMatch(/^3 actions · 2.4 ?s · 1 failed/);
    expect(visible(stepsHTML(turn(1, { steps: [step()] }), emptyTimelineState()))).toMatch(/^1 action · /);
  });

  test("a turn that did nothing has no line at all", () => {
    expect(stepsHTML(turn(1), emptyTimelineState())).toBe("");
  });

  test("the model calls that went well are the thinking between steps: not rows, not counted", () => {
    const t1 = turn(1, { steps: [step({ kind: "model", tool: null, server: null }), step(), step({ kind: "model", tool: null, server: null, outcome: "error", reason: "network" })] });
    const html = stepsHTML(t1, emptyTimelineState());
    expect(html).toContain("1 個動作");
    expect(html.match(/<li class="step/g)).toHaveLength(2); // the lookup, and the thinking that failed
    expect(html).toContain("連不上服務");
  });

  test("a task looking through its own tools calls no service: no row", () => {
    const t1 = turn(1, { task: running({ state: "done", finished_at_ms: T0 + 9_000 }), steps: [step({ tool: "find_tools", ms: 3 }), step({ tool: "search_issues", server: "GitHub" })] });
    const html = stepsHTML(t1, emptyTimelineState());
    expect(html.match(/<li class="step/g)).toHaveLength(1);
    expect(html).toContain("GitHub：讀取");
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

  test("a task under way is a card: open, a turning ring, the time since it began, the step it is at and 停止", () => {
    const state = { ...emptyTimelineState(), nowMs: T0 + 42_000 };
    const html = stepsHTML(turn(3, { task: running(), steps: [step()] }), state);
    expect(html).toContain('<div class="steps task is-live" data-open="true">');
    expect(html).toContain('aria-label="進行中 · 42 秒"');
    expect(html).toContain(`data-since="${T0}"`);
    expect(html).toContain('<span class="step-t shimmer">思考中…</span>'); // the live step, with light passing over it
    expect(html).toContain('class="spin"');
    expect(html).toContain('<button type="button" class="btn sm stop" data-act="stop" data-task="41">停止</button>');
    expect(visible(html)).toBe("進行中 42 秒 停止 查天氣 0.4 秒 思考中… 42 秒");
  });

  test("a task that waits is queued, and one that was stopped says so with what it did", () => {
    expect(stepsHTML(turn(3, { task: running({ state: "queued", started_at_ms: null }) }), emptyTimelineState())).toContain("排隊中");
    const stopped = turn(3, { task: running({ state: "cancelled", finished_at_ms: T0 + 5_000 }), steps: [step()] });
    expect(summaryText(stopped, T0).lead).toBe("已中止 · 1 個動作");
    expect(workMs(stopped, T0 + 99_000)).toBe(5_000); // an ended task's time does not grow
  });

  test("a task that waits for the owner's yes says so, with a clock and 停止, and does not tick", () => {
    const html = stepsHTML(turn(3, { task: running({ state: "waiting" }), steps: [step()] }), { ...emptyTimelineState(), nowMs: T0 + 42_000 });
    expect(html).toContain('<div class="steps task" data-open="false">');
    expect(visible(html)).toMatch(/^等你處理 /);
    expect(html).toContain('<path d="' + "M"); // an icon, not the turning ring
    expect(html).not.toContain('class="spin"');
    expect(html).not.toContain("data-since");
    expect(html).toContain('<button type="button" class="btn sm stop" data-act="stop" data-task="41">停止</button>');
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

  test("a task seen under way that is done now: its check pops in once", () => {
    const state = emptyTimelineState();
    const live = turn(3, { task: running(), steps: [step()] });
    const done = turn(3, { task: running({ state: "done", finished_at_ms: T0 + 9_000 }), steps: [step()] });
    noteTaskEnds([live], state);
    expect(stepsHTML(live, state)).not.toContain("task-check");
    noteTaskEnds([done], state);
    expect(stepsHTML(done, state)).toContain('<span class="task-check is-pop">');
    noteTaskEnds([done], state);
    expect(stepsHTML(done, state)).toContain('<span class="task-check">');
    // a task that was done before the page saw it does not pop
    const fresh = emptyTimelineState();
    noteTaskEnds([done], fresh);
    expect(stepsHTML(done, fresh)).not.toContain("is-pop");
  });
});

describe("停止", () => {
  test("the button says it is stopping at once, and the task's id goes to the core", async () => {
    const state = emptyTimelineState();
    let finish!: (value: { cancelled: boolean }) => void;
    const cancel = vi.fn(() => new Promise<{ cancelled: boolean }>((resolve) => (finish = resolve)));
    const asked = requestStop(state, 41, cancel);
    expect(cancel).toHaveBeenCalledWith(41);
    const html = stepsHTML(turn(3, { task: running() }), state);
    expect(html).toContain('data-act="stop" data-task="41" disabled>正在停止…</button>');
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
    expect(wantsRefresh({ type: "AgentQuestionClosed", id: "c-1" })).toBe(false);
  });
});

/** How the tests draw a turn: a face that says whether it moves. */
const LOOK: TurnLook = { face: (loop) => `<i class="face${loop ? " loop" : ""}"></i>` };

describe("a turn", () => {
  test("a request handed to the background by a member is one exchange line, named for whoever asked", () => {
    const delegate = turn(8, { route: "delegate", source: "member", prefix: "", member_id: 4, peer_id: 3, question: "整理報告", text: "我交給背景處理了" });
    const look: TurnLook = { ...LOOK, memberName: (id) => (id === 3 ? "小幫手" : "拉斐爾"), memberFace: () => "<i></i>" };
    const html = turnHTML(delegate, emptyTimelineState(), false, look);
    expect(html).toContain('class="tn tn-x"');
    expect(html).toContain("<b>小幫手</b>");
    expect(html).not.toContain('class="bubble"');
  });

  test("the same turn with no one named says no name and no dangling colon, and is no broken row", () => {
    const delegate = turn(8, { route: "delegate", source: "member", prefix: "", member_id: 4, peer_id: null, question: "整理報告", text: "我交給背景處理了" });
    const look: TurnLook = { ...LOOK, memberName: () => "拉斐爾", memberFace: () => "<i></i>" };
    const html = turnHTML(delegate, emptyTimelineState(), false, look);
    expect(html).toContain('class="tn tn-x"');
    expect(html).not.toContain("<b>");
    expect(html).not.toContain("拉斐爾");
    expect(html).toContain("<span>2 則訊息往來</span>");
  });

  test("the person on the right without 「」 with where and when; Raphael on the left with his face and 《》", () => {
    const html = turnHTML(turn(5), emptyTimelineState(), false, LOOK);
    expect(html).not.toContain('class="me-src"');
    expect(html).toContain('data-act="edit-message"');
    expect(html).toContain('<div class="bubble">台北天氣如何，順便記到行事曆</div>');
    expect(html).toContain('<span class="ra-face" title="gpt-6-luna · US$0.0012"><i class="face"></i></span>');
    expect(html).toContain('<div class="ans"><div class="message-text"><p>《答。台北現在 24 度。》</p>');
    expect(html).not.toContain("tn-foot"); // nothing more to open
  });

  test("source data stays available but no source marks are drawn", () => {
    for (const source of ["voice", "telegram", "app"]) {
      const html = turnHTML(turn(5, { source }), emptyTimelineState(), false, LOOK);
      expect(html).not.toContain('class="me-src"');
      expect(pendingTurnHTML("Pending", source, LOOK)).not.toContain('class="me-src"');
    }
    expect(new Set(["voice", "telegram", "app"].map(sourceIcon)).size).toBe(3);
    setLocale("en");
    expect(sourceName("app")).toBe("Typed");
  });

  test("while its task runs Raphael's face moves; a turn not asked aloud has no bubble", () => {
    expect(turnHTML(turn(5, { task: running() }), emptyTimelineState(), false, LOOK)).toContain('<i class="face loop">');
    expect(turnHTML(turn(5, { question: " " }), emptyTimelineState(), false, LOOK)).not.toContain("bubble");
  });

  test("a detail is closed behind a button that says whether it is open", () => {
    const closed = turnHTML(turn(5, { detail: "更多" }), emptyTimelineState(), false, LOOK);
    expect(closed).toContain('class="btn sm icon tn-more" data-act="detail" aria-expanded="false" aria-controls="tn-detail-5" aria-label="詳細" title="詳細"');
    expect(closed).toContain('<div class="tn-detail" id="tn-detail-5" hidden><p>更多</p></div>');
    expect(turnHTML(turn(5, { detail: "更多" }), emptyTimelineState(), true, LOOK)).toContain('<div class="tn-detail" id="tn-detail-5"><p>更多</p></div>');
  });

  test("the words looked for are marked in the question, the answer and the detail", () => {
    const html = turnHTML(turn(5, { detail: "台北晚上轉晴" }), emptyTimelineState(), true, { ...LOOK, query: "台北" });
    expect(html.match(/<mark>台北<\/mark>/g)).toHaveLength(3);
  });

  test("everything that came from outside is escaped, steps too", () => {
    const evil = '<img src=x onerror="alert(1)">';
    const html = turnHTML(turn(5, { question: evil, text: evil, detail: evil, model: evil, steps: [step({ tool: evil, server: evil, outcome: "error" })] }), emptyTimelineState(), true, { ...LOOK, query: "img" });
    expect(html).not.toContain("<img");
    expect(html).toContain("&lt;");
  });

  test("a turn that is not in the history yet: the words typed, or a shimmer for words being heard, and Raphael thinking", () => {
    const typed = pendingTurnHTML("明天會下雨嗎？", "app", LOOK);
    expect(typed).toContain('<div class="bubble">明天會下雨嗎？</div>');
    expect(typed).toContain('<i class="face loop">');
    expect(typed).toContain('class="think" role="img" aria-label="思考中…"');
    expect(pendingTurnHTML(null, "voice", LOOK)).toContain('<span class="shimmer">正在辨識…</span>');
    expect(pendingTurnHTML("<b>", "app", LOOK)).toContain("&lt;b&gt;");
  });

  test.each(["openai/gpt-6-luna", "cli:claude:sonnet", "cli:codex", "cli:gemini"])("a %s member turn with thinking, progress, media cards, event chip, and multiple replies renders in order without duplicating turn.text", (model) => {
    const messages = [
      { seq: 1, kind: "thinking" as const, text: "主人想看今天的 NHK 新聞與相關影片，先檢索重點報導。", at_ms: T0 + 1_000 },
      { seq: 2, kind: "progress" as const, text: "先幫你找今天的 NHK 重點新聞。", at_ms: T0 + 2_000 },
      { seq: 3, kind: "thinking" as const, text: "已找到新聞頁面與 YouTube 報導影片，整理成卡片附上。", at_ms: T0 + 4_000 },
      {
        seq: 4,
        kind: "media" as const,
        text: JSON.stringify({
          type: "link",
          url: "https://www3.nhk.or.jp/news/html/20261023/k1001.html",
          title: "日銀維持利率不變 關注春季物價走勢",
          site: "NHK",
          description: "日本銀行於今日金融政策決定會議決議維持現行政策利率。",
          thumbnail: "https://www3.nhk.or.jp/news/html/20261023/thumb.jpg",
          embed: null,
        }),
        at_ms: T0 + 4_500,
      },
      {
        seq: 5,
        kind: "media" as const,
        text: JSON.stringify({
          type: "video",
          url: "https://www.youtube.com/watch?v=hJdEb0N_0sc",
          title: "NHK ニュース解説：金融政策の行方",
          site: "YouTube · NHK",
          description: "記者解説動画",
          thumbnail: null,
          embed: "https://www.youtube.com/embed/hJdEb0N_0sc",
        }),
        at_ms: T0 + 5_000,
      },
      { seq: 6, kind: "event" as const, text: "已建立例行任務：每天 08:00 · 早安新聞", at_ms: T0 + 5_500 },
      { seq: 7, kind: "reply" as const, text: "以上是今天 NHK 的頭條新聞與解說影片。", at_ms: T0 + 6_000 },
      { seq: 8, kind: "reply" as const, text: "我也幫你設好每天早上 08:00 的早安新聞例行任務了，想考哪一段單字隨時跟我說！", at_ms: T0 + 6_500 },
    ];
    // While running: thinking block is open with a live shimmer
    const runningHtml = turnHTML(
      turn(20, { model, status: "running", text: "不該重複出現的文字", detail: "不該出現的詳細", messages }),
      emptyTimelineState(),
      false,
      { ...LOOK, plain: true },
    );
    expect(runningHtml.match(/class="work-section think-box/g)).toHaveLength(1);
    expect(runningHtml).toContain('data-act="thinking" aria-expanded="false"');
    expect(runningHtml).toContain("work-toggle");
    expect(runningHtml).toContain("主人想看今天的 NHK 新聞與相關影片");
    expect(runningHtml).toContain("已找到新聞頁面與 YouTube 報導影片");

    // When done: thinking block auto-collapses to 思考了 N 秒, and turn.text / turn.detail are not duplicated
    const doneHtml = turnHTML(
      turn(20, { model, status: "done", text: "不該重複出現的文字", detail: "不該出現的詳細", messages }),
      emptyTimelineState(),
      false,
      { ...LOOK, plain: true },
    );
    expect(doneHtml.match(/class="work-section think-box/g)).toHaveLength(1);
    expect(doneHtml).toContain('data-act="thinking" aria-expanded="false"');
    expect(doneHtml).toContain("work-toggle");
    expect(doneHtml).toContain('class="work-body think-body" id="work-20" hidden');
    expect(doneHtml).toContain('data-media-type="link"');
    expect(doneHtml).toContain("日銀維持利率不變 關注春季物價走勢");
    expect(doneHtml).toContain('data-media-type="video"');
    expect(doneHtml).toContain('src="https://www.youtube.com/embed/hJdEb0N_0sc"');
    expect(doneHtml).toContain('data-act="open-url" data-url="https://www3.nhk.or.jp/news/html/20261023/k1001.html"');
    expect(doneHtml).toContain("在網站開啟");
    expect(doneHtml).toContain("已建立例行任務：每天 08:00 · 早安新聞");
    expect(doneHtml).toContain("以上是今天 NHK 的頭條新聞與解說影片。");
    expect(doneHtml).toContain("我也幫你設好每天早上 08:00 的早安新聞例行任務了");
    expect(doneHtml).not.toContain("不該重複出現的文字");
    expect(doneHtml).not.toContain("不該出現的詳細");
  });

  test("media cards support image lightbox buttons, direct video players, and inline PDF viewers while rejecting unsafe URLs", () => {
    const html = turnHTML(
      turn(21, {
        status: "done",
        messages: [
          {
            seq: 1,
            kind: "media",
            text: JSON.stringify({
              type: "image",
              url: "https://example.com/chart.png",
              title: "匯率走勢圖",
              site: "NHK",
            }),
            at_ms: T0 + 1_000,
          },
          {
            seq: 2,
            kind: "media",
            text: JSON.stringify({
              type: "video",
              url: "https://example.com/clip.mp4",
              title: "現場片段",
              site: "Example",
            }),
            at_ms: T0 + 2_000,
          },
          {
            seq: 3,
            kind: "media",
            text: JSON.stringify({
              type: "pdf",
              url: "https://example.com/report.pdf",
              title: "金融政策報告.pdf",
              site: "BOJ",
            }),
            at_ms: T0 + 3_000,
          },
          {
            seq: 4,
            kind: "media",
            text: JSON.stringify({
              type: "link",
              url: "javascript:alert(1)",
              title: "Unsafe link",
            }),
            at_ms: T0 + 4_000,
          },
        ],
      }),
      emptyTimelineState(),
      false,
      { ...LOOK, plain: true },
    );
    expect(html).toContain('data-act="lightbox" data-src="https://example.com/chart.png"');
    expect(html).toContain('<video class="media-video" controls preload="metadata" src="https://example.com/clip.mp4"></video>');
    expect(html).toContain('<iframe class="media-pdf-frame" src="https://example.com/report.pdf"');
    expect(html).toContain('data-act="pdf-expand"');
    expect(html).not.toContain("javascript:alert");
  });

  test("a day heading", () => {
    expect(dayHTML("今天")).toBe('<li class="day"><span>今天</span></li>');
    expect(dayHTML("<x>")).toContain("&lt;x&gt;");
  });
});

describe("Raphael operating an app", () => {
  const computer = (tool: string | null, over: Partial<AgentStep> = {}): AgentStep =>
    step({ kind: "computer", tool, server: "Calculator", ...over });

  test("each action has its title, in each language", () => {
    const titles: Record<string, [string, string, string]> = {
      apps: ["電腦：查看視窗", "Computer: windows", "コンピューター：ウィンドウ一覧"],
      look: ["電腦：查看畫面", "Computer: look", "コンピューター：画面を確認"],
      open: ["電腦：開啟 App", "Computer: open app", "コンピューター：アプリを開く"],
      focus: ["電腦：切換視窗", "Computer: switch window", "コンピューター：ウィンドウ切替"],
      click: ["電腦：點擊", "Computer: click", "コンピューター：クリック"],
      type: ["電腦：輸入", "Computer: type", "コンピューター：入力"],
      keys: ["電腦：按鍵", "Computer: keys", "コンピューター：キー操作"],
      scroll: ["電腦：捲動", "Computer: scroll", "コンピューター：スクロール"],
      something_new: ["電腦：操作", "Computer: action", "コンピューター：操作"],
    };
    for (const [tool, [zh, en, ja]] of Object.entries(titles)) {
      setLocale("zh-TW");
      expect(stepTitle(computer(tool)), tool).toBe(zh);
      setLocale("en");
      expect(stepTitle(computer(tool)), tool).toBe(en);
      setLocale("ja");
      expect(stepTitle(computer(tool)), tool).toBe(ja);
    }
  });

  test("the tooltip is the app's name, or blank; the place is this PC; the picture is the monitor", () => {
    expect(stepToolTip(computer("click"))).toBe("Calculator");
    expect(stepToolTip(computer("click", { server: null }))).toBe("");
    expect(stepPlace(computer("click"))).toBe("這台電腦");
    const html = stepHTML(computer("click"), false);
    expect(html).toContain('aria-label="這台電腦"');
    expect(html).toContain('<span class="step-t" title="Calculator">電腦：點擊</span>');
    expect(html).toContain(ico("monitor"));
  });

  test("looking at the windows or the screen is bookkeeping when it went well, and shown when it failed", () => {
    const steps = [computer("apps"), computer("look"), computer("click"), computer("look", { outcome: "error", reason: "error" }), computer("apps", { outcome: "blocked", reason: "refused_app" })];
    const shown = shownSteps(turn(7, { steps }));
    expect(shown.map((s) => `${s.tool}:${s.outcome}`)).toEqual(["click:ok", "look:error", "apps:blocked"]);
    // find_tools stays bookkeeping, and a look of anything else is no bookkeeping
    expect(shownSteps(turn(8, { steps: [step({ tool: "find_tools" }), step({ kind: "tool", tool: "look", server: "X" })] }))).toHaveLength(1);
  });

  test("the reasons of a refusal have words in each language", () => {
    const why = (reason: string): string => reasonText(computer("click", { outcome: "error", reason }));
    expect(["password", "handoff", "refused_app", "interrupted", "denied"].map(why)).toEqual(["密碼欄位", "需主人操作", "不允許的 App", "主人中斷", "未允許"]);
    setLocale("en");
    expect(["password", "handoff", "refused_app", "interrupted", "denied"].map(why)).toEqual(["Password field", "Needs you to take over", "App not allowed", "Interrupted by you", "Not allowed"]);
    setLocale("ja");
    expect(["password", "handoff", "refused_app", "interrupted", "denied"].every((reason) => why(reason) !== "")).toBe(true);
    // a service's refused login keeps its own words
    setLocale("zh-TW");
    expect(reasonText(step({ outcome: "error", reason: "denied" }))).toBe("登入被拒");
  });
});
