// Only the native boundary is replaced; the real index.html, modules and CSS still run.
export function installMock(fixture, config = {}, target = window) {
  const data = structuredClone(fixture);
  const variant = config.fixture ?? {};
  for (const [key, value] of Object.entries(variant)) if (key in data) data[key] = structuredClone(value);
  Object.assign(data.device, { appearance: config.theme ?? "light", ui_language: config.locale ?? "zh-TW", onboarded: !variant.onboarding });
  data.settings.skin = config.skin ?? "default";
  if (variant.liveScenarioOwnerView) data.turns = structuredClone(data.liveScenarioOwnerTurns);
  if (variant.onlyRaphael) data.members = data.members.filter(member => member.id === 0);
  if (variant.waitingMember !== undefined) data.members.find(member => member.id === variant.waitingMember).waiting = true;
  if (variant.problem) data.status.openrouter.state = "no_key";
  let adminQuestion = variant.adminApproval ? data.adminQuestions[variant.adminApproval] : null;
  let adminTurn = adminQuestion ? data.turns.find(turn => turn.id === adminQuestion.turn_id) : null;
  if (variant.firstBot) {
    data.members = [{ ...data.members[0], profile_ready: false }];
    data.groups = [];
    data.turns = [];
    adminQuestion = adminTurn = null;
  }
  if (adminQuestion && !adminTurn) throw new Error("Approval fixture requires its explicit source turn");
  if (adminQuestion) {
    adminTurn.status = "confirm";
    adminTurn.messages = adminTurn.messages.filter(message => message.kind !== "reply");
    adminTurn.text = "";
  }
  if (variant.running || variant.quickRunning) {
    const conversation = data.conversations.find(item => item.member_id === 0 && (variant.quickRunning ? item.kind === "quick" : item.is_default));
    const runningTurn = data.turns.find(turn => turn.member_id === 0 && turn.conversation_id === conversation?.id);
    runningTurn.status = "running";
    runningTurn.messages = runningTurn.messages.filter(message => message.kind !== "reply");
    runningTurn.text = "";
  }
  // Turns the core writes later and then announces (agent.turn), such as an Alt+Space answer.
  const arrivals = new Map((variant.arrivals ?? []).map(turn => [turn.id, structuredClone(turn)]));
  if (variant.p04Blocked) {
    data.roomHandoffs=[{...data.roomHandoff,state:"failed",reason:"missing_artifact",result:"",evidence:[]}];
    data.messages.at(-1).text="尚未取得檔案，目前沒有可驗收結果。";
    data.messages.push({...data.roomWrapupMessage,reply_to:null,reply_to_member:null,text:"### 結論\n目前沒有可驗收的成果。\n\n### 待決定\n小晴的檔案尚未完成，請決定是否重試。\n\n### 下一步\n- 小晴：補齊檔案後重新驗收。"});
  }
  if (variant.p04Short) {
    data.messages=[data.messages[0],{...data.messages[1],text:"目前沒有交付成果，需要先取得來源權限。"},
      {...data.roomWrapupMessage,reply_to:null,reply_to_member:null,text:"### 結論\n目前沒有可驗收的成果。\n\n### 待決定\n請提供來源權限。\n\n### 下一步\n- 小晴：取得權限後處理。"}];
  }
  if (variant.roomThinking) { data.messages.push(...data.roomThinkingMessages); data.roomExchanges.push(data.roomActiveExchange); }
  if (variant.p03Handoff) {
    const task = { ...data.roomHandoff, state: variant.p03Handoff };
    data.messages = [data.messages[0], { ...data.messages[1], member_id: 0, text: "請小晴立即整理驗收報告。" }];
    if (variant.p03SourceDates) data.messages[0].text = "來源寫明早截止、明天中午提交。現在就開始整理驗收報告。";
    if (task.state !== "done") { task.result = ""; task.evidence = []; }
    if (task.state === "failed") task.reason = "work_without_action";
    data.roomHandoffs = [task];
    if (task.state === "running") data.messages.push({ ...data.roomThinkingMessages[0], turn_id: task.work_turn_id });
    if (task.state === "done") data.messages.push({ ...data.messages[1], id: 4, member_id: 1, turn_id: task.work_turn_id, text: task.result, created: task.created_at_ms + 1000 });
    if (variant.p03Private) {
      const source = data.turns.find(turn => turn.member_id === 0 && turn.conversation_id === 1);
      data.turns = [{ ...source, question: "請小晴立即整理驗收報告。", text: "", detail: null, messages: [], handoffs: [task] }];
    }
  }
  if (variant.p02Meeting) data.turns.find(turn => turn.member_id === 0 && turn.conversation_id === 1).messages.push({ seq: 99, kind: "event", text: JSON.stringify(data.p02MeetingEvent), at_ms: data.now });
  if (variant.splitReply) {
    // The core writes a long reply as messages of one 《答。…》 line (agent/service.py `_message`).
    const turn = data.turns.find(turn => turn.id === 1), reply = turn.messages.find(message => message.kind === "reply");
    const [first, last] = reply.text.split("\n\n我已");
    turn.messages = [...turn.messages.filter(message => message !== reply), { ...reply, text: first }, { ...reply, seq: reply.seq + 1, text: `我已${last}`, at_ms: reply.at_ms + 500 }];
  }
  if (variant.systemEvents) {
    const turn = data.turns.find(turn => turn.id === 1);
    turn.handoffs = [{ ...data.roomHandoff, state: "done" }, { ...data.roomHandoff, task_id: 43, member_id: 2, member_name: "小森", mode: "consult", state: "running", result: "", evidence: [] }];
    turn.messages.push({ seq: 99, kind: "event", text: JSON.stringify(data.p02MeetingEvent), at_ms: data.now });
  }
  // A task that waits for an app shows its connector card; `cardSignIn` "approve" finishes the sign-in the
  // way the core reports it (authorizing, then connected) and adds the turn that resumes the task.
  if (variant.connectorCard) data.turns.push(structuredClone(data.connectorTurns[variant.connectorCard]));
  // A task that waits for a key shows its secret card, and one paused for the owner's hands its computer card. The
  // fixture reports their states as the core does and keeps only the states: the key typed into a card is
  // received by `integrations.card_save` and forgotten (`resumeFails`: the task does not continue; `signedIn`:
  // the page no longer asks for the sign-in when the owner presses 我完成了).
  if (variant.secretCard) data.turns.push(structuredClone(data.secretTurns[variant.secretCard]));
  if (variant.computerCard) data.turns.push(structuredClone(data.computerTurns[variant.computerCard]));
  // A step that needs the owner's yes shows its permission card, and the question it follows is the one the window holds
  // (`agent_question_open`), answered with `agent_question_answer`; the card follows the answer as the core reports it.
  // `memberDraftFirst` sets whether 小晴 writes mail as a draft first.
  let permission = null;
  if (variant.permissionCard) {
    const made = structuredClone(data.permissionTurns[variant.permissionCard]);
    data.turns.push(made.turn);
    permission = { ...made, card: made.turn.permissions[0] };
  }
  if (variant.memberDraftFirst !== undefined) data.members.find(member => member.id === 1).draft_first = variant.memberDraftFirst;
  // A member's email draft is a card in its chat. The fixture keeps the states and the digests only: what the owner types
  // is received by `integrations.draft_save` as a new version, and nothing is ever sent (`sent` lists what 傳送 would send).
  // `mailOffer` has the member ask whether 小森 may send it.
  if (variant.mailDraft) {
    const mail = structuredClone(data.mailTurns[variant.mailDraft]);
    if (variant.mailOffer) mail.drafts[0].offer = { member_id: 2, name: data.members.find(member => member.id === 2).name };
    data.turns.push(mail);
    if (!data.conversations.some(item => item.id === data.colleagueConversation.id)) data.conversations.push(structuredClone(data.colleagueConversation));
  }
  if (variant.colleagues) {
    // 小森 asked 小晴 from its own chat; 小晴's main chat shows each question where it came.
    data.conversations.push(data.colleagueConversation);
    data.turns.push(data.colleagueTurn, ...data.colleagueExchanges.slice(0, variant.colleagues));
  }
  if (variant.p02Roles) data.rooms[0].roles = data.p02Roles;
  if (variant.p02Blocked || variant.p02Fallback || variant.p02LongExchange) {
    const exchange = { ...data.roomActiveExchange, state: "stopped", active_turn: null };
    if (variant.p02Blocked) {
      // 小森's first reply hit the CLI rate limit; the owner then stopped a second request.
      data.messages = [data.messages[0], data.messages[1], data.p02BlockedMessage];
      exchange.origin_message_id = data.p02BlockedMessage.id;
      exchange.reply_intents = data.p02BlockedIntents;
    }
    if (variant.p02Fallback) exchange.routing = { fallback: "timeout", model: "fixture/router" };
    if (variant.p02LongExchange) { exchange.turns_taken = 8; exchange.hops = 5; exchange.state = "done"; exchange.reply_intents = [{ id: 100, recipient_id: 1, recipient_name: data.members[1].name, state: "done", reason: "" }]; }
    data.roomExchanges.push(exchange);
  }
  if (variant.roomDiscussion || variant.roomWrapup) data.messages.push(...data.roomDiscussionMessages);
  if (variant.roomWrapup) data.messages.push(data.roomWrapupMessage);
  if (variant.roomMissingSource) data.messages[2].reply_reference = { message_id: 999, unavailable: true };
  if (variant.usageFishVoice) { data.settings.tts_enabled = true; data.settings.tts_service = "fish"; }
  if (variant.usageEmpty) {
    data.spendRows = [];
    data.status.spend.month_usd = 0;
    data.usage.total = 0;
    data.usage.summary = Object.fromEntries(Object.keys(data.usage.summary).map(key => [key, 0]));
    data.usage.groups = Object.fromEntries(Object.keys(data.usage.groups).map(key => [key, []]));
  }
  if (variant.homeManyQuestions) {
    data.home.today.unshift(...[3, 4].map(id => ({ ...data.home.today[0], id: `question:${id}`, turn_id: id,
      title: id === 3 ? "確認資料來源範圍" : "核准後續研究安排" })));
    data.home.counts.today = data.home.today.length;
  }
  if (variant.homeEmpty) {
    data.home.today = []; data.home.reports = []; data.home.calendar = [];
    data.home.counts = { today: 0, reports: 0, calendar: 0 };
    data.home.usage = { month_usd: 0, limit_usd: 20, daily: Array(14).fill(0), allowances: { brains: [] } };
    data.status.spend = { ...data.status.spend, month_usd: 0, today_usd: 0, daily: Array(14).fill(0) };
    data.home.dictation = { words: 0, audio_seconds: 0, time_saved_seconds: 0, speed_wpm: null, daily: Array(14).fill(0) };
  }
  function usageBill(period) {
    // The shared fixture clock and browser run in Asia/Taipei.
    const local = new Date(data.now + 8 * 3600000);
    const midnight = (year, month, day) => Date.UTC(year, month, day) - 8 * 3600000;
    const year = local.getUTCFullYear(), month = local.getUTCMonth(), day = local.getUTCDate();
    let lower = -Infinity, upper = Infinity;
    if (period === "month") { lower = midnight(year, month, 1); upper = midnight(year, month + 1, 1); }
    if (period === "last_month") { lower = midnight(year, month - 1, 1); upper = midnight(year, month, 1); }
    if (period === "week") {
      const monday = day - (local.getUTCDay() + 6) % 7;
      lower = midnight(year, month, monday); upper = midnight(year, month, monday + 7);
    }
    const rows = data.spendRows.filter(row => row.created_at_ms >= lower && row.created_at_ms < upper);
    const total = rows.reduce((sum, row) => sum + row.cost_usd, 0);
    const amounts = { agent: 0, stt: 0, cleanup: 0, memory: 0 };
    for (const row of rows) {
      const purpose = ["agent", "stt", "cleanup", "memory"].includes(row.purpose) ? row.purpose : "other";
      amounts[purpose] = (amounts[purpose] ?? 0) + row.cost_usd;
    }
    return { cost_usd: total, purposes: Object.entries(amounts).map(([purpose, cost_usd]) => ({ purpose, cost_usd, share: total ? cost_usd / total : 0 })) };
  }
  // Sidebar and overview use the same ledger rows, including floating-point totals.
  if (!variant.homeEmpty) data.status.spend.month_usd = usageBill("month").cost_usd;
  const state = { data, calls: [], errors: [], urls: [], sent: [], exported: null, channel: null, emit(event) {
    if (event.type === "AgentTurn" && arrivals.has(event.turn_id)) { data.turns.push(arrivals.get(event.turn_id)); arrivals.delete(event.turn_id); }
    state.channel?.onmessage(event);
  } };
  let maximized = false;
  target.__look = state;
  const copy = value => value === undefined ? undefined : structuredClone(value);
  const nextId = items => Math.max(0, ...items.map(item => item.id)) + 1;
  const unsupported = name => { const message = `Unmocked IPC: ${name}`; state.errors.push(message); throw new Error(message); };
  const notify = (type, extra = {}) => { setTimeout(() => state.emit({ type, ...extra }), 0); };
  const byMember = (items, p) => items.filter(item => item.member_id === (p.member_id ?? 0));
  const update = (items, id, patch) => {
    const item = items.find(item => item.id === id);
    if (!item) throw new Error(`Fixture item not found: ${id}`);
    Object.assign(item, patch); return item;
  };
  // The core's inventory of the owner's apps (integrations/inventory.py) as a double, computed from the fixture's connections so that
  // every scene that switches, signs in or uninstalls moves the count as the core moves it: an app is connected when its tools can be
  // called now; one that waits for a sign-in, failed, is off, has every tool off, or is a second way to an app that is counted already
  // is listed and never counted. `inventoryUnreadable` is a core that cannot say.
  const inventory = () => {
    if (variant.inventoryUnreadable) return { state: "unknown", count: null, apps: [] };
    const entries = [
      ...data.servers.map(server => {
        const tools = server.enabled ? server.tools.filter(tool => tool.enabled).length : 0;
        const state = !server.enabled ? "disabled" : server.error === "auth" ? "needs_sign_in" : server.error ? "error" : server.status === "unchecked" ? "unchecked" : tools ? "ready" : "no_tools";
        return { id: server.id, name: server.name, enabled: server.enabled, state, tools, services: server.catalog_id ? [server.catalog_id] : [], rank: 2 };
      }),
      ...data.managedConnectors.map(connector => {
        const off = new Set(connector.disabled_tools ?? []);
        const ready = connector.enabled && connector.status === "ACTIVE" ? connector.enabled_tools.filter(tool => !off.has(tool)) : [];
        const waiting = ["INITIALIZING", "INITIATED", "EXPIRED", "REVOKED", "INACTIVE"].includes(connector.status);
        const state = !connector.enabled ? "disabled" : waiting ? "needs_sign_in" : connector.status === "FAILED" ? "error" : ready.length ? "ready" : "no_tools";
        const google = connector.kind === "google";
        const services = google ? [["gmail", "gmail_"], ["googlecalendar", "calendar_"]].filter(([, prefix]) => ready.some(tool => tool.startsWith(prefix))).map(([id]) => id) : [connector.id.replace("composio:", "")];
        return { id: connector.id, name: connector.name || (google ? "Google" : connector.id), enabled: connector.enabled, state, tools: ready.length, services, rank: google ? 0 : 1 };
      }),
    ];
    const claimed = new Set();
    for (const entry of [...entries].sort((a, b) => a.rank - b.rank)) {
      if (entry.state !== "ready") continue;
      if (entry.services.length && entry.services.every(service => claimed.has(service))) entry.state = "duplicate";
      else entry.services.forEach(service => claimed.add(service));
    }
    const apps = entries.map(({ id, name, enabled, state, tools }) => ({ id, name, enabled, state, tools, connected: state === "ready" }));
    return { state: "complete", count: apps.filter(app => app.connected).length, apps };
  };
  // A skill's document: its frontmatter says its name and when to use it, as the core reads them (members/capabilities.py `skill_metadata`).
  const skillText = skill => skill.text ?? `---\nname: ${skill.name}\ndescription: ${skill.description}\n---\n`;
  const skillRow = ({ text, ...skill }) => skill;
  const skillOf = p => {
    const skill = data.skills.find(item => item.id === p.id && item.member_id === p.member_id);
    if (!skill) throw new Error("Invalid params: no such member skill");
    return skill;
  };
  const frontmatter = (text, key) => {
    const head = text.startsWith("---") ? text.split("---")[1] ?? "" : "";
    return head.split("\n").map(line => line.trim()).find(line => line.startsWith(`${key}:`))?.slice(key.length + 1).trim() ?? "";
  };
  async function core(method, p = {}) {
    switch (method) {
      case "settings.get": return { settings: data.settings };
      case "settings.set": data.settings[p.key] = p.value; notify("SettingsChanged"); return { settings: data.settings };
      case "status.get": return data.status;
      case "home.overview": return { ...data.home, report: data.status };
      case "status.check_openrouter": data.status.openrouter.state = "ok"; return data.status.openrouter;
      case "secrets.status": return { has_key: !variant.problem };
      case "stats.summary": return data.stats;
      case "history.list": return { items: data.history.filter(item => (!p.query || item.text.includes(p.query)) && (!p.after_ms || item.created_at_ms >= p.after_ms) && (!p.before_ms || item.created_at_ms < p.before_ms)) };
      case "history.delete": data.history = data.history.filter(item => item.id !== p.id); return { deleted: true };
      case "dictionary.list": return { entries: data.dictionary };
      case "dictionary.add": { const entry = { ...p, id: nextId(data.dictionary), misheard: [], source: "manual", created_at_ms: data.now }; data.dictionary.push(entry); return { entry }; }
      case "dictionary.learn": {
        const learned = [];
        for (const { word } of p.corrections) {
          if (data.dictionary.some(entry => entry.word === word)) continue;
          data.dictionary.push({ id: nextId(data.dictionary), word, misheard: [], source: "learned", created_at_ms: data.now });
          learned.push(word);
        }
        return { learned };
      }
      case "dictionary.delete": data.dictionary = data.dictionary.filter(item => item.id !== p.id); return { deleted: true };
      case "members.list": return { members: data.members, groups: data.groups, team_context: {
        schema_version: 1, revision: "fixture", self_id: 0, count: data.members.length, primary_id: 0, chief_id: 0,
        inventory_state: "complete", room_member_ids: null,
        members: data.members.map(m => ({ id: m.id, name: m.name, role: m.role, blurb: m.blurb ?? "" })),
        // the roster of every model carries the same inventory and the same count as the sidebar and the connections page
        apps: inventory().apps, connected: inventory().count,
      } };
      case "members.presets": return { presets: data.presets };
      case "members.viewing": case "members.read": return {};
      case "members.tool_choices": return { tools: [], connections: data.servers };
      case "members.update": { const member = update(data.members, p.id, p); notify("MembersChanged", { member_id: p.id }); return { member }; }
      case "members.create": { const member = { ...data.members[1], ...p, id: nextId(data.members) }; data.members.push(member); notify("MembersChanged", { member_id: member.id }); return { member, routine_offer: null }; }
      case "members.delete": data.members = data.members.filter(item => item.id !== p.id); notify("MembersChanged", { member_id: p.id }); return { deleted: true };
      case "members.skills.list": return { skills: byMember(data.skills, p).map(skillRow) };
      case "members.skills.read": { const skill = skillOf(p); return { skill: skillRow(skill), text: skillText(skill) }; }
      case "members.skills.update": {
        const skill = skillOf(p);
        if (p.text !== undefined) {
          // as the core refuses a document it cannot keep: nothing changes
          const name = frontmatter(p.text, "name"), description = frontmatter(p.text, "description");
          if (!name) throw new Error("Invalid params: name must not be empty");
          if (!description) throw new Error("Invalid params: description must not be empty");
          if (data.skills.some(other => other !== skill && other.member_id === skill.member_id && other.name.toLowerCase() === name.toLowerCase())) throw new Error("Invalid params: a skill with that name already exists");
          Object.assign(skill, { name, description, text: p.text });
        }
        if (p.enabled !== undefined) skill.enabled = p.enabled;
        notify("MembersChanged", { member_id: p.member_id });
        return { skill: skillRow(skill) };
      }
      case "members.skills.delete": {
        const deleted = data.skills.some(item => item.id === p.id && item.member_id === p.member_id);
        data.skills = data.skills.filter(item => !(item.id === p.id && item.member_id === p.member_id));
        notify("MembersChanged", { member_id: p.member_id });
        return { deleted };
      }
      case "members.commands.list": return { commands: byMember(data.commands, p) };
      case "members.voices": return data.voices;
      case "groups.create": { const group = { ...p, id: nextId(data.groups), position: data.groups.length, sources: [] }; data.groups.push(group); notify("MembersChanged", { member_id: 0 }); return { group }; }
      case "groups.update": { const group = update(data.groups, p.id, p); notify("MembersChanged", { member_id: 0 }); return { group }; }
      case "groups.delete": data.groups = data.groups.filter(item => item.id !== p.id); notify("MembersChanged", { member_id: 0 }); return { deleted: true };
      case "conversations.list": return { conversations: byMember(data.conversations, p) };
      case "conversations.create": { const conversation = { id: nextId(data.conversations), member_id: p.member_id ?? 0, title: p.title ?? "", is_default: false, created_at_ms: data.now, updated_at_ms: data.now }; data.conversations.push(conversation); return { conversation }; }
      case "conversations.rename": update(data.conversations, p.id, { title: p.title }); return {};
      case "conversations.delete": data.conversations = data.conversations.filter(item => item.id !== p.id); return { deleted: true };
      case "agent.history.list": {
        // As the core lists them: a member's main conversation also holds what colleagues asked it.
        const main = data.conversations.find(c => c.member_id === (p.member_id ?? 0) && c.is_default)?.id;
        return { turns: byMember(data.turns, p).filter(turn => p.conversation_id === undefined || turn.conversation_id === p.conversation_id || (turn.scope === "exchange" && p.conversation_id === main)).slice().reverse() };
      }
      case "agent.history.search": case "messages.search": return { turns: byMember(data.turns, p).filter(turn => `${turn.question} ${turn.text} ${turn.messages.map(message => message.text).join(" ")}`.includes(p.query)).map(turn => ({ ...turn, conversation_title: data.conversations.find(c => c.id === turn.conversation_id)?.title })) };
      case "agent.history.delete": data.turns = data.turns.filter(item => item.id !== p.id); notify("AgentTurn", { turn_id: p.id, member_id: p.member_id ?? 0 }); return { deleted: true };
      case "agent.history.stop": {
        // As the core settles a stopped request: denied, with no answer, keeping its public steps.
        for (const turn of data.turns.filter(item => item.session_id === p.session_id && item.member_id === (p.member_id ?? 0) && ["running", "confirm"].includes(item.status))) {
          Object.assign(turn, { status: "denied", prefix: "了", text: "已取消。", messages: turn.messages.filter(message => message.kind !== "reply") });
          notify("AgentTurn", { turn_id: turn.id, member_id: turn.member_id });
        }
        return { stopped: true };
      }
      case "agent.speech.status": return data.speech;
      case "agent.question.open": return null;
      case "background.questions.list": return { questions: [] };
      case "quota.list": return { quotas: data.allowances.quotas };
      case "memory.list": return { memories: p.space === "shared" ? data.sharedMemories : byMember(data.memories, p), summary: null };
      case "safety.rules.list": return { rules: data.safetyRules };
      case "routines.list": return { routines: p.member_id === undefined ? data.routines : byMember(data.routines, p) };
      case "routines.runs": return { runs: data.routineRuns.filter(run => run.routine_id === p.routine_id), total: data.routineRuns.filter(run => run.routine_id === p.routine_id).length };
      case "routines.create": { const routine = { ...p, id: nextId(data.routines), enabled: true, last_run: null, next_run: data.now + 86400000 }; data.routines.push(routine); notify("RoutinesChanged", { member_id: routine.member_id }); return { routine }; }
      case "routines.update": { const routine = update(data.routines, p.id, p); notify("RoutinesChanged", { member_id: routine.member_id }); return { routine }; }
      case "routines.delete": data.routines = data.routines.filter(item => item.id !== p.id); notify("RoutinesChanged", { member_id: 0 }); return { deleted: true };
      case "rooms.list": return { rooms: data.rooms };
      case "rooms.messages": return { messages: data.messages.filter(item => item.room_id === p.room_id), exchange: data.roomExchanges.filter(item => item.room_id === p.room_id).at(-1) ?? null, handoffs: data.roomHandoffs };
      case "rooms.create": { const room = { ...p, id: nextId(data.rooms), created: data.now, group_id: p.group_id ?? null, is_team: Number(p.is_team ?? false), default_responder: p.default_responder ?? "auto" }; data.rooms.push(room); return { room }; }
      case "rooms.update": return { room: update(data.rooms, p.room_id, p) };
      case "rooms.delete": data.rooms = data.rooms.filter(item => item.id !== p.room_id); return { deleted: true };
      case "rooms.stop": { for (const exchange of data.roomExchanges.filter(item => item.room_id === p.room_id && ["running", "queued"].includes(item.state))) exchange.stop_requested = 1; for(const exchange of data.roomExchanges.filter(item=>item.room_id===p.room_id)){exchange.state="stopped";exchange.active_turn=null;}for(const message of data.messages.filter(item=>item.room_id===p.room_id&&item.status==="running")){message.status="error";message.text="已停止工作。";}for(const task of data.roomHandoffs.filter(item=>["queued","running","waiting"].includes(item.state)))task.state="cancelled";data.messages.push({...data.roomWrapupMessage,id:nextId(data.messages),reply_to:null,reply_to_member:null,text:"### 結論\n已停止本次工作，已完成的動作保留。\n\n### 待決定\n尚未完成的工作已取消。\n\n### 下一步\n等待你的新指示。"});notify("RoomsChanged", { room_id: p.room_id }); return { stopped: true }; }
      case "rooms.send": { const message = { id: nextId(data.messages), room_id: p.room_id, member_id: null, text: p.text, request_id: p.request_id, status: "done", created: data.now, reply_to: p.reply_to ?? null, turn_id: null, reply_to_member: data.messages.find(item => item.id === p.reply_to)?.member_id ?? null, origin_message_id: nextId(data.messages), kind: "message" }; data.messages.push(message); return { message_id: message.id, duplicate: false }; }
      case "brains.cli": return { clis: data.clis };
      case "brains.cli_disconnect": {
        const cli = data.clis.find(cli => cli.id === p.id);
        cli.disconnected = true; cli.available = false;
        cli.savedModels = cli.models; cli.models = [];
        notify("BrainsChanged", {});
        return { disconnected: true };
      }
      case "brains.cli_login": {
        const cli = data.clis.find(cli => cli.id === p.id);
        cli.disconnected = false;
        cli.available = cli.installed && cli.logged_in === true;
        if (cli.available && cli.savedModels) cli.models = cli.savedModels;
        notify("BrainsChanged", {});
        return { started: !cli.available, reconnected: cli.available };
      }
      case "models.catalog": return data.catalog;
      case "models.status": return data.modelStatus;
      case "models.local": return { models: data.localModels };
      case "providers.list": return { providers: data.providers };
      case "providers.kinds": return { kinds: data.providerKinds };
      case "providers.models": return { models: data.catalog.llm.map(model => ({ id: model.id, name: model.name, context: model.context_length, vision: true, tools: true, audio_in: false, price_in: model.input_per_mtok, price_out: model.output_per_mtok })) };
      case "languages.list": return { groups: data.languages };
      case "audio.devices": return data.audioDevices;
      case "audio.monitor": return {};
      case "services.status": return data.serviceStatus;
      case "connectors.status": return { connectors: data.managedConnectors, composio_configured: data.composioConfigured, google_configured: true };
      case "connectors.catalog": {
        if (!data.composioConfigured) return { ok: false, error: "missing_key" };
        return { items: data.pluginCatalog.filter(s => (!p.search || `${s.name} ${s.description}`.toLowerCase().includes(p.search.toLowerCase())) && (!p.category || s.categories.includes(p.category))), next_cursor: null };
      }
      case "connectors.categories": return { items: data.pluginCategories };
      case "connectors.tools": return { items: data.pluginTools[p.id] ?? [] };
      case "connectors.set_key": data.composioConfigured = true; return { configured: true };
      case "connectors.set_tools": {
        const connector = data.managedConnectors.find(c => c.id === p.id);
        if (p.enabled !== undefined) connector.enabled = p.enabled;
        if (p.disabled_tools) {
          connector.disabled_tools = p.disabled_tools;
          for (const tool of data.pluginTools[p.id] ?? []) tool.enabled = !p.disabled_tools.includes(tool.slug);
          connector.enabled_tools = (data.pluginTools[p.id] ?? []).filter(t => t.enabled).map(t => t.slug);
        }
        state.emit({ type: "ConnectionsChanged" }); return { connector };
      }
      case "connectors.connect": {
        if (variant.pluginsConnecting) return new Promise(() => {});
        const entry = data.pluginCatalog.find(s => s.slug === p.toolkit);
        data.managedConnectors.push({ id: `composio:${entry.slug}`, kind: "composio", name: entry.name, description: entry.description, status: "ACTIVE", enabled: true, enabled_tools: [], disabled_tools: [] });
        state.emit({ type: "ConnectionsChanged" }); return { connected: true };
      }
      case "connectors.google.connect": return { connected: true };
      case "connectors.disconnect": data.managedConnectors = data.managedConnectors.filter(c => c.id !== p.id); state.emit({ type: "ConnectionsChanged" }); return { disconnected: true };
      case "integrations.card_connect": {
        const turn = data.turns.find(item => item.connectors?.some(card => card.id === p.id));
        const card = turn?.connectors.find(item => item.id === p.id);
        if (!card) throw new Error(`Fixture card not found: ${p.id}`);
        if (!["pending", "failed", "cancelled"].includes(card.state)) return { card: copy(card), started: false };
        Object.assign(card, { state: "authorizing", error: null });
        notify("AgentTurn", { turn_id: turn.id, member_id: turn.member_id });
        if (variant.cardSignIn === "approve") {
          setTimeout(() => {
            Object.assign(card, { state: "connected", tool_count: 2, resumed: true });
            data.turns.push(structuredClone(data.connectorResume));
            state.emit({ type: "AgentTurn", turn_id: turn.id, member_id: turn.member_id });
          }, 0);
        }
        return { card: copy(card), started: true };
      }
      case "integrations.card_save": {
        const turn = data.turns.find(item => item.secrets?.some(card => card.id === p.id));
        const card = turn?.secrets.find(item => item.id === p.id);
        if (!card) throw new Error(`Fixture card not found: ${p.id}`);
        if (typeof p.value !== "string" || !p.value.trim()) throw new Error("Fixture: a key is needed");
        if (!["pending", "failed"].includes(card.state)) return { card: copy(card), saved: false };
        Object.assign(card, { state: "saved", error: null });
        notify("AgentTurn", { turn_id: turn.id, member_id: turn.member_id });
        setTimeout(() => {
          if (variant.resumeFails) Object.assign(card, { state: "saved", error: "resume_failed" });
          else {
            Object.assign(card, { state: "resumed", resumed: true });
            data.turns.push(structuredClone(data.secretResume));
          }
          state.emit({ type: "AgentTurn", turn_id: turn.id, member_id: turn.member_id });
        }, 0);
        return { card: copy(card), saved: true };
      }
      case "integrations.card_resume": {
        const turn = data.turns.find(item => item.secrets?.some(card => card.id === p.id));
        const card = turn?.secrets.find(item => item.id === p.id);
        if (!card) throw new Error(`Fixture card not found: ${p.id}`);
        if (card.state !== "saved" || !card.error) return { card: copy(card), resumed: false };
        Object.assign(card, { state: "resumed", error: null, resumed: true });
        data.turns.push(structuredClone(data.secretResume));
        notify("AgentTurn", { turn_id: turn.id, member_id: turn.member_id });
        return { card: copy(card), resumed: true };
      }
      case "integrations.card_done": {
        const turn = data.turns.find(item => item.computers?.some(card => card.id === p.id));
        const card = turn?.computers.find(item => item.id === p.id);
        if (!card) throw new Error(`Fixture card not found: ${p.id}`);
        if (card.state !== "control") return { card: copy(card), done: false };
        Object.assign(card, { state: "returning", note: null });
        notify("AgentTurn", { turn_id: turn.id, member_id: turn.member_id });
        setTimeout(() => {
          if (variant.signedIn) {
            Object.assign(card, { state: "done" });
            data.turns.push(structuredClone(data.computerResume));
          } else Object.assign(card, { state: "control", note: "still" });
          state.emit({ type: "AgentTurn", turn_id: turn.id, member_id: turn.member_id });
        }, 0);
        return { card: copy(card), done: true };
      }
      case "integrations.card_stop": {
        const turn = data.turns.find(item => item.computers?.some(card => card.id === p.id));
        const card = turn?.computers.find(item => item.id === p.id);
        if (!card) throw new Error(`Fixture card not found: ${p.id}`);
        if (!["control", "returning"].includes(card.state)) return { card: copy(card), stopped: false };
        Object.assign(card, { state: "stopped", note: null });
        notify("AgentTurn", { turn_id: turn.id, member_id: turn.member_id });
        return { card: copy(card), stopped: true };
      }
      case "integrations.card_cancel": {
        const turn = data.turns.find(item => item.connectors?.some(card => card.id === p.id));
        const card = turn?.connectors.find(item => item.id === p.id);
        if (card?.state !== "authorizing") return { cancelled: false };
        card.state = "cancelled";
        notify("AgentTurn", { turn_id: turn.id, member_id: turn.member_id });
        return { cancelled: true };
      }
      case "integrations.draft_save": case "integrations.draft_send": case "integrations.draft_discard": case "integrations.draft_handover": {
        let turn, draft;
        for (const item of data.turns) {
          draft = item.drafts?.find(each => each.id === p.id && each.mode === "card");
          if (draft) { turn = item; break; }
        }
        if (!draft) throw new Error(`Fixture draft not found: ${p.id}`);
        const changed = () => { notify("AgentTurn", { turn_id: turn.id, member_id: turn.member_id }); return turn; };
        const open = ["draft", "failed"].includes(draft.state);
        if (method === "integrations.draft_save") {
          if (p.digest !== draft.digest || !open) return { draft: copy(draft), saved: false };
          const fields = { to: p.to ?? draft.to, cc: p.cc ?? draft.cc, subject: p.subject ?? draft.subject, body: p.body ?? draft.body };
          const from = draft.senders.find(sender => sender.key === p.via) ?? draft.from;
          if (from.key === draft.from.key && Object.keys(fields).every(key => fields[key] === draft[key])) return { draft: copy(draft), saved: false };
          // A new version with a new digest, as the core makes one.
          const version = draft.version + 1;
          Object.assign(draft, fields, { from, version, digest: String(version).padStart(64, "b"), error: null, state: "draft" });
          changed();
          return { draft: copy(draft), saved: true };
        }
        if (method === "integrations.draft_send") {
          if (p.digest !== draft.digest || draft.state !== "draft") return { draft: copy(draft), sent: false };
          state.sent.push({ id: draft.id, digest: p.digest, via: draft.from.key, to: draft.to, cc: draft.cc, subject: draft.subject, body: draft.body });
          draft.state = "sent";
          changed();
          return { draft: copy(draft), sent: true };
        }
        if (method === "integrations.draft_discard") {
          const discarded = open;
          if (discarded) { draft.state = "discarded"; changed(); }
          return { draft: copy(draft), discarded };
        }
        // The owner's answer to 「這封回信可以交給…寄嗎？」: yes moves the same version to the colleague's chat, as a card
        // on a turn of that chat, and leaves one centred line in this one. It never sends.
        if (!draft.offer || draft.state !== "draft") return { draft: copy(draft), handed: false };
        if (p.choice === "no") { draft.offer = null; changed(); return { draft: copy(draft), handed: false, declined: true }; }
        const author = data.members.find(member => member.id === turn.member_id).name;
        const carrier = { id: nextId(data.turns), session_id: `draft-${draft.id}-${draft.version}`, member_id: draft.offer.member_id,
          conversation_id: data.colleagueConversation.id, created_at_ms: data.now, question: "", route: "draft", source: "draft", prefix: "",
          text: `${author}把一封郵件草稿「${draft.subject}」交給你，等主人編輯後寄出。`, detail: null, status: "done", cost_usd: 0, model: null,
          steps: [], attachments: [], messages: [], drafts: [{ ...structuredClone(draft), offer: null, holder_id: draft.offer.member_id, author_name: author }] };
        turn.drafts = turn.drafts.map(each => each === draft ? { id: draft.id, mode: "handed", state: draft.state, holder_id: draft.offer.member_id, holder_name: draft.offer.name } : each);
        data.turns.push(carrier);
        notify("AgentTurn", { turn_id: carrier.id, member_id: carrier.member_id });
        changed();
        return { draft: copy(carrier.drafts[0]), handed: true };
      }
      case "toolsets.list": return { groups: data.servers };
      case "integrations.catalog": return { services: data.services };
      case "integrations.audit": return { entries: [] };
      case "integrations.inventory": return inventory();
      case "integrations.test": return { ok: true, tools: ["search", "save"] };
      case "integrations.preview": { const entries = Object.entries(JSON.parse(p.text).mcpServers ?? {}); return { candidates: entries.map(([name, definition], index) => ({ ...data.candidates[0], ...definition, id: `paste-${index}`, name })), rejected: 0 }; }
      case "integrations.discover": return { candidates: data.candidates, rejected: 0 };
      case "integrations.import": return { servers: data.candidates.filter(item => p.ids.includes(item.id)) };
      case "integrations.add": { const server = { ...data.servers[0], ...p, id: `server-${data.servers.length + 1}` }; data.servers.push(server); return { server }; }
      case "integrations.update": return { server: update(data.servers, p.id, p) };
      case "integrations.set_enabled": return { server: update(data.servers, p.id, { enabled: p.enabled, status: p.enabled ? "ready" : "disabled" }) };
      case "integrations.refresh": return { server: data.servers.find(s => s.id === p.id) };
      case "integrations.set_tool_enabled": {
        data.servers.find(s => s.id === p.id).tools.find(t => t.name === p.tool).enabled = p.enabled;
        return {};
      }
      case "integrations.set_tools_enabled": {
        for (const tool of data.servers.find(s => s.id === p.id).tools) if (p.tools.includes(tool.name)) tool.enabled = p.enabled;
        return {};
      }
      case "integrations.disconnect": update(data.servers, p.id, { enabled: false, status: "disconnected", auth: "none" }); return { disconnected: true };
      case "usage.runs": return { ...data.usage, range: p.range ?? data.usage.range, bill: usageBill(p.range ?? "month"), fish_voice: !!(data.settings.tts_enabled && data.settings.tts_service === "fish") };
      case "usage.allowances": return data.allowances;
      case "usage.analytics": return { ...data.analytics, range: p.range ?? data.analytics.range };
      case "usage.export": return { csv: data.usageCsv };
      case "messaging.telegram.status": return data.messaging;
      default: return unsupported(method);
    }
  }
  const callbacks = new Map(); let callbackId = 0;
  target.__TAURI_INTERNALS__ = {
    metadata: { currentWindow: { label: "main" }, currentWebview: { label: "main" } },
    transformCallback(fn) { callbacks.set(++callbackId, fn); return callbackId; },
    unregisterCallback(id) { callbacks.delete(id); },
    runCallback(id, value) { callbacks.get(id)?.(value); },
    convertFileSrc(path) { return path; },
    async invoke(command, args = {}) {
      state.calls.push({ command, ...copy(command === "main_events" ? {} : args) });
      // Native Channel instances contain callbacks; do not serialize them before registration.
      if (command === "overlay_subscribe") {
        state.overlayChannel = args.channel;
        for (const event of variant.overlayEvents ?? []) args.channel.onmessage(copy(event));
        return;
      }
      if (command === "overlay_log") return;
      if (command === "main_events") { state.channel = args.channel; return; }
      // Escape reaches the native worker even when no capture is active.
      if (command === "window_hotkey" && args.kind === "cancel") return;
      if (command === "agent_ask_text") {
        if (variant.firstBot) {
          const profile = variant.firstBot;
          if (![profile.name, profile.personality, profile.instructions].every(text => args.text.includes(text))) throw new Error("First Bot answers were lost at the native boundary");
          adminTurn = { ...copy(fixture.turns[0]), id: 90, member_id: 0, conversation_id: 1, question: args.text,
            status: "confirm", text: "", detail: null, messages: [] };
          data.turns.push(adminTurn);
          adminQuestion = { ...copy(data.adminQuestions.draft), id: "first-bot-profile", turn_id: 90, groups: [],
            drafts: [{ id: "draft-1", member_id: 0, name: profile.name, personality: profile.personality,
              instructions: profile.instructions, role: "", blurb: "", group: "", avatar_preset: "" }] };
          notify("AgentTurn", { turn_id: 90, member_id: 0 });
          notify("AgentQuestion", copy(adminQuestion));
        }
        return null;
      }
      if (command === "core_call") return copy(await core(args.method, args.params));
      if (command === "device_settings_get") return copy(data.device);
      if (command === "device_settings_set") { Object.assign(data.device, args.patch); notify("DeviceSettings", { settings: copy(data.device) }); return copy(data.device); }
      if (command === "avatar_get") return copy(data.avatar);
      if (command === "agent_question_open") return copy(adminQuestion ?? permission?.question ?? null);
      if (command === "agent_question_answer" && permission) {
        if (args.id !== permission.question.id) throw new Error("Unknown permission question");
        // The core settles the card once, and the turn with what Raphael says of it.
        const { turn, card } = permission;
        card.state = !args.approved ? "denied" : args.always ? "always" : "allowed";
        const text = args.approved ? permission.done : permission.denied;
        Object.assign(turn, { status: args.approved ? "done" : "denied", prefix: args.approved ? "答" : "了", text, messages: [{ seq: 1, kind: "reply", text, at_ms: data.now }] });
        notify("AgentQuestionClosed", { id: card.id });
        notify("AgentTurn", { turn_id: turn.id, member_id: turn.member_id });
        permission = null;
        return;
      }
      if (command === "agent_question_answer" && adminQuestion) {
        if (args.id !== adminQuestion.id) throw new Error("Unknown approval question");
        if (variant.firstBot) {
          if (args.approved) {
            Object.assign(data.members[0], args.drafts[0], { id: 0, profile_ready: true });
            adminTurn.status = "done";
            adminTurn.text = `已重新命名為 ${data.members[0].name}`;
            adminTurn.detail = data.members[0].name;
          } else { adminTurn.status = "denied"; adminTurn.text = "已取消。"; }
          adminTurn.messages = [{ seq: 1, kind: "reply", text: adminTurn.text, at_ms: data.now }];
          notify("MembersChanged", { member_id: 0 });
          notify("AgentQuestionClosed", { id: adminQuestion.id });
          notify("AgentTurn", { turn_id: adminTurn.id, member_id: 0 });
          adminQuestion = null;
          return;
        }
        if (Array.isArray(args.drafts)) {
          if (args.approved) {
            const groups = args.groups ?? (data.adminGroup ? [data.adminGroup] : []);
            for (const proposed of groups) {
              const existing = data.groups.find(g => g.id === proposed.group_id);
              if (existing) existing.name = proposed.name;
              else if (!data.groups.some(g => g.name === proposed.name)) {
                data.groups.push({ ...copy(data.adminGroup), name: proposed.name });
              }
            }
            let nextMemberId = 8;
            for (const draft of args.drafts) {
              const group = data.groups.find(g => g.name === draft.group) ?? null;
              const id = nextMemberId++;
              data.members.push({
                ...data.members[1],
                ...data.adminMember,
                id,
                name: draft.name,
                role: draft.role,
                blurb: draft.blurb,
                personality: draft.personality ?? "",
                instructions: draft.instructions ?? "",
                group_id: group ? group.id : null,
                avatar: draft.avatar_preset ? (draft.avatar_preset.startsWith("preset:") ? draft.avatar_preset : `preset:${draft.avatar_preset}`) : "",
                avatar_url: null,
                position: data.members.length,
              });
              data.conversations.push({ id: 80 + id, member_id: id, title: "", is_default: true, created_at_ms: data.now, updated_at_ms: data.now });
            }
            notify("MembersChanged", { member_id: 8 });
            const names = args.drafts.map(d => d.name).join("、");
            adminTurn.status = "done";
            adminTurn.text = `已建立 ${names}`;
            adminTurn.detail = args.drafts.map(d => {
              const head = [d.name, d.role, d.group].filter(Boolean).join(" · ");
              return d.blurb ? `${head} — ${d.blurb}` : head;
            }).join("\n");
            adminTurn.messages.push({ seq: 4, kind: "reply", text: adminTurn.text, at_ms: data.now });
          } else {
            adminTurn.status = "denied";
            adminTurn.text = "已取消建立成員。";
            adminTurn.messages.push({ seq: 4, kind: "reply", text: adminTurn.text, at_ms: data.now });
          }
          notify("AgentQuestionClosed", { id: adminQuestion.id });
          notify("AgentTurn", { turn_id: adminTurn.id, member_id: 0 });
          adminQuestion = null;
          return;
        }
        if (args.approved && adminQuestion.tier !== "T3") {
          data.groups.push(copy(data.adminGroup));
          data.members.push({ ...data.members[1], ...data.adminMember });
          data.conversations.push({ id: 80, member_id: data.adminMember.id, title: "", is_default: true, created_at_ms: data.now, updated_at_ms: data.now });
          notify("MembersChanged", { member_id: data.adminMember.id });
        }
        adminTurn.status = args.approved ? "done" : "denied";
        adminTurn.text = args.approved ? "已建立 COO：營運長。" : "已取消建立成員。";
        adminTurn.messages.push({ seq: 4, kind: "reply", text: adminTurn.text, at_ms: data.now });
        notify("AgentQuestionClosed", { id: adminQuestion.id });
        notify("AgentTurn", { turn_id: adminTurn.id, member_id: 0 });
        adminQuestion = null;
        return;
      }
      if (command === "avatar_pick") return null;
      if (command === "plugin:app|version") return "0.2.0";
      if (command === "plugin:event|listen") return ++callbackId;
      if (command === "plugin:window|is_maximized") return maximized;
      if (command === "plugin:window|toggle_maximize") { maximized = !maximized; return; }
      if (command === "keyboard_has_altgr") return false;
      if (command === "mic_access") return { allowed: true, blocked_by: null };
      if (command === "schedule_keep_awake_get") return data.awake ?? false;
      if (command === "schedule_keep_awake_set") return data.awake = args.enabled;
      if (["open_external_url", "open_link"].includes(command)) { state.urls.push(args.url); return; }
      if (command === "save_usage_csv") { state.exported = args.csv; return true; }
      if (["agent_page_shown", "hotkeys_pause", "hotkeys_practice", "plugin:event|unlisten", "plugin:window|set_focus", "plugin:webview|set_webview_zoom"].includes(command)) return;
      return unsupported(command);
    },
  };
  target.__TAURI_EVENT_PLUGIN_INTERNALS__ = { unregisterListener() {} };
  return state;
}
