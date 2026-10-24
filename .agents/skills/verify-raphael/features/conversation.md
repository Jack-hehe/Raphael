# Conversation

## Sub-features

Named conversations per member; new/rename/delete; public work/event chips; answers,
attachments, reply quoting, message actions, search and composer menus.

## How to get to it (user POV)

Open Conversation. Select Raphael or another member in the sidebar (compact width:
member picker). The conversation title opens its picker. Select the current member
again to open details; conversation row menus live there. Use sidebar search, or
the compact picker's search action, for messages.

## Driving it with look

```powershell
npm run look -- chat running empty picker no-conversations new-conversation row-menu rename delete --theme both --width 1120,720
npm run look -- work composer mode actions reply search search-target tooltip --theme both --width 1120,720
npm run look -- member-composer-empty member-text member-composer-hover conversation-notice --theme both --width 1120,720
```

Keep reports and result PNGs: answer/attachment, expanded work, open menus/dialogs,
quoted draft and highlighted search target. Inspect action-menu placement and
composer clearance at 720. A real send needs a native journey: submit an innocuous
prompt, observe public progress/final reply, navigate away/back, and verify
conversation persistence. Keep the resulting turn and relevant logs.

## Gotchas

Collapsed work contains hidden `.chat-message` elements before the visible reply.
Answer scenes target `.chat-message[data-expandable]`; the driver chooses a visible
match. The header picker deliberately has no row actions. Rename/delete scenes open
dialogs, not committed changes. Empty/running are fixture variants, not proof of
streaming, cancellation, persistence or file transfer.
