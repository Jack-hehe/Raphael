# Team and rooms

## Sub-features

Grouped board, group creation/rename/delete, zoom/drag targets, team mentions,
room creation/group assignment, membership and conversation actions. Room messages route
through bounded, persisted exchanges: explicit mentions/replies, saved automatic/member/everyone
responders, sequential handoffs, up to two material chime-ins and Raphael's wrap-up.
Explicit addresses are durable source/recipient reply intents. Six-turn admission slices and
three-hop limits bound optional chime-ins only; pending replies continue in scheduled slices
before Raphael concludes. Stop, revoked membership, cost limits and uncertain restart work
retain named blocked intents. A member reply that fails (for example a CLI still rate-limited
after its retries) blocks only that reply with its named reason; the exchange goes on and
Raphael's wrap-up names the unfinished reply. A blocked reply is a centred line in the thread after its exchange's last
message, never a note above the composer, and names why in the window language, CLI rate limits, quota, sign-in, busy
and errors included. Completed sources recover once without replaying work.
A meeting is a room_meeting agenda or the owner's first message after the room's parts are saved; later owner messages
are answered as usual. The moderator always opens round one: the @ addresses in a meeting's own message (an agenda's
「@拉斐爾 作最後結論」) are not calls to speak first. Room-only moderator/pro/con roles run two rounds: each turn is told its round and who still has to speak, an addressed
member takes their planned place in the open round, the moderator closes after the second, and Raphael called on then
gives the single conclusion. During a moderated meeting no one hands off work, and no hand-off ever goes back to a
colleague waiting upstream on it. The room keeps its parts afterwards, but the owner's next message is no meeting:
Raphael hands each of the meeting's items to its single owner, all in one turn, and every item starts at once
(Guardian S08.meeting-actions). Empty and (pass) replies stay hidden.
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
history, preview, search, badges and worker context. A room's turn and work one colleague hands another read the
team's shared notes, never a member's own notes or private chat; Guardian S13 checks both directions live. Reply chips use server-derived source
authors, including the owner; missing sources display an unavailable label.
Reply on a member message directs the next owner message to that member; the composer
shows the recipient and lets the owner cancel the reply. Stop immediately cancels the task tree and blocks queued recipients. Completed actions remain; a deterministic chief summary closes the stopped generation. A new owner message immediately invalidates the previous task generation and blocks its queued reply intents; completed actions are retained.

Team's mention menu follows the typed @ character, including text before it and
newlines. A mirror copies the field's font, wrapping, padding and scroll offsets;
the menu prefers above the text line and flips below when space is limited. Other
menus retain their existing alignment. Room and Team composers share the Bots/apps inventory from TeamContextSnapshot,
including connection states. The Team plus menu opens member creation or a room picker with
selected chips. Raphael uses room_meeting for discussions, reuses exact membership, posts an
agenda and leaves a centered private event whose button opens the real room pane.

## Driving it with look

```powershell
npm run look -- team team-group-menu team-rename team-delete team-new-menu team-new-group team-mentions team-empty team-drop-target team-zoom --theme both --width 1120,720,1920x1080
npm run look -- sidebar compact-rooms new-room room-group-menu room room-draft room-menu room-rename room-members room-delete room-hover --theme both --width 1120,720,1920x1080
npm run look -- room-composer-empty room-text room-composer-hover team-composer-empty team-text team-composer-hover --theme both --width 1120,720,1920x1080
npm run look -- room-replies room-discussion room-thinking room-wrapup room-row-menu new-room-responder room-reply-send room-stop --theme both --width 1120,720,1920x1080
npm run look -- room-required-chief room-generated-name room-private-isolation room-reply-owner room-reply-peer room-reply-source-missing --theme both --width 1120,720,1920x1080
npm run look -- team-roster-aware mentions-bots-apps member-mentions-bots-apps plus-create-room room-mentions room-routing-fallback meeting-open-room meeting-chief-event room-role-assignment room-round-typing room-mention-after-hop-limit room-mention-after-turn-limit room-mention-blocked room-wrapup-after-replies --theme both --width 1120,720,1920x1080
npm run look -- primary-room-chief --theme both --width 1120,720,1920x1080
```

