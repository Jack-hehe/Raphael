# Plugins, connections and MCP

## Sub-features

Settings > Connections contains one connected view (the tab 已連結) for Composio
toolkits, private Google, custom MCP servers and the members' private skills. Every
row has a bare service mark (or a generic plug, or the member's face for a skill),
name, one-line description, switch and detail chevron. Managed bridge
servers are not duplicate rows. Tool switches are grouped by write/read in the
detail. Disconnect lives in the detail overflow with a second-press confirmation.
Custom MCP add/edit/test, command/HTTP, JSON paste and local discovery stay reachable.

The rows sit under three headings: 應用程式 (their tools can be called now), 尚未連結
(waiting for sign-in, failed, a duplicate of another connection, all tools off or
unreadable, each with its reason) and 私人技能. An app is connected only when its tools
can be called now, and the core defines that once (`integrations/inventory.py`); the
sidebar's 連結應用程式 entry (a badge on its icon in the rail and the narrow sidebar), the tab's number, the rows
under 應用程式 and the roster every model is given (`TeamContextSnapshot.connected`)
all show that one count. An unreadable inventory shows 「—」 instead of a number (the
entry's accessible name says it cannot be read), and an app whose state cannot be read
says 「無法讀取」 on its row.
An app's detail shows its account(s) by status, `已開啟 N/M 個工具` with a switch per
tool, signing in again and, in the overflow, disconnect. A private skill's page shows
its name, who has it, its description (when it is used), a switch, the whole
SKILL.md as editable text and 刪除技能. Sharing an app or a skill is not offered
until members can share across users (P13).

The Store uses connectors.catalog and connectors.categories, shared search/dropdown,
paged cards, browser sign-in progress and already-added state. A missing Composio key
shows only the password prompt and Save action. Non-empty keys are probed without a local
prefix rule. Errors distinguish an OpenRouter key, Composio refusal and network/timeout
failure, with generic copy otherwise; keys never enter view state or error
copy. Remote logos accept only HTTPS Composio domains under the existing image CSP;
bundled marks/generic plug cover absent artwork. The fixture uses installed marks
and does not prove remote image availability.

## How to get to it (user POV)

Press 連結應用程式 at the bottom of the sidebar (the plug with a count at the bottom
of the rail), or open Settings > Connections. Use 已連結 to open a service
detail or a private skill, change their switches or open an overflow. Add offers custom command/URL, pasted JSON and import.
Use Store to save a Composio project key, filter the catalog and Add a service;
complete its login in the browser, then return to 已連結.

## Driving it with look

```powershell
npm run look -- plugins-installed plugins-detail plugins-store plugins-store-category plugins-store-key plugins-store-key-openrouter plugins-connecting --theme both --width 1120,720,1920x1080
npm run look -- connections connections-hover server-menu connections-add-menu connections-command connections-test connections-url connections-edit connections-delete --theme both --width 1120,720,1920x1080
npm run look -- connections-paste connections-paste-preview connections-discover connections-empty connections-discover-empty --theme both --width 1120,720,1920x1080
npm run look -- connect-apps-categories connected-apps-skills connected-apps-unreadable connections-app-switch connector-many-tools connector-account-tools connector-uninstalled skill-private-detail --theme both --width 1120,720,1920x1080
```

The last command is the apps and skills set (`scenes/apps.mjs`). Each scene enters
through the sidebar's 連結應用程式; `apps-count` compares the badge, the tab's
number, the rows and the roster, `apps-unreadable` checks that a core that cannot
list the apps leaves 「—」 and no number in all of them. An app has one switch, in
its row and its dialog (owner, 2026-10-27: 「一次全部開啟或一次關掉」, never tool by
tool): on is the app and every tool on, mixed is on with some tools off, and a press
on mixed or off turns every tool on, a press on all-on turns the app off.
`app-switch-all` drives GitHub with 18 of 46 tools off through mixed → all on → off →
all on and fails if any tool was switched one by one. `connector-folded` checks that a
dialog shows one folded N/M line, no switch per tool and no scroll; `connector-tools`
unfolds it and checks N/M against the switches and that the actions sit flush at the
dialog's edge, and
`skill-page` checks the action order and that the document is whole and starts at its first line. Also run `home` (the sidebar entry on the wide
and compact layouts) and the `plugins-*` scenes above for regressions.

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

The count is the core's, never the page's: `integrations.inventory` returns it with each
app's state, `connections()` carries the same states, and a change of either notifies
`integrations.changed` (a failure that appears or recovers, a connection added or
removed, a switch). A tool switched off or an app disconnected leaves the offered tools of both
brains at once and the gate refuses it too, also when the owner had said 一律允許 (the
narrow rule `tool:<key>` never revives it; the rule itself stays listed in Settings).
The account is shown by status only (「帳號 N」, 「你的 Google 帳號」, 「已登入」) because the core
keeps no name or address for one. A skill's trigger is its description plus its switch;
deleting it, or turning it off, removes it from the prompt and from `skill_read`.
`members.skills.read` is in the shell's method allowlist (`commands.rs`), covered by its
unit test and compiled only in CI. Fixture connections and skills are synthetic; the
IPC double computes the inventory from them as the core does, so a scene cannot prove
that the core's real states (a provider's account status, a failing server) map the same way.

A chat's secret card (see [Conversation](conversation.md)) fills a secret a connection already declares: it writes the same
vault slot `integrations.add` and `integrations.update` do (`<server>:header:<name>` or `<server>:env:<name>`), never any
other name, so Composio's project key, Google's client and the model keys are out of its reach.

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

Count the connected apps by hand against the sidebar: connect one, let a sign-in expire, add
the same service twice and uninstall one, and watch the number follow each time; ask a CLI
and an API brain how many apps are connected. Switch a tool off, start a new turn and a
running one, and confirm neither brain offers or runs it; do the same after uninstalling,
once with 一律允許 chosen earlier. Edit a private skill's document, ask for what its
description names and see it used; delete it and see that it is not.

## UI audit regression coverage

Custom MCP transport uses the shared dropdown (`data-k="conn-type"`); HTTP
journeys choose the `http` menu item. Custom server deletion asks to delete; managed
connector disconnection asks to disconnect. Security and secret-storage notices remain.

## Right click on a connection (R-UI-44)

A custom server's ⋯ sits in the header of its dialog, and a subscription account's ⋯ on its card in Settings › 模型; a right click anywhere on the header or the card
opens that menu at the pointer (測試, 編輯伺服器, 刪除; 中斷連線), the one the ⋯ opens. See [Members](members.md) for the rule and its table.

```powershell
npm run look -- context-menu-connection context-menu-cli-account --theme both --width 1120,720,1920x1080
```
