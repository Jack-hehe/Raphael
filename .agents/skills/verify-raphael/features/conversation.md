# Conversation

## Sub-features

Named conversations per member; new/rename/delete; public work/event chips; answers,
attachments, reply quoting, message actions, search and composer menus.
Private history, previews, search and unread state exclude room and inter-member exchange
audit rows. Room answers stay in their room/thread and have no shortcut into private history.
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
npm run look -- composer-task-stop chat running empty details raphael-details details-closed conversation-filter new-conversation row-menu rename delete --theme both --width 1120,720,1920x1080
npm run look -- quick-conversation-list quick-conversation --theme both --width 1120,720,1920x1080
npm run look -- live-scenario-owner-view --theme both --width 1120,720,1920x1080
npm run look -- work composer mode actions reply search search-target tooltip slash-commands --theme both --width 1120,720,1920x1080
npm run look -- member-composer-empty member-text member-composer-hover conversation-notice --theme both --width 1120,720,1920x1080
npm run look -- composer-learn member-composer-learn room-composer-learn team-composer-learn --theme both --width 1120,720,1920x1080
npm run look -- plain-bot-chat centred-system-events --theme both --width 1120,720,1920x1080
```

Keep reports and result PNGs: answer/attachment, expanded work, open menus/dialogs,
quoted draft and highlighted search target. Inspect action-menu placement and
composer clearance at 720. Slash commands use the slash's measured text rectangle, start-aligned above the line with a below-line fallback. The slash-commands scene checks its left edge within 24px of the character. The chat scene also checks 15px/24px message type,
12px bubble radii, a borderless answer and the original Raphael emblem and other members' 40px faces. Owner
messages use the skin accent/on-accent colors; assistant messages use a raised surface. Every thread reads plainly
(R-UI-42): the core stores Raphael's replies and progress as his 《prefix。…》 line, split over messages when long,
and the window shows, copies and quotes them without it; his in-app questions are plain too, while the overlay keeps
the persona. `chat`, `running` and `plain-bot-chat` reject a visible prefix or stray closing mark. Work is one muted
line above the answer and the day is a plain centred word. `centred-system-events` shows a hand-off, a consultation and
a meeting between the question and the answer, each one centred grey line. A real send needs a native journey: submit an innocuous
prompt, observe public progress/final reply, navigate away/back, and verify
conversation persistence. Keep the resulting turn and relevant logs.

The four composer-learning scenes seed synthetic dictation through the existing native event
boundary, correct Open Mouse Bot to OpenMausBot, and send through the real composer.
They assert exactly one dictionary.learn call with the complete correction, exactly one
localized notice clear of the composer, and the unchanged single gold-emblem / bare-plane slot before and after send.
Only dictation seeds learning; ordinary typing and clipboard paste do not. Native microphone,
external-editor correction tracking and persistence still require isolated native acceptance.

## Gotchas

Typed requests run independently per request id, including different conversations of
one member, while the voice slot owns microphone and overlay priority. A queued typed
YES/NO card opens in its conversation immediately; either the card or overlay can answer
first, and the question follows foreground focus once voice releases the overlay.
The existing `admin-approval` look scene now opens a second member question through the
native event boundary, visits that member, then returns to Raphael and requires his original
approval card. Window cards retain independent ids across members and conversations.

Typed progress never goes to the overlay. See `DICTATION-NOTES.md` for native concurrency,
Esc, spoken-question and paste-last acceptance; browser fixtures cannot prove these.

Collapsed work contains hidden `.chat-message` elements before the visible reply.
Answer scenes target `.chat-message[data-expandable]`; the driver chooses a visible
match. The title reflects the details panel with aria-expanded; opening rooms preserves
the remembered member-panel preference. Rename/delete scenes open
dialogs, not committed changes. Empty/running are fixture variants, not proof of
streaming, cancellation, persistence or file transfer.

Delete confirmations use a compact 480px panel with a fine theme border, a top-right
close control, an announced irreversible-action description and pill actions aligned
right: Cancel then theme-accent Delete. Cancel retains initial focus. The shared
`delete-confirmation` rendered check covers conversation, group, room and routine
deletion, including a confirmation stacked over the routine editor.

## Team administration and progress

Team administration approvals are covered by the member feature's `admin-*`
scenes. Public progress parses complete reply envelopes and drops malformed or
partial JSON; the exact CLI regression is exercised by core tests. The shared
rendered audit also rejects non-finite durations in work summaries.

## Guardian scenario owner view

The live-scenario-owner-view scene replays S17 synthetic history through the shared IPC fixture. It checks the personal conversation, actionable Traditional Chinese failure, and separation of progress from the final answer. This rendered fixture does not prove live model or connector acceptance. Core scenario reports retain digests, scopes, message kinds and test-mode receipts in owner-view.json.

## Meetings and shared inventory

Durable assignments use `handoff`, which returns a task receipt immediately. The requesting
conversation displays one centred grey line (the assignee's face, 已轉交給/已詢問 and the name, then the
state) whose detail shows the request, the result and the produced files, never task numbers. Work runs
in the assignee's isolated work turn; results return only to the requesting source. Quick
`ask_member` consultation remains tool data. Source-document deadlines never schedule work.
The room handoff scenes in [Team and rooms](team-and-rooms.md) cover the shared event renderer;
private and room result destinations are also checked by offline core tests.

Every private/API/CLI/member turn receives the current public TeamContextSnapshot with self,
count, stable IDs, roles, primary/chief and secret-free app connection states. Meeting requests
use room_meeting; one centred grey line with the group icon, outside Raphael's message, opens the room, and quick ask_member answers remain tool
data folded into the requesting assistant reply. Room history remains outside private turns.

```powershell
npm run look -- meeting-open-room meeting-chief-event member-mentions-bots-apps --theme both --width 1120,720,1920x1080
```


## P04 task lifecycle

Background narration uses the typed worker.progress status channel and is never appended to
agent_messages. Legacy stored progress remains readable. Terminal replies are separately validated
and published atomically with the task verdict and result outbox. Private Stop calls
agent.history.stop and preserves completed history and tool evidence; ack, running and owner-question
waiting all retain Stop in the existing gold slot. Microphone release never follows task Stop.
The shared composer tests cover IME Enter, Stop acknowledgement races, independent send completion
and the return to the voice emblem/tooltip at idle. Native microphone/controller behavior remains
unverified by rendered fixtures.