The owner's primary contact (see [Members](members.md)) is not the chief of staff. Raphael is in every room, wears the crown and writes each exchange's one wrap-up
whoever the primary is, and he can never be taken out of a room; choosing another primary or hiding or deleting the primary changes no room, task or exchange.
`primary-room-chief` opens a room with 小晴 as the primary (she takes the top display slot while Raphael keeps his emblem in her former slot) and checks that the header names every member with the crown on
Raphael alone and that the room ends with his one wrap-up. Offline proof: core/tests/members/test_primary.py runs the same exchange with the primary chosen before the owner
speaks, while the chief frames it, after the first reply, while the wrap-up runs and back to Raphael, and every one gives the same turns and one conclusion; Guardian S07.meeting
is held with HiKari as the primary and requires every room turn to carry that roster.

Keep board/picker/thread/dialog PNGs. Check membership, authors, mention lists,
composer and overlay clearance. Team journeys assert the header padding and aligned Add action. The default team scene also checks R-UI-01: group panels size to their own members without stretching to a taller sibling. At 1920x1080 their columns span at least 80% of workspace width; there is no minimum panel area. Tall rosters remain reachable by panning, and names and roles keep a readable measure. The team-mentions scene checks line-start, multiline and inline @ positions, requiring the menu's left edge within 24px of the character at all three sizes. Mention items lead with the member's face, the app's connection mark or the team icon in one aligned column, with an app's state quiet on the right; slash items start at the row padding; neither reserves an empty icon/check column. For mutation work extend scenes through create,
send, rename and membership changes; assert UI and mock boundary records. Native
proof needs a harmless room message, actual recipient responses and persistence
after reopening the isolated dev instance. Record recipients, states and cleanup.

## Gotchas

Durable work uses the shared `handoff` contract, independently of conversational @ reply
intents. Its receipt creates an isolated assignee task immediately, with an explicit stage,
model, source thread and schedule provenance. Source dates never defer work. A parent that
waits releases the worker slot. Safe queued work and waiting continuations survive restart;
uncertain running effects end as interrupted and are not replayed. Room results are delivered
once through the persisted outbox/inbox. Removed recipients have named refusals or withheld
results. Promises without action evidence cannot complete work.

```powershell
npm run look -- room-handoff-queued room-handoff-running room-handoff-result room-handoff-failed room-immediate-work room-source-dates-not-schedule handoff-centred-line room-thinking --theme both --width 1120,720,1920x1080
```

Inspect the compact centred face/name/state line, the expanded request/result/file detail without task numbers
and the separate final member message. The request is the delegating member's own message of the source turn. The `room-thinking` fixture uses actual active-turn/reply-intent state.
`handoff-centred-line` covers the requesting private conversation even without public messages;
the six room handoff scenes cover room dispatch and results.
Offline P00 S03.immediate-start and S08.immediate-start exercise API/CLI dispatch; they do not
establish full Gmail/meeting workflow or live provider acceptance. Core handoff tests additionally
write and hash an actual HTML artifact through the shared gate/tool pipeline.

