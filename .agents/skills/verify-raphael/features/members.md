# Members

## Sub-features

Identity/personality, profile editing, avatar selection, memory, routines, skills,
commands and voice settings. Members have independent conversations.

## How to get to it (user POV)

In Conversation select a member, then click the stacked header title for details.
At compact width use the member picker to select the member first. The same title
closes the panel; its close button updates the same remembered session state. Details has the avatar, edit pencil and resource
panes. Team's creation menu is another entry point to member creation; verify the
complete flow for creation changes.

## Driving it with look

```powershell
npm run look -- details member-avatar member-edit details-memory details-routines details-skills details-commands details-voice --theme both --width 1120,720
npm run look -- team-new-menu member-text --theme both --width 1120,720
```

Keep details/resource-pane/avatar-menu/edit-dialog PNGs; check layering, focus and
scroll access. Current scenes open editing but do not save. For creation work extend
shared scenes to create a synthetic member, open its avatar picker while the dialog
is open, save, open its conversation and reopen details. Assert identity/avatar and
selection. Native proof must also relaunch the isolated dev instance and confirm
persistence, then remove test data.

## Gotchas

Fixture member 1 is 小晴 with conversation 11; Raphael has special avatar behavior.
Skills/commands/voices have empty fixture collections. Seed resources before claiming
populated-resource flows. Native avatar picking/cropping, files, audio output and
provider behavior need native evidence. A dialog screenshot does not prove saving.

## Raphael team administration

Raphael can administer the team from his conversation: overview, member creation
and editing, groups, any member's routines, skill imports and command templates.
Management tools, including the legacy create_member alias, are offered only to member 0. Both creation names share the same ten-per-turn limit and member service. Creation uses distinct available
presets, then the neutral silhouette, and the create dialog defaults.

```powershell
npm run look -- admin-approval admin-approval-batch admin-approval-long admin-delete-approval admin-approved admin-denied --theme both --width 1120,720
```

The scenes retain full approval fields, allow long instructions to scroll by
keyboard, keep actions below the details, select the member after approval and
assert that rejection leaves the roster unchanged. Deletion uses the existing T3
held approval even in Direct mode. The boundary fixture does not prove model
selection or persistence: core tests drive API turns and authenticated HTTP MCP
turns through the real member service. Live provider/CLI and native relaunch
checks remain separate.

Ask mode collects the entire turn's reversible member/group changes in one card,
including eight-member requests across multiple model rounds or sequential CLI MCP
calls. Every member lists name, role, blurb and group. Approval applies the entire
transaction; rejection or expiry applies none. An invalid operation rolls everything
back without publishing partial roster changes. Deletions retain per-member T3 cards.
Chinese and Japanese labels use full-width colons; English uses colon-space.
The admin-approval-batch scene checks all eight entries, internal keyboard scrolling
and the one pair of actions in both themes and widths.
