# Members

## Sub-features

Identity/personality, profile editing, avatar selection, memory, routines, skills,
commands and voice settings. Members have independent conversations.

## How to get to it (user POV)

In Conversation select a member, then select it again for details. At compact width
use the member picker both times. Details has the avatar, edit pencil and resource
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
