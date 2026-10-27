import { scene, click, hover, type, wait, page, settings, scroll, key, check } from "./steps.mjs";

const connections = settings("connections");
const add = [...connections, click('[data-k="conn-add"]')];
const custom = [...add, click('.menu [data-value="custom"]')];
const serverDetail = [...connections, click('[data-conn="open"][data-id="notes"]')];
const store = [...connections, click('[data-conn="tab"][data-id="store"]'), wait('.cs-card')];
const usage = [...settings("models"), click('[data-models-tab="usage"]'), wait(".run-tile"), wait(".run-bill-total"), check("usage-summary")];
const cliMenu = [...settings("models"), scroll(".cli-accounts"), click('[data-cli-menu="codex"]')];
const cliDisconnected = [...cliMenu, click('#app-popover [data-pick="disconnect"]'), wait('[data-cli="codex"]')];
const modelChooser = role => [...settings("models"), click(`[data-model-picker="${role}"] .mp-trigger`), wait(".model-chooser .mc-row")];
const localChooser = [...modelChooser("stt"), click('[data-source="local-speech"]'), wait(".lm-main")];
const dictation = tab => [page("dictation"), click(`[data-dictation-tab="${tab}"]`), check("dictation-navigation"), ...(tab === "overview" ? [wait('.page-overview .icard'), check("dictation-overview")] : [])];
export default [
  scene("quick-model-menu", "Quick questions shared model picker in existing model settings", [...settings("models"), click('[data-k="quick-model"]'), wait('.model-chooser'), click('[data-source="openrouter"]'), wait('.model-chooser [data-model="openai/gpt-6-luna"]')]),
  scene("quick-effort-menu", "Quick questions pick their effort inside the shared model list, as the answer model does, starting quick (low)", [
    ...settings("models"), check("quick-model-row"), click('[data-k="quick-model"]'), wait('.model-chooser'), click('.model-chooser [data-source="cli:codex"]'),
    click('.model-chooser [data-model="cli:codex:gpt-6.1"]'), wait('.model-chooser .mc-effort [data-effort="low"][aria-pressed="true"]'), check("model-effort-order")]),
  scene("quick-model-selection", "Quick questions take a subscription model and its own effort while Raphael's answer model stays", [
    ...settings("models"), click('[data-k="quick-model"]'), wait('.model-chooser'), click('.model-chooser [data-source="cli:codex"]'),
    click('.model-chooser [data-model="cli:codex:gpt-6.1"]'), click('.model-chooser .mc-effort [data-effort="high"]'), click('.model-chooser [data-dlg="use"]'),
    wait('[data-k="quick-model"][aria-label*="GPT-6.1"]'), check("quick-model-row"), check("quick-model-selection")]),
  ...["answer", "cleanup", "stt"].map(role => scene(`model-choices-${role}`, `Spaced ${role} choices with separate metadata and actions`, [...modelChooser(role), check("model-choice-layout"), ...(role === "answer" ? [check("model-effort-order")] : role === "cleanup" ? [check("cleanup-no-effort")] : [])])),
  scene("model-choices-local", "Local speech choices keep subtle borders, spacing and readable metadata", [...localChooser, scroll('.lm-row:last-child'), check("model-choice-scroll"), scroll('.lm-row:first-child'), check("model-choice-layout")]),
  scene("model-choices-local-selected", "Preview a local model without downloading and keep keyboard focus visible", [...localChooser, click('.lm-main[data-name="qwen3-asr"]'), key("Tab"), { action: "focus", selector: '.lm-main[data-name="qwen3-asr"]' }, check("model-choice-selection"), check("model-choice-layout")]),
  scene("cli-account-menu", "Connected CLI account row actions", [...cliMenu, check("cli-account-menu")]),
  scene("cli-account-disconnected", "Disconnect only in Raphael and offer reconnection", [...cliDisconnected, check("cli-account-disconnected")]),
  scene("cli-account-reconnected", "Reconnect a signed-in CLI without starting login", [...cliDisconnected, click('[data-cli="codex"]'), wait('[data-cli-menu="codex"]'), check("cli-account-reconnected")]),
  ...["minimize", "maximize", "close"].map(control => scene(`window-hover-${control}`, `Transparent ${control} caption hover`, [{ action: "window-control", control }])),
  scene("window-hover-restore", "Restore caption keeps its centered glyph and transparent hover", [{ action: "window-control", control: "maximize", restore: true }]),
  scene("window-hover-modal", "Caption hover stays usable while settings dim the window", [...settings("models"), { action: "window-control", control: "close", hoverOnly: true }]),
  ...["general", "appearance", "connections", "models", "dictation", "agent", "messaging", "data", "about"].map(pane => scene(pane === "general" ? "profile" : pane, `${pane} settings pane`, settings(pane))),
  scene("skins", "Skin preview hover", [...settings("appearance"), hover('[data-skin-choice="paper"]')]),
  ...["default", "paper", "graphite", "lagoon", "dusk", "midnight", "meadow", "ink", "catppuccin"].map(skin => scene(`skin-${skin}`, `${skin} appearance previews`, [...settings("appearance"), click(`[data-skin-choice="${skin}"]`)])),
  scene("plugins-installed", "Unified managed, private Google and custom MCP plugins", [...connections, wait('[data-conn="open"][data-id="composio:notion"]')]),
  scene("plugins-detail", "Managed tools grouped by write and read", [...connections, click('[data-conn="open"][data-id="composio:notion"]'), wait('[data-conn="tool"][data-tool="NOTION_SEARCH"]')]),
  scene("plugins-store", "Built-in services followed by deduplicated Composio apps", [...store, wait('.cs-composio-section .cs-card'), check("plugins-store"), check("plugins-store-rows"), scroll('.cs-composio-section')]),
  scene("plugins-store-category", "Plugin category dropdown", [...store, wait('.cs-composio-section .cs-card'), click('[data-k="conn-category"]'), wait('#app-popover [data-value="productivity"]'), check("plugins-store-rows")]),
  scene("plugins-store-key", "Built-in services with optional Composio setup", [...store, wait('[data-conn="configure-composio"]'), check("plugins-store"), check("plugins-store-rows"), scroll('.cs-composio-setup')], { composioConfigured: false }),
  scene("plugins-store-key-openrouter", "An OpenRouter key gets an actionable, value-free Composio error", [
    ...connections, click('[data-conn="tab"][data-id="store"]'), wait('[data-conn="configure-composio"]'), click('[data-conn="configure-composio"]'), wait('#conn-key'),
    type('#conn-key', 'sk-or-look-synthetic'), click('[data-conn="save-key"]'), wait('.cs-key-form [role="alert"]'), check("plugins-key-openrouter"), check("plugins-store-rows"),
  ], { composioConfigured: false }),
  scene("plugins-store-key-consumer", "A consumer key directs the owner to a Composio project key without echoing it", [
    ...connections, click('[data-conn="tab"][data-id="store"]'), wait('[data-conn="configure-composio"]'), click('[data-conn="configure-composio"]'), wait('#conn-key'),
    type('#conn-key', 'ck_look-synthetic'), click('[data-conn="save-key"]'), wait('.cs-key-form [role="alert"]'), check("plugins-key-consumer"), check("plugins-store-rows"),
  ], { composioConfigured: false }),
  scene("plugins-connecting", "Built-in sign-in works without Composio", [...store, click('[data-conn="join"][data-id="github"]'), wait('.cs-card .sub.wait'), check("plugins-builtin-join"), check("plugins-store-rows")], { composioConfigured: false, pluginsConnecting: true }),
  scene("connections-hover", "MCP server hover", [...connections, hover('[data-k="cs-open:notes"]')]),
  scene("server-menu", "MCP server detail actions", [...serverDetail, click('[data-conn="server-menu"][data-id="notes"]')]),
  scene("connections-add-menu", "Add a connection menu", add),
  scene("connections-command", "Command MCP form", custom),
  scene("connections-test", "MCP test result", [...custom, type("#conn-name", "專案筆記"), type("#conn-command", "npx"), type("#conn-args", "-y\nnotes-server\nC:/Work/Team notes"), click('[data-conn="test-draft"]'), scroll(".add-grid [role=status]")]),
  scene("connections-url", "HTTP MCP form", [...custom, click('[data-k="conn-type"]'), click('.menu [data-value="http"]')]),
  scene("connections-edit", "Edit MCP server", [...serverDetail, click('[data-conn="server-menu"][data-id="notes"]'), click('[data-conn="edit-server"]')]),
  scene("connections-delete", "Custom MCP deletion confirmation in overflow", [...serverDetail, click('[data-conn="server-menu"][data-id="notes"]'), click('[data-conn="disconnect"]')]),
  scene("connections-paste", "Paste MCP JSON", [...add, click('.menu [data-value="paste"]')]),
  scene("connections-paste-preview", "Preview MCP JSON", [...add, click('.menu [data-value="paste"]'), type("[data-mcp-json]", '{"mcpServers":{"專案筆記":{"command":"npx","args":["notes-server"]}}}'), click('[data-dlg="preview"]')]),
  scene("connections-discover", "Discover local MCP definitions", [...add, click('.menu [data-value="import"]')]),
  scene("connections-empty", "Connections without servers", [...connections, click('[data-conn="tab"][data-id="installed"]')], { servers: [], managedConnectors: [] }),
  scene("connections-discover-empty", "Discovery without candidates", [...add, click('.menu [data-value="import"]')], { servers: [], candidates: [] }),
  scene("usage", "Usage summary and grouping", usage),
  scene("usage-bill-breakdown", "Ledger purposes reconcile exactly with the sidebar", [...usage, check("usage-bill-breakdown")], { usageFishVoice: true }),
  scene("usage-members", "Expanded member usage", [...usage, click('[data-run-act="members"]'), scroll(".run-members")]),
  scene("usage-period", "Usage period menu", [...usage, click('[data-run-act="period"]')]),
  ...["day", "provider", "routine"].map(group => scene(`usage-group-${group}`, `Usage grouped by ${group}`, [...usage, click(`[data-run-group="${group}"]`), scroll(".run-groups")])),
  scene("usage-allowances", "Subscription exclusion opens Claude, Codex and Gemini allowances", [...usage, click('[data-run-act="show-allowance"]'), check("usage-allowance-link")]),
  scene("usage-empty", "Usage without runs", usage, { usageEmpty: true }),
  ...["overview", "history", "dictionary"].map(tab => scene(`dictation-${tab}`, `Dictation ${tab}`, [...dictation(tab), ...(tab === "dictionary" ? [wait(".wrow"), click('[data-act="word-menu"]'), wait('#app-popover [data-pick="delete"]'), key("Escape")] : [])])),
  scene("dictation-keyboard", "Horizontal dictation navigation with keyboard focus", [...dictation("overview"), key("ArrowRight"), wait('[data-dictation-tab="history"][aria-selected="true"]'), key("ArrowLeft"), wait('[data-dictation-tab="overview"][aria-selected="true"]'), key("End"), key("Home"), wait('[data-dictation-tab="overview"][aria-selected="true"]'), key("End"), wait('[data-dictation-tab="dictionary"][aria-selected="true"]'), check("dictation-navigation")]),
  scene("dictation-history-day", "History filtered through the heatmap", [...dictation("overview"), click('.heat [data-date="2026-10-24"]'), wait(".day-row:not([hidden])")]),
  scene("dictation-notice", "Dictation connection notice", dictation("overview"), { problem: true }),
  scene("conversation-notice", "Conversation connection notice", [], { problem: true }),
  scene("onboarding", "First-run welcome", [wait('#ob:not([hidden]) [data-ob="next"]')], { onboarding: true }),
  scene("onboarding-abilities", "First-run ability choices and wide primary action", [click('[data-ob="next"]'), wait('.ob-abilities')], { onboarding: true }),
  scene("onboarding-done", "First-run completion with optional abilities disabled", [click('[data-ob="next"]'), { action: "disable-abilities" }, click('[data-ob="next"]'), wait('.ob-col h2')], { onboarding: true }),
  scene("after-onboarding", "First run opens the home overview with all optional abilities disabled", [click('[data-ob="next"]'), { action: "disable-abilities" }, click('[data-ob="next"]'), click('[data-ob="next"]'), wait(".page-home:not([hidden]) .ho-hero"), check("home-overview")], { onboarding: true }),
];
