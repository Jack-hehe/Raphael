// Only the native boundary is replaced; the real index.html, modules and CSS still run.
export function installMock(fixture, config = {}, target = window) {
  const data = structuredClone(fixture);
  const variant = config.fixture ?? {};
  for (const [key, value] of Object.entries(variant)) if (key in data) data[key] = structuredClone(value);
  Object.assign(data.device, { appearance: config.theme ?? "light", ui_language: config.locale ?? "zh-TW", onboarded: !variant.onboarding });
  data.settings.skin = config.skin ?? "default";
  if (variant.onlyRaphael) data.members = data.members.filter(member => member.id === 0);
  if (variant.waitingMember !== undefined) data.members.find(member => member.id === variant.waitingMember).waiting = true;
  if (variant.problem) data.status.openrouter.state = "no_key";
  let adminQuestion = variant.adminApproval ? data.adminQuestions[variant.adminApproval] : null;
  const adminTurn = adminQuestion ? data.turns.find(turn => turn.id === adminQuestion.turn_id) : null;
  if (adminQuestion && !adminTurn) throw new Error("Approval fixture requires its explicit source turn");
  if (adminQuestion) {
    adminTurn.status = "confirm";
    adminTurn.messages = adminTurn.messages.filter(message => message.kind !== "reply");
    adminTurn.text = "";
  }
  if (variant.running) {
    data.turns[0].status = "running";
    data.turns[0].messages = data.turns[0].messages.filter(message => message.kind !== "reply");
    data.turns[0].text = "";
  }
  if (variant.roomThinking) { data.messages.push(...data.roomThinkingMessages); data.roomExchanges.push(data.roomActiveExchange); }
  if (variant.roomDiscussion || variant.roomWrapup) data.messages.push(...data.roomDiscussionMessages);
  if (variant.roomWrapup) data.messages.push(data.roomWrapupMessage);
  if (variant.roomMissingSource) data.messages[2].reply_reference = { message_id: 999, unavailable: true };
  if (variant.usageEmpty) {
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
  const state = { data, calls: [], errors: [], urls: [], exported: null, channel: null, emit(event) { state.channel?.onmessage(event); } };
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
      case "members.list": return { members: data.members, groups: data.groups };
      case "members.presets": return { presets: data.presets };
      case "members.viewing": case "members.read": return {};
      case "members.tool_choices": return { tools: [], connections: data.servers };
      case "members.update": { const member = update(data.members, p.id, p); notify("MembersChanged", { member_id: p.id }); return { member }; }
      case "members.create": { const member = { ...data.members[1], ...p, id: nextId(data.members) }; data.members.push(member); notify("MembersChanged", { member_id: member.id }); return { member, routine_offer: null }; }
      case "members.delete": data.members = data.members.filter(item => item.id !== p.id); notify("MembersChanged", { member_id: p.id }); return { deleted: true };
      case "members.skills.list": return { skills: byMember(data.skills, p) };
      case "members.commands.list": return { commands: byMember(data.commands, p) };
      case "members.voices": return data.voices;
      case "groups.create": { const group = { ...p, id: nextId(data.groups), position: data.groups.length, sources: [] }; data.groups.push(group); notify("MembersChanged", { member_id: 0 }); return { group }; }
      case "groups.update": { const group = update(data.groups, p.id, p); notify("MembersChanged", { member_id: 0 }); return { group }; }
      case "groups.delete": data.groups = data.groups.filter(item => item.id !== p.id); notify("MembersChanged", { member_id: 0 }); return { deleted: true };
      case "conversations.list": return { conversations: byMember(data.conversations, p) };
      case "conversations.create": { const conversation = { id: nextId(data.conversations), member_id: p.member_id ?? 0, title: p.title ?? "", is_default: false, created_at_ms: data.now, updated_at_ms: data.now }; data.conversations.push(conversation); return { conversation }; }
      case "conversations.rename": update(data.conversations, p.id, { title: p.title }); return {};
      case "conversations.delete": data.conversations = data.conversations.filter(item => item.id !== p.id); return { deleted: true };
      case "agent.history.list": return { turns: byMember(data.turns, p).filter(turn => p.conversation_id === undefined || turn.conversation_id === p.conversation_id).slice().reverse() };
      case "agent.history.search": case "messages.search": return { turns: byMember(data.turns, p).filter(turn => `${turn.question} ${turn.text} ${turn.messages.map(message => message.text).join(" ")}`.includes(p.query)).map(turn => ({ ...turn, conversation_title: data.conversations.find(c => c.id === turn.conversation_id)?.title })) };
      case "agent.history.delete": data.turns = data.turns.filter(item => item.id !== p.id); notify("AgentTurn", { turn_id: p.id, member_id: p.member_id ?? 0 }); return { deleted: true };
      case "agent.speech.status": return data.speech;
      case "agent.question.open": return null;
      case "background.questions.list": return { questions: [] };
      case "quota.list": return { quotas: data.allowances.quotas };
      case "memory.list": return { memories: byMember(data.memories, p), summary: null };
      case "safety.rules.list": return { rules: data.safetyRules };
      case "routines.list": return { routines: p.member_id === undefined ? data.routines : byMember(data.routines, p) };
      case "routines.runs": return { runs: data.routineRuns.filter(run => run.routine_id === p.routine_id), total: data.routineRuns.filter(run => run.routine_id === p.routine_id).length };
      case "routines.create": { const routine = { ...p, id: nextId(data.routines), enabled: true, last_run: null, next_run: data.now + 86400000 }; data.routines.push(routine); notify("RoutinesChanged", { member_id: routine.member_id }); return { routine }; }
      case "routines.update": { const routine = update(data.routines, p.id, p); notify("RoutinesChanged", { member_id: routine.member_id }); return { routine }; }
      case "routines.delete": data.routines = data.routines.filter(item => item.id !== p.id); notify("RoutinesChanged", { member_id: 0 }); return { deleted: true };
      case "rooms.list": return { rooms: data.rooms };
      case "rooms.messages": return { messages: data.messages.filter(item => item.room_id === p.room_id), exchange: data.roomExchanges.filter(item => item.room_id === p.room_id).at(-1) ?? null };
      case "rooms.create": { const room = { ...p, id: nextId(data.rooms), created: data.now, group_id: p.group_id ?? null, is_team: Number(p.is_team ?? false), default_responder: p.default_responder ?? "auto" }; data.rooms.push(room); return { room }; }
      case "rooms.update": return { room: update(data.rooms, p.room_id, p) };
      case "rooms.delete": data.rooms = data.rooms.filter(item => item.id !== p.room_id); return { deleted: true };
      case "rooms.stop": { for (const exchange of data.roomExchanges.filter(item => item.room_id === p.room_id && ["running", "queued"].includes(item.state))) exchange.stop_requested = 1; notify("RoomsChanged", { room_id: p.room_id }); return { stopped: true }; }
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
      case "toolsets.list": return { groups: data.servers };
      case "integrations.catalog": return { services: data.services };
      case "integrations.audit": return { entries: [] };
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
      case "usage.runs": return { ...data.usage, range: p.range ?? data.usage.range };
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
      if (command === "agent_ask_text") return null;
      if (command === "core_call") return copy(await core(args.method, args.params));
      if (command === "device_settings_get") return copy(data.device);
      if (command === "device_settings_set") { Object.assign(data.device, args.patch); notify("DeviceSettings", { settings: copy(data.device) }); return copy(data.device); }
      if (command === "avatar_get") return copy(data.avatar);
      if (command === "agent_question_open") return copy(adminQuestion);
      if (command === "agent_question_answer" && adminQuestion) {
        if (args.id !== adminQuestion.id) throw new Error("Unknown approval question");
        if (Array.isArray(args.drafts)) {
          if (args.approved) {
            if (data.adminGroup && !data.groups.some(g => g.name === data.adminGroup.name)) {
              data.groups.push(copy(data.adminGroup));
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
