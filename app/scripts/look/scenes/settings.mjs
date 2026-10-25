import { scene, click, hover, type, wait, page, settings, scroll, key, check } from "./steps.mjs";

const connections = settings("connections");
const add = [...connections, click('[data-k="conn-add"]')];
const custom = [...add, click('.menu [data-value="custom"]')];
const usage = [...settings("models"), click('[data-models-tab="usage"]'), wait(".run-tile"), check("usage-summary")];
const dictation = tab => [page("dictation"), click(`[data-dictation-tab="${tab}"]`), check("dictation-navigation")];
export default [
  ...["general", "appearance", "connections", "models", "dictation", "agent", "messaging", "data", "about"].map(pane => scene(pane === "general" ? "profile" : pane, `${pane} settings pane`, settings(pane))),
  scene("skins", "Skin preview hover", [...settings("appearance"), hover('[data-skin-choice="paper"]')]),
  ...["default", "paper", "graphite", "lagoon", "dusk", "midnight", "meadow", "ink", "catppuccin"].map(skin => scene(`skin-${skin}`, `${skin} appearance previews`, [...settings("appearance"), click(`[data-skin-choice="${skin}"]`)])),
  scene("connections-hover", "MCP server hover", [...connections, hover('[data-conn="server-menu"]')]),
  scene("server-menu", "MCP server actions", [...connections, click('[data-conn="server-menu"][data-id="notes"]')]),
  scene("connections-add-menu", "Add a connection menu", add),
  scene("connections-command", "Command MCP form", custom),
  scene("connections-test", "MCP test result", [...custom, type("#conn-name", "專案筆記"), type("#conn-command", "npx"), type("#conn-args", "-y\nnotes-server\nC:/Work/Team notes"), click('[data-conn="test-draft"]'), scroll(".add-grid [role=status]")]),
  scene("connections-url", "HTTP MCP form", [...custom, click('[data-conn="form-type"][data-type="http"]')]),
  scene("connections-edit", "Edit MCP server", [...connections, click('[data-conn="server-menu"][data-id="notes"]'), click('[data-conn="edit-server"]')]),
  scene("connections-delete", "Disconnected MCP server menu", [...connections, click('[data-conn="server-menu"][data-id="notes"]'), click('[data-conn="disconnect"]'), click('[data-conn="server-menu"][data-id="notes"]')]),
  scene("connections-paste", "Paste MCP JSON", [...add, click('.menu [data-value="paste"]')]),
  scene("connections-paste-preview", "Preview MCP JSON", [...add, click('.menu [data-value="paste"]'), type("[data-mcp-json]", '{"mcpServers":{"專案筆記":{"command":"npx","args":["notes-server"]}}}'), click('[data-dlg="preview"]')]),
  scene("connections-discover", "Discover local MCP definitions", [...add, click('.menu [data-value="import"]')]),
  scene("connections-empty", "Connections without servers", connections, { servers: [] }),
  scene("connections-discover-empty", "Discovery without candidates", [...add, click('.menu [data-value="import"]')], { servers: [], candidates: [] }),
  scene("usage", "Usage summary and grouping", usage),
  scene("usage-members", "Expanded member usage", [...usage, click('[data-run-act="members"]'), scroll(".run-members")]),
  scene("usage-period", "Usage period menu", [...usage, click('[data-run-act="period"]')]),
  ...["day", "provider", "routine"].map(group => scene(`usage-group-${group}`, `Usage grouped by ${group}`, [...usage, click(`[data-run-group="${group}"]`), scroll(".run-groups")])),
  scene("usage-allowances", "Allowances for Claude, Codex and Gemini CLIs", [...usage, scroll(".run-allowance")]),
  scene("usage-empty", "Usage without runs", usage, { usageEmpty: true }),
  ...["overview", "history", "dictionary"].map(tab => scene(`dictation-${tab}`, `Dictation ${tab}`, dictation(tab))),
  scene("dictation-keyboard", "Vertical dictation navigation with keyboard focus", [...dictation("overview"), key("ArrowDown"), wait('[data-dictation-tab="history"][aria-selected="true"]'), key("End"), wait('[data-dictation-tab="dictionary"][aria-selected="true"]'), check("dictation-navigation")]),
  scene("dictation-history-day", "History filtered through the heatmap", [...dictation("overview"), click('.heat [data-date="2026-10-24"]'), wait(".day-row:not([hidden])")]),
  scene("dictation-notice", "Dictation connection notice", dictation("overview"), { problem: true }),
  scene("conversation-notice", "Conversation connection notice", [], { problem: true }),
  scene("onboarding", "First-run welcome", [wait('#ob:not([hidden]) [data-ob="next"]')], { onboarding: true }),
  scene("after-onboarding", "First run opens the home overview with all optional abilities disabled", [click('[data-ob="next"]'), { action: "disable-abilities" }, click('[data-ob="next"]'), click('[data-ob="next"]'), wait(".page-home:not([hidden]) .ho-raphael"), check("home-overview")], { onboarding: true }),
];
