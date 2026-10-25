# Conversation

## Sub-features

Named conversations per member; new/rename/delete; public work/event chips; answers,
attachments, reply quoting, message actions, search and composer menus.

## How to get to it (user POV)

From Home, click Raphael in the rail or open a member report. Select Raphael or another member in the sidebar (compact width:
member picker). Raphael stays pinned first as an ordinary row with the same 40px
face, name/role/time line and preview as the other members. Click the stacked member/conversation title to toggle details. The title displays
the current session only; conversation creation, search, switching and row menus
live in the details list. The top-right gear opens Settings. Use sidebar search, or
the compact picker's search action, for messages.

## Driving it with look

```powershell
npm run look -- chat running empty details raphael-details details-closed conversation-filter new-conversation row-menu rename delete --theme both --width 1120,720
npm run look -- work composer mode actions reply search search-target tooltip --theme both --width 1120,720
npm run look -- member-composer-empty member-text member-composer-hover conversation-notice --theme both --width 1120,720
```

Keep reports and result PNGs: answer/attachment, expanded work, open menus/dialogs,
quoted draft and highlighted search target. Inspect action-menu placement and
composer clearance at 720. The chat scene also checks 15px/24px message type,
12px bubble radii, a borderless answer and the pinned 40px member faces. Owner
messages use the skin accent/on-accent colors; assistant messages use a raised surface. A real send needs a native journey: submit an innocuous
prompt, observe public progress/final reply, navigate away/back, and verify
conversation persistence. Keep the resulting turn and relevant logs.

## Gotchas

Collapsed work contains hidden `.chat-message` elements before the visible reply.
Answer scenes target `.chat-message[data-expandable]`; the driver chooses a visible
match. The title reflects the details panel with aria-expanded; opening rooms preserves
the remembered member-panel preference. Rename/delete scenes open
dialogs, not committed changes. Empty/running are fixture variants, not proof of
streaming, cancellation, persistence or file transfer.

## Team administration and progress

Team administration approvals are covered by the member feature's `admin-*`
scenes. Public progress parses complete reply envelopes and drops malformed or
partial JSON; the exact CLI regression is exercised by core tests. The shared
rendered audit also rejects non-finite durations in work summaries.
