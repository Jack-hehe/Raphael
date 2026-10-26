# Team and rooms

## Sub-features

Grouped board, group creation/rename/delete, zoom/drag targets, team mentions,
room creation/group assignment, membership and conversation actions. Room messages route
through bounded, persisted exchanges: explicit mentions/replies, saved automatic/member/everyone
responders, sequential handoffs, up to two material chime-ins and Raphael's wrap-up.
Each exchange has at most six member turns and three handoffs; the final turn is reserved
for a short wrap-up after at least one turn when Raphael participates. Empty and (pass) replies stay hidden.
Explicit mentions use whole names, prefer the longest matching name, ignore email addresses,
and run in the order they occur in the message. The same rule applies to peer handoffs.

## How to get to it (user POV)

Choose Team for the board/creation menu. The padded header puts the title and Add on the same row without an attached member count. Conversation's sidebar lists rooms and a
plus button. At compact width open the member picker, then Rooms. Open a room and
use its header menu for rename/membership/delete. Wide sidebar rows expose the same actions
through a hover/focus more button. New rooms choose 2-6 members including required Raphael,
and a default responder. Their generated name follows selection until the owner edits it.
Raphael cannot be deselected. Legacy six-member rooms without Raphael retain every member
and require owner adjustment before sending. Room turns and exchanges remain outside private
history, preview, search, badges and worker context. Reply chips use server-derived source
authors, including the owner; missing sources display an unavailable label.
Reply on a member message directs the next owner message to that member; the composer
shows the recipient and lets the owner cancel the reply. Stop finishes the current speaker
and discards queued turns. A new owner message takes priority after the current speaker.

Team's mention menu follows the typed @ character, including text before it and
newlines. A mirror copies the field's font, wrapping, padding and scroll offsets;
the menu prefers above the text line and flips below when space is limited. Other
menus retain their existing alignment. Room composers currently have no mention
autocomplete menu.

## Driving it with look

```powershell
npm run look -- team team-group-menu team-rename team-delete team-new-menu team-new-group team-mentions team-empty team-drop-target team-zoom --theme both --width 1120,720,1920x1080
npm run look -- sidebar compact-rooms new-room room-group-menu room room-draft room-menu room-rename room-members room-delete room-hover --theme both --width 1120,720,1920x1080
npm run look -- room-composer-empty room-text room-composer-hover team-composer-empty team-text team-composer-hover --theme both --width 1120,720,1920x1080
npm run look -- room-replies room-discussion room-thinking room-wrapup room-row-menu new-room-responder room-reply-send room-stop --theme both --width 1120,720,1920x1080
npm run look -- room-required-chief room-generated-name room-private-isolation room-reply-owner room-reply-peer room-reply-source-missing --theme both --width 1120,720,1920x1080
```

Keep board/picker/thread/dialog PNGs. Check membership, authors, mention lists,
composer and overlay clearance. Team journeys assert the header padding and aligned Add action. The default team scene also checks R-UI-01: at 1920x1080 the group panels occupy at least 80% of workspace width and 35% of workspace area, remain inside the scrollport, and keep names and roles at a readable measure. The team-mentions scene checks line-start, multiline and inline @ positions, requiring the menu's left edge within 24px of the character at all three sizes. Mention/slash items start at normal row padding and reserve no empty icon/check column. For mutation work extend scenes through create,
send, rename and membership changes; assert UI and mock boundary records. Native
proof needs a harmless room message, actual recipient responses and persistence
after reopening the isolated dev instance. Record recipients, states and cleanup.

## Gotchas

Room 1 has messages; room 2 is a team room. The compact picker is a different entry
point and needs 720 coverage. Synthetic drag scenes do not prove pointer/keyboard
drag accessibility. A confirmation dialog is not proof of deletion or delivery. The row-menu scene uses the
sidebar at wide widths and header actions in the compact picker. Inspect named reply chips,
role tags, Raphael's crown, the single speaking row, wrap-up card and responder menu.
The wrap-up has labelled conclusion, owner-decision and next-step sections; named
next-step owners are emphasized. The global look audit rejects visible raw Markdown
headings, task markers, bold markers and fenced-code delimiters on every page.
The room fixture is synthetic IPC only; core tests use a fake brain and shared worker/CLI
loop with real approval gates. Paid provider discussion, installed CLI execution and native
persistence still require the owner's isolated live acceptance. Restart stops exchanges
without replaying tasks or approvals. The earlier forced moderator loop is not implemented.
