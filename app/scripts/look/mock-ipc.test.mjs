import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
import { installMock } from "./mock-ipc.js";
import { checkRendered } from "./checks.mjs";

const fixture = JSON.parse(readFileSync(new URL("./fixture.json", import.meta.url), "utf8"));
describe("shared look IPC boundary", () => {
  it.each([true, false])("settles draft approval in the formal conversation while preserving quick history (%s)", async approved => {
    const target = {};
    installMock(fixture, { fixture: { adminApproval: "draft" } }, target);
    const invoke = target.__TAURI_INTERNALS__.invoke;
    const history = conversation_id => invoke("core_call", { method: "agent.history.list", params: { member_id: 0, conversation_id } });
    const quick = await history(4);
    expect((await history(1)).turns[0].status).toBe("confirm");
    const question = await invoke("agent_question_open");
    await invoke("agent_question_answer", { id: question.id, approved, drafts: question.drafts });
    const turn = (await history(1)).turns[0];
    expect(turn.status).toBe(approved ? "done" : "denied");
    expect(turn.text).toBe(approved ? "已建立 COO、PM、RD" : "已取消建立成員。");
    if (approved) expect(turn.detail.split("\n")).toHaveLength(3);
    expect(await history(4)).toEqual(quick);
  });
  it("initializes rendered owner-view helpers before checking a reply and rejects progress in final messages", () => {
    const answer = { textContent: "需要重新登入並授予讀取權限。", getBoundingClientRect: () => ({ width: 400, height: 40 }) };
    vi.stubGlobal("document", { querySelectorAll: () => [answer] });
    vi.stubGlobal("window", { __look: { calls: [{ method: "agent.history.list", params: { member_id: 0 } }] } });
    try {
      expect(checkRendered("live-scenario-owner-view").answers).toBe(1);
      window.__look.calls[0].params = { conversation_id: 1 };
      expect(checkRendered("live-scenario-owner-view").scope).toBe("personal");
      answer.textContent += "正在查看";
      expect(() => checkRendered("live-scenario-owner-view")).toThrow("progress stays outside");
    } finally { vi.unstubAllGlobals(); }
  });
  it("replays the Guardian owner-view event/history contract without changing personal scope", async () => {
    const target = {};
    installMock(fixture, { fixture: { liveScenarioOwnerView: true } }, target);
    const result = await target.__TAURI_INTERNALS__.invoke("core_call", { method: "agent.history.list", params: { member_id: 0, conversation_id: 1 } });
    expect(result.turns).toHaveLength(1);
    expect(result.turns[0]).toMatchObject({ source: "app", member_id: 0, conversation_id: 1 });
    expect(result.turns[0].messages.map(message => message.kind)).toEqual(["progress", "reply"]);
    expect(result.turns[0].messages[1].text).not.toContain("正在");
  });
  it("keeps reports out of today's work in both default and expanded home fixtures", async () => {
    for (const homeManyQuestions of [false, true]) {
      const target = {};
      installMock(fixture, { fixture: { homeManyQuestions } }, target);
      const home = await target.__TAURI_INTERNALS__.invoke("core_call", { method: "home.overview" });
      expect(home.today.every(item => ["question", "routine"].includes(item.kind))).toBe(true);
      expect(home.today.some(item => home.reports.some(report => report.id === item.id))).toBe(false);
      expect(home.counts.today).toBe(home.today.length);
      expect(home.today.length).toBe(homeManyQuestions ? 5 : 3);
    }
  });
  it("keeps empty home spend and the sidebar status consistent and exposes connection problems", async () => {
    const target = {};
    installMock(fixture, { fixture: { homeEmpty: true, problem: true } }, target);
    const call = method => target.__TAURI_INTERNALS__.invoke("core_call", { method });
    const home = await call("home.overview"), status = await call("status.get");
    expect(home.usage.month_usd).toBe(status.spend.month_usd);
    expect(home.report.openrouter.state).toBe("no_key");
    expect(home.today).toEqual([]);
    expect(JSON.stringify(fixture.home)).not.toMatch(/\?{3}/);
  });
  it("creates the approved member in the promised group with an empty conversation", async () => {
    const target = {};
    installMock(fixture, { fixture: { adminApproval: "create" } }, target);
    const invoke = target.__TAURI_INTERNALS__.invoke;
    const question = await invoke("agent_question_open");
    await invoke("agent_question_answer", { id: question.id, approved: true });
    const roster = await invoke("core_call", { method: "members.list" });
    const created = roster.members.find(member => member.id === 8);
    expect(roster.groups.find(group => group.id === created.group_id)?.name).toBe("個人公司");
    expect(created.preview).toBe("");
    expect(created.last_at).toBeNull();
  });
  it("isolates conversations by member and turns by conversation", async () => {
    const target = {};
    installMock(fixture, {}, target);
    const call = (method, params) => target.__TAURI_INTERNALS__.invoke("core_call", { method, params });
    expect((await call("conversations.list", { member_id: 1 })).conversations.map(c => c.id)).toEqual([11]);
    expect((await call("agent.history.list", { member_id: 0, conversation_id: 2 })).turns).toEqual([]);
    expect((await call("agent.history.list", { member_id: 1, conversation_id: 11 })).turns[0].member_id).toBe(1);
  });
  it("registers native event channels and emits settings changes", async () => {
    const target = {};
    installMock(fixture, {}, target);
    const events = [];
    await target.__TAURI_INTERNALS__.invoke("main_events", { channel: { onmessage(event) { events.push(event); } } });
    await target.__TAURI_INTERNALS__.invoke("device_settings_set", { patch: { appearance: "dark" } });
    await new Promise(resolve => setTimeout(resolve, 10));
    expect(events[0].settings.appearance).toBe("dark");
  });
  it("fails unsupported IPC even if the app catches the rejection", async () => {
    const target = {};
    const state = installMock(fixture, {}, target);
    await expect(target.__TAURI_INTERNALS__.invoke("core_call", { method: "new.feature.list" })).rejects.toThrow("Unmocked IPC: new.feature.list");
    expect(state.errors).toEqual(["Unmocked IPC: new.feature.list"]);
  });
  it("models idle Escape cancellation while rejecting unsupported native hotkeys", async () => {
    const target = {};
    const state = installMock(fixture, {}, target);
    await expect(target.__TAURI_INTERNALS__.invoke("window_hotkey", { kind: "cancel" })).resolves.toBeUndefined();
    expect(state.calls).toContainEqual({ command: "window_hotkey", kind: "cancel" });
    expect(state.errors).toEqual([]);
    await expect(target.__TAURI_INTERNALS__.invoke("window_hotkey", { kind: "dictation_down" })).rejects.toThrow("Unmocked IPC: window_hotkey");
    await expect(target.__TAURI_INTERNALS__.invoke("window_hotkey", { kind: "unknown" })).rejects.toThrow("Unmocked IPC: window_hotkey");
    expect(state.errors).toEqual(["Unmocked IPC: window_hotkey", "Unmocked IPC: window_hotkey"]);
  });
  it("starts each scene from isolated fixture data", async () => {
    const first = {}, second = {};
    installMock(fixture, { fixture: { turns: [], problem: true }, theme: "dark", locale: "ja" }, first);
    installMock(fixture, {}, second);
    const invoke = (target, method) => target.__TAURI_INTERNALS__.invoke("core_call", { method });
    expect((await invoke(first, "agent.history.list")).turns).toEqual([]);
    expect((await invoke(first, "status.get")).openrouter.state).toBe("no_key");
    expect((await invoke(second, "agent.history.list")).turns).toHaveLength(2);
    expect((await invoke(second, "status.get")).openrouter.state).toBe("ok");
    expect((await first.__TAURI_INTERNALS__.invoke("device_settings_get")).ui_language).toBe("ja");
  });
});
