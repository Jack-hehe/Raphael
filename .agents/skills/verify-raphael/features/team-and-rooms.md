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
Raphael's wrap-up names the unfinished reply. Completed sources recover once without replaying work.
Room-only moderator/pro/con roles run two sequential rounds before the chief concludes. Empty and (pass) replies stay hidden.
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
```

Keep board/picker/thread/dialog PNGs. Check membership, authors, mention lists,
composer and overlay clearance. Team journeys assert the header padding and aligned Add action. The default team scene also checks R-UI-01: at 1920x1080 the group panels occupy at least 80% of workspace width and 35% of workspace area, remain inside the scrollport, and keep names and roles at a readable measure. The team-mentions scene checks line-start, multiline and inline @ positions, requiring the menu's left edge within 24px of the character at all three sizes. Mention/slash items start at normal row padding and reserve no empty icon/check column. For mutation work extend scenes through create,
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

Inspect the compact centered face/name/state line, expanded task/result detail and separate
final member message. The `room-thinking` fixture uses actual active-turn/reply-intent state.
`handoff-centred-line` covers the requesting private conversation even without public messages;
the six room handoff scenes cover room dispatch and results.
Offline P00 S03.immediate-start and S08.immediate-start exercise API/CLI dispatch; they do not
establish full Gmail/meeting workflow or live provider acceptance. Core handoff tests additionally
write and hash an actual HTML artifact through the shared gate/tool pipeline.

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