Room 1 has messages; room 2 is a team room. The compact picker is a different entry
point and needs 720 coverage. Synthetic drag scenes do not prove pointer/keyboard
drag accessibility. A confirmation dialog is not proof of deletion or delivery. The row-menu scene uses the
sidebar at wide widths and header actions in the compact picker. Inspect named reply chips,
Raphael's crown, the header faces' name and role tooltips, the single speaking row, the wrap-up and the responder menu.
A message shows face, name and reply chip only: roles stay in the member column and the header. The wrap-up is
Raphael's ordinary message with face and name, no card, whose conclusion, owner-decision and next-step parts are
light headings; named next-step owners are emphasized. The global look audit rejects visible raw Markdown
headings, task markers, bold markers and fenced-code delimiters on every page.
The room fixture is synthetic IPC only; core tests use a fake brain and shared worker/CLI
loop with real approval gates. Paid provider discussion, installed CLI execution and native
persistence still require the owner's isolated live acceptance. Restart settles completed sources and resumes queued replies; uncertain active work remains
blocked for reconciliation, without replaying tasks or approvals. Fake-brain S02/S07.meeting tests cover
production API/CLI paths, worker protocol and authenticated MCP; rendered fixtures do not prove
live role reasoning or provider acceptance.

## UI audit regression coverage

Team installs the same custom composer tooltip as member/room conversations and
hides it on navigation. Every composer-hover scene waits for the visible tooltip.

## P04 evidence, final messages and Stop

Stages have one durable owner; completion requires executor evidence and required artifact bytes.
Failed acceptance has a named blocked/incomplete stage verdict. Progress is a typed status event,
never persisted as a message. Terminal publication and its task/outbox event share one transaction.
The chief waits for accepted replies and child work, then concludes once. Stopped/blocked exchanges receive an atomic deterministic summary after all work is terminal, without another paid call. This includes short
discussions with zero deliverables. Its first line says the work was stopped only after the owner's Stop
or a new owner message; otherwise it says the discussion did not fully finish and names each unfinished
reply with its reason (rate limit, quota, login, budget, error). New instructions invalidate prior task generations.
Room, private and Team composers show Stop in the existing gold slot through acknowledgement,
running and waiting; task Stop and microphone Stop keep distinct lifecycles.

```powershell
npm run look -- room-workflow-blocked room-final-owner composer-task-stop room-progress-status-only room-no-dangling-mention room-short-wrapup room-stop room-wrapup running --theme both --width 1120,720,1920x1080
```

Inspect every sheet and full-size Stop, blocked and short-wrap-up image. These scenes use IPC
fixtures; P00 S07.short-wrapup, S08.completion and S11 use production loops with offline API/CLI
brains. Native controller/microphone behavior and paid providers require separate acceptance.

## Right click on a room or a group (R-UI-44)

A right click on a room's row in the sidebar, on the header of an open room, on a group's header in the member column or on a group's header on the team board opens the
menu of its ⋯ at the pointer, the same one the ⋯ opens (rename, members, delete, and the rest). The rule and its table are in [Members](members.md); the scenes above show
those ⋯ and the audit of every look scene fails one that no row reaches.

## Team canvas and member actions (P15b1)

```powershell
npm run look -- team team-panned team-rearranged team-member-menu team-drop-target team-fit team-zoom team-new-menu team-group-menu team-composer-empty --theme both --width 1120,720,1920x1080
```

The board pans with a right or middle drag anywhere except an action, or a left drag on empty canvas.
Wheel movement pans; Ctrl+wheel zooms at the pointer, within 50-150%. Minus/plus zoom about the
viewport center. Opening starts at 100% and reopening retains the zoom chosen in this page session; opening only pans to reveal the top card. The monitor icon alone fits the chief and all groups in view within the same zoom range.
If a layout is taller than the viewport even at 50%, fit starts at the top instead of clipping the chief. Opening or fitting the board brings the entire chief card into the canvas with a 16px inset; overflowing groups remain reachable by panning. Reset restores the automatic layout.
The primary occupies the top card above the automatic groups; Raphael exchanges into its original group slot and remains the room chief. The geometry class `team-chief` names the top slot, not a new coordinator identity. Drag a group's header to move that panel independently.
Device-local positions are keyed by group id in `raphael.team.positions`; missing, invalid or inaccessible
localStorage falls back to automatic layout/in-memory placement. The Add menu's Reset group layout
clears all manual positions and restores the initial pan/zoom. Manual groups take priority; automatic groups
flow around them. Each drag resolves to the nearest free position with a 16px gap, including the chief as
an obstacle. Loading, resizing and zooming resolve conflicting saved positions without rewriting the
saved preference. Drop previews also trigger layout so growing groups do not cover their neighbours.

