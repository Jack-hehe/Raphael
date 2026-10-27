import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";
import { installMock } from "./mock-ipc.js";
import { checkRendered } from "./checks.mjs";

const fixture = JSON.parse(readFileSync(new URL("./fixture.json", import.meta.url), "utf8"));
describe("shared look IPC boundary", () => {
  it("reconciles the month bill exactly with sidebar ledger spend, including fractional costs", async () => {
    const target = {};
    installMock(fixture, { fixture: { usageFishVoice: true } }, target);
    const call = (method, params = {}) => target.__TAURI_INTERNALS__.invoke("core_call", { method, params });
    const month = await call("usage.runs", { range: "month" });
    expect(month.bill.cost_usd).toBe((await call("status.get")).spend.month_usd);
    expect(month.bill.purposes.map(row => row.purpose)).toEqual(["agent", "stt", "cleanup", "memory", "other"]);
    expect(month.bill.purposes.find(row => row.purpose === "stt").share).toBeCloseTo(.4);
    expect(month.fish_voice).toBe(true);
    expect((await call("usage.runs", { range: "last_month" })).bill.cost_usd).toBe(.25);
    expect((await call("usage.runs", { range: "all" })).bill.cost_usd).toBeCloseTo(1.49);
  });
  it("keeps a finite empty bill and omits disabled Fish Audio voice", async () => {
    const target = {};
    installMock(fixture, { fixture: { usageEmpty: true } }, target);
    const report = await target.__TAURI_INTERNALS__.invoke("core_call", { method:"usage.runs", params:{range:"month"} });
    expect(report.bill.cost_usd).toBe(0);
    expect(report.bill.purposes.every(row => row.cost_usd === 0 && row.share === 0)).toBe(true);
    expect(report.fish_voice).toBe(false);
  });
  it.each([undefined, -1])("rejects an approval without an explicit existing source turn (%s)", turn_id => {
    const data = structuredClone(fixture);
    data.adminQuestions.draft.turn_id = turn_id;
    expect(() => installMock(data, { fixture: { adminApproval: "draft" } }, {})).toThrow("Approval fixture requires its explicit source turn");
  });
  it("puts running state in the visible formal conversation and preserves quick questions", async () => {
    const target = {};
    installMock(fixture, { fixture: { running: true } }, target);
    const history = conversation_id => target.__TAURI_INTERNALS__.invoke('core_call', { method: 'agent.history.list', params: { member_id: 0, conversation_id } });
    const turn = (await history(1)).turns[0];
    expect(turn.status).toBe('running');
    expect(turn.messages.some(message => message.kind === 'reply')).toBe(false);
    expect((await history(4)).turns[0].status).toBe('done');
  });
  it("stops only the named quick request and delivers an announced quick answer, leaving formal history alone", async () => {
    const target = {};
    installMock(fixture, { fixture: { quickRunning: true, arrivals: [{ ...fixture.turns[0], id: 91, session_id: "look-quick-later" }] } }, target);
    const invoke = target.__TAURI_INTERNALS__.invoke;
    const history = conversation_id => invoke("core_call", { method: "agent.history.list", params: { member_id: 0, conversation_id } });
    const formal = await history(1);
    expect((await history(4)).turns.map(turn => [turn.id, turn.status])).toEqual([[90, "running"]]);
    expect(await invoke("core_call", { method: "agent.history.stop", params: { session_id: "look-quick", member_id: 0 } })).toEqual({ stopped: true });
    const [stopped] = (await history(4)).turns;
    expect([stopped.status, stopped.text, stopped.messages.some(message => message.kind === "reply")]).toEqual(["denied", "已取消。", false]);
    target.__look.emit({ type: "AgentTurn", turn_id: 91, member_id: 0 });
    expect((await history(4)).turns.map(turn => turn.id)).toEqual([91, 90]);
    expect(await history(1)).toEqual(formal);
  });
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
  it("settles an approval in its source conversation after quick history is prepended", async () => {
    const target = {};
    installMock(fixture, { fixture: { adminApproval: "draft" } }, target);
    const invoke = target.__TAURI_INTERNALS__.invoke;
    const question = await invoke("agent_question_open");
    const quickBefore = structuredClone(target.__look.data.turns.find(turn => turn.conversation_id === 4));
    await invoke("agent_question_answer", { id: question.id, approved: true, drafts: question.drafts });
    const formal = (await invoke("core_call", { method: "agent.history.list", params: { member_id: 0, conversation_id: 1 } })).turns;
    expect(formal.some(turn => turn.status === "done" && turn.text.startsWith("已建立"))).toBe(true);
    expect(target.__look.data.turns.find(turn => turn.conversation_id === 4)).toEqual(quickBefore);
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

describe("the apps and the private skills at the IPC boundary", () => {
  const ask = (target, method, params = {}) => target.__TAURI_INTERNALS__.invoke("core_call", { method, params });
  const toolkit = (slug, over = {}) => ({ id: `composio:${slug}`, kind: "composio", name: slug, status: "ACTIVE", enabled: true, enabled_tools: [`${slug}_one`, `${slug}_two`], disabled_tools: [], accounts: [{ id: `ca_${slug}`, status: "ACTIVE" }], ...over });
  const google = { id: "google", kind: "google", status: "ACTIVE", enabled: true, enabled_tools: ["gmail_list_messages", "calendar_list_events"], disabled_tools: [] };

  it("counts the apps whose tools can be called now and lists the others with their state, as the core's inventory does", async () => {
    const target = {};
    installMock(fixture, { fixture: { servers: [], managedConnectors: [
      toolkit("notion"), toolkit("slack", { status: "INITIALIZING" }), toolkit("linear", { status: "FAILED" }), toolkit("trello", { enabled: false }),
      toolkit("jira", { disabled_tools: ["jira_one", "jira_two"] }), google, toolkit("gmail"), toolkit("googlecalendar"),
    ] } }, target);
    const inventory = await ask(target, "integrations.inventory");
    expect(Object.fromEntries(inventory.apps.map(app => [app.id, app.state]))).toEqual({
      "composio:notion": "ready", "composio:slack": "needs_sign_in", "composio:linear": "error", "composio:trello": "disabled", "composio:jira": "no_tools",
      google: "ready", "composio:gmail": "duplicate", "composio:googlecalendar": "duplicate" });
    expect([inventory.state, inventory.count]).toEqual(["complete", 2]);
    expect(inventory.apps.filter(app => app.connected).map(app => app.id)).toEqual(["composio:notion", "google"]);
  });

  it("gives the roster of a model the same inventory and the same count as the sidebar and the page", async () => {
    const target = {};
    installMock(fixture, {}, target);
    const inventory = await ask(target, "integrations.inventory");
    const { team_context } = await ask(target, "members.list");
    expect([team_context.inventory_state, team_context.connected, team_context.apps]).toEqual(["complete", inventory.count, inventory.apps]);
    // and it moves as the core moves it: an uninstall, a tool switched off, an app switched off
    await ask(target, "connectors.disconnect", { id: "composio:notion" });
    expect((await ask(target, "integrations.inventory")).count).toBe(inventory.count - 1);
    await ask(target, "connectors.set_tools", { id: "google", enabled: false });
    const after = await ask(target, "integrations.inventory");
    expect(after.count).toBe(inventory.count - 2);
    expect((await ask(target, "members.list")).team_context.connected).toBe(after.count);
  });

  it("is an inventory that cannot be read when the scene says so", async () => {
    const target = {};
    installMock(fixture, { fixture: { inventoryUnreadable: true } }, target);
    expect(await ask(target, "integrations.inventory")).toEqual({ state: "unknown", count: null, apps: [] });
    const { team_context } = await ask(target, "members.list");
    expect([team_context.inventory_state, team_context.connected]).toEqual(["unknown", null]);
  });

  it("reads, edits, switches and deletes a skill of a member as the core does, and refuses a document it cannot keep", async () => {
    const target = {};
    const skill = (id, name, over = {}) => ({ id, member_id: 1, name, description: `Use for ${name}`, enabled: true, source: "owner/repo", ...over });
    const state = installMock(fixture, { fixture: { skills: [skill(7, "summary"), skill(8, "notes", { text: "---\nname: notes\ndescription: Use for notes\n---\nBody" })] } }, target);
    const list = async () => (await ask(target, "members.skills.list", { member_id: 1 })).skills;
    expect((await list()).map(item => Object.keys(item).includes("text"))).toEqual([false, false]); // the list holds no document
    const read = await ask(target, "members.skills.read", { member_id: 1, id: 7 });
    expect(read.text).toBe("---\nname: summary\ndescription: Use for summary\n---\n");
    expect(read.skill).toMatchObject({ id: 7, name: "summary" });
    const edited = "---\nname: summaries\ndescription: Use for a summary of a thread\n---\nStart with the verdict.";
    const answered = await ask(target, "members.skills.update", { member_id: 1, id: 7, text: edited });
    expect(answered.skill).toMatchObject({ name: "summaries", description: "Use for a summary of a thread", enabled: true });
    expect((await ask(target, "members.skills.read", { member_id: 1, id: 7 })).text).toBe(edited);
    for (const [text, message] of [["no frontmatter", "name must not be empty"], ["---\nname: x\n---\n", "description must not be empty"],
      ["---\nname: Notes\ndescription: Use\n---\n", "a skill with that name already exists"]])
      await expect(ask(target, "members.skills.update", { member_id: 1, id: 7, text })).rejects.toThrow(message);
    expect((await ask(target, "members.skills.read", { member_id: 1, id: 7 })).text).toBe(edited); // refused: nothing changed
    expect((await ask(target, "members.skills.update", { member_id: 1, id: 8, enabled: false })).skill.enabled).toBe(false);
    await expect(ask(target, "members.skills.read", { member_id: 2, id: 7 })).rejects.toThrow("no such member skill");
    expect(await ask(target, "members.skills.delete", { member_id: 1, id: 7 })).toEqual({ deleted: true });
    expect((await list()).map(item => item.id)).toEqual([8]);
    expect(state.errors).toEqual([]);
  });
});
