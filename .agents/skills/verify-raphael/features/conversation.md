# Conversation

## Sub-features

Named conversations per member; new/rename/delete; public work/event chips; answers,
attachments, reply quoting, message actions, search and composer menus.
Raphael's single persistent quick-question conversation is created on the first global
voice question, pinned above formal conversations as 快速提問 / Quick questions / クイック質問.
Its fixed title and permanent identity have no rename/delete menu; individual messages
can still be deleted. Opening it and typing continues with the quick model and effort.

## How to get to it (user POV)

From Home, click Raphael in the rail or open a member report. Select Raphael or another member in the sidebar (compact width:
member picker). Raphael stays first with his original large bare emblem and name. The column's top-left arrow toggles a remembered 56px face strip, including at compact width. Other expanded rows retain their 40px faces, time and preview. Click the stacked member/conversation title to toggle details. The title displays
the current session only; conversation creation, search, switching and row menus
live in the details list. The top-right gear opens Settings. Use sidebar search, or
the compact picker's search action, for messages.

## Driving it with look

```powershell
npm run look -- chat running empty details raphael-details details-closed conversation-filter new-conversation row-menu rename delete --theme both --width 1120,720,1920x1080
npm run look -- quick-conversation-list quick-conversation --theme both --width 1120,720,1920x1080
npm run look -- work composer mode actions reply search search-target tooltip slash-commands --theme both --width 1120,720,1920x1080
npm run look -- member-composer-empty member-text member-composer-hover conversation-notice --theme both --width 1120,720,1920x1080
npm run look -- composer-learn member-composer-learn room-composer-learn team-composer-learn --theme both --width 1120,720,1920x1080
```

Keep reports and result PNGs: answer/attachment, expanded work, open menus/dialogs,
quoted draft and highlighted search target. Inspect action-menu placement and
composer clearance at 720. Slash commands use the slash's measured text rectangle, start-aligned above the line with a below-line fallback. The slash-commands scene checks its left edge within 24px of the character. The chat scene also checks 15px/24px message type,
12px bubble radii, a borderless answer and the original Raphael emblem and other members' 40px faces. Owner
messages use the skin accent/on-accent colors; assistant messages use a raised surface. A real send needs a native journey: submit an innocuous
prompt, observe public progress/final reply, navigate away/back, and verify
conversation persistence. Keep the resulting turn and relevant logs.

The four composer-learning scenes seed synthetic dictation through the existing native event
boundary, correct Open Mouse Bot to OpenMausBot, and send through the real composer.
They assert exactly one dictionary.learn call with the complete correction, exactly one
localized notice clear of the composer, and the unchanged single gold-emblem / bare-plane slot before and after send.
Only dictation seeds learning; ordinary typing and clipboard paste do not. Native microphone,
external-editor correction tracking and persistence still require isolated native acceptance.

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