Within each group, member cards fill the available width in up to three columns, then wrap to another row. A one-member group retains a single card width; groups keep their own content height. Drop previews update this width capacity before resolving collisions, then restore it on leave/end. At 1920 the one/eight-member fixture uses one and three columns; at 720 its large group uses two.

Every ordinary member card, including a primary in the top slot, has the column's shared overflow menu (pin, move, primary, unread,
read all, edit, duplicate, clear, hide, delete), including the same submenus, confirmations and RPCs.
Raphael keeps the column's protected chief behavior: conversation action and double click, no destructive
member menu. Cards show waiting/unread dots and `member.model`, falling back to the answer model. `modelPickerSelection` supplies the same catalog/CLI display name and subscription source as the model picker button; neither text nor tooltip exposes a raw CLI ref or effort suffix. Cached CLI metadata remains usable after logout.
A card double click opens its private conversation. During a cross-group drag the destination is outlined
and tinted, with an inert preview of the arriving member at the end of its list; leaving or ending the drag
clears it. Dropping updates group membership, not ordering inside a group.

The panned and rearranged scenes use real pointer drags; member-menu right-clicks the card and checks
menu identity/position with the shared R-UI-44 assertion. Drop highlighting uses the existing synthetic
HTML drag boundary. Fit checks every visible group and the chief against the canvas bounds.
The `team-no-overlap` rendered check compares every visible group rectangle and the chief card in
team, panned, rearranged, member-menu, drop-target, fit and zoom scenes. `team-canvas.test.ts` covers
nearest free placement, multiple obstacles, automatic flow, saved collisions, resize/zoom and growing previews.
Inspect every contact sheet and full-size image, especially the 720px composer and zoom controls.
DOM tests in `pages/team.test.ts` cover pointer/wheel math, persistence/reset, unavailable storage,
menu equality and each action's effect, confirmation cancellation, drop cleanup and double-click navigation.
Browser fixtures prove rendered interactions and localStorage writes, not native relaunch or provider calls.

## Content-sized team panels (owner screenshot, 2026-10-27)

The seven canvas scenes above use `fixture.json`'s `teamBoard` roster through the shared mock IPC variant: 學習 has 紫苑 alone, 未分組 has eight members, and the answer/member refs include `cli:codex:gpt-6.1-sol?effort=high` and `cli:claude:haiku`. `team-member-columns` checks available column count (at most three), first-row filling, one-member width and Raphael role consistency. `team-content-size` compares each panel's own content, margins and padding with its height, and rejects raw CLI labels. `team-chief-visible` checks the whole chief card on opening and after fit. `team-no-overlap` remains active after every canvas interaction. Inspect every contact sheet and all 720/1920 PNGs: one-person panels stay short, CLI names include their source, the top card is not cut on opening/fit, and composer/zoom remain reachable. At the 50% floor a tall roster can extend below the canvas; fit does not claim every group fits in that case.

## Primary display exchange (owner, 2026-10-27)

`primary-team-board` uses the one/eight-member CLI fixture with 紫苑 as primary: her card takes the top slot, and Raphael takes her place in 學習 with his emblem, localized chief-of-staff role and protected actions. It checks the exchanged IDs, 100% opening zoom, wrapped content-sized panels, no overlap and top-card visibility, without pressing Fit before capture. `team-fit` independently checks fitting; `team-zoom` checks retained zoom after reopening. `primary-team-mentions` checks member ordering while keeping everyone/groups/apps and mention values unchanged. `primary-room-picker`, `primary-room-members` and `primary-room-chief` cover display order with identity 0 still required/crowned and authoring the single wrap-up. See [Members](members.md) for the shared order and other pickers. No core or routing contract changes.
