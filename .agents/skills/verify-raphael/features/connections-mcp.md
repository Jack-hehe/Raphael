# Plugins, connections and MCP

## Sub-features

Settings > Connections contains one installed list for Composio toolkits, private
Google and custom MCP servers. Every row has a bare service mark (or a generic plug),
name, one-line description, service switch and detail chevron. Managed bridge
servers are not duplicate rows. Tool switches are grouped by write/read in the
detail. Disconnect lives in the detail overflow with a second-press confirmation.
Custom MCP add/edit/test, command/HTTP, JSON paste and local discovery stay reachable.

The Store uses connectors.catalog and connectors.categories, shared search/dropdown,
paged cards, browser sign-in progress and already-added state. A missing Composio key
shows only the password prompt and Save action; keys never enter view state or error
copy. Remote logos accept only HTTPS Composio domains under the existing image CSP;
bundled marks/generic plug cover absent artwork. The fixture uses installed marks
and does not prove remote image availability.

## How to get to it (user POV)

Open Settings > Connections. Use Installed to open a service detail, change its
switches or open its overflow. Add offers custom command/URL, pasted JSON and import.
Use Store to save a Composio project key, filter the catalog and Add a service;
complete its login in the browser, then return to Installed.

## Driving it with look

```powershell
npm run look -- plugins-installed plugins-detail plugins-store plugins-store-category plugins-store-key plugins-connecting --theme both --width 1120,720,1920x1080
npm run look -- connections connections-hover server-menu connections-add-menu connections-command connections-test connections-url connections-edit connections-delete --theme both --width 1120,720,1920x1080
npm run look -- connections-paste connections-paste-preview connections-discover connections-empty connections-discover-empty --theme both --width 1120,720,1920x1080
```

Inspect every contact sheet and detail/store/key/menu full-size image, especially
720px scrolling, ellipsized descriptions and overflow confirmation. Browser scenes
exercise the real entry and IPC doubles. Vitest covers asynchronous connection,
catalog races/pagination, key privacy and managed tool decisions. Core tests cover
default-on new tools, persisted service switches, provider failures and the shared
API/CLI MCP definitions for Raphael and members without a restart.

## Gotchas

Composio opens the login in the core and polls there; no frontend OAuth URL or token
is stored. A chat's connector card (see [Conversation](conversation.md)) signs in through these same
`connectors.google.connect` and `connectors.connect` methods, so their lock and value-free failures apply. connectors.changed maps to ConnectionsChanged. Status reads here use
refresh:false to avoid account refresh/notification loops. tools reads can notify;
the controller keeps the in-flight tool state when reloading the list. A whole
service switch preserves individual exclusions via connectors.set_tools.enabled.
Google consent scopes and existing execution gates still apply independently.

Fixture server notes supplies custom edit/test flows. All credentials, tools and
catalog entries in browser evidence are synthetic. Browser network is blocked outside
the owned Vite origin. These scenes cannot prove OAuth, OS credential persistence,
remote logo hosting, provider schemas, account expiry/revocation or actual tools.

## Native acceptance owned by the maintainer

Run the full look matrix, judge with external ChatGPT/Codex reference images and
verify --live in the authorized environment, as reserved in .brief-plugins.md.
Use an isolated data directory and a scoped Composio test key. Save/reopen, search
and change categories, Add through browser sign-in, cancel/deny/expire and retry,
disable a tool/service, verify the next API and CLI turns for Raphael and a member,
and disconnect/revoke. Confirm new tools default on, exclusions survive restart,
remote logos load under native CSP, and logs contain no key/token/payload.

For private Google use an owner-provided Desktop client: configure through the
existing core RPC, sign in, exercise partial consent and refresh, and verify Gmail
and Calendar tool switches and revocation. Home calendar reads only through the
enabled private calendar_list_events tool. From a chat, ask for a nonce mail, connect from its Gmail card,
cancel once, finish once and reconnect once; only that task resumes and reads the mail. Test a custom local MCP server with
add/test/edit/paste/discover, a harmless call and removal; stop only that test server.

## UI audit regression coverage

Custom MCP transport uses the shared dropdown (`data-k="conn-type"`); HTTP
journeys choose the `http` menu item. Custom server deletion asks to delete; managed
connector disconnection asks to disconnect. Security and secret-storage notices remain.
