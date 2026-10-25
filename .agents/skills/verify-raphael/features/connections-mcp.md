# Connections and MCP

Home reads the private Google primary calendar through connectors.status (without
refreshing Composio accounts) and the enabled calendar_list_events tool. Its bounded
read shares connector scope/tool restrictions; an unavailable calendar does not
remove Home's routines, questions or reports. See [Home](home.md) and the offline
test_home_calendar_uses_only_enabled_private_google_tool connector regression.

## Sub-features

Server list/actions, command/HTTP forms, test results, edit/disconnect, JSON paste
preview, local discovery and empty states.

## How to get to it (user POV)

Open Settings > Connections. Add offers custom commands, pasted JSON and import.
A server's overflow menu contains edit/disconnect. Switch custom form type for
HTTP and test a draft before saving.

## Driving it with look

```powershell
npm run look -- connections connections-hover server-menu connections-add-menu connections-command connections-test connections-url connections-edit connections-delete --theme both --width 1120,720
npm run look -- connections-paste connections-paste-preview connections-discover connections-empty connections-discover-empty --theme both --width 1120,720
```

Keep list/menu/form/status/preview PNGs and reports. Inspect long arguments, access
to Test/status and dialog stacking at 720. For actual connection work use an
authorized isolated native app and local test MCP server: add, test, save/reopen,
invoke a harmless tool, disconnect and verify it is unavailable. Keep sanitized
server logs/state and stop only the server you started.

## Gotchas

Fixture server `notes` supplies edit flows; tests/discovery return synthetic results.
`connections-delete` captures the disconnected server's menu, not native removal.
Browser network is blocked except the owned Vite origin; favicon returns empty 204.
UI success does not prove OAuth, credentials, process launch, HTTP transport, local
config scanning or actual tools.

## Managed connectors core (UI pending)

Composio and Google (private) use connectors.* RPCs documented in CONN-NOTES.md.
This branch adds no dialog or catalog UI. Existing look scenes are regression
proof only. Use connectors.tools for Composio toggles; MCP meta-tools are locked.
Generic integration mutations refuse managed entries. The connectors.changed
notification reaches the existing ConnectionsChanged main event.

Offline verification: npm run verify -- --core --rust includes HTTP doubles,
a real isolated loopback listener driven by a simulated browser, scope/draft policy,
one-use approvals, privacy and Rust bridge tests. For native acceptance use an
isolated data directory and owner-provided test project/client: save a scoped
Composio key, connect by browser/poll, read through the agent gate, disable a tool,
disconnect/revoke; separately configure a Desktop Google client, exercise partial
consent/refresh and draft-only denial, approve an intentional test send or event,
then revoke. Repeat availability for Raphael and a member. Check expiry and
secret-free logs. These live/native steps remain pending in this core branch.
