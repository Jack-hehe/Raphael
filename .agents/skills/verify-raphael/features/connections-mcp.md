# Connections and MCP

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
