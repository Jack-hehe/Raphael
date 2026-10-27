# Conversation

## Sub-features

Named conversations per member; new/rename/delete; public work/event chips; answers,
attachments, reply quoting, message actions, search and composer menus; connector cards for an app a
task needs, and the one resume of that task (see Connecting an app from the chat below); a secret card for a key a
connection needs and a computer card for the owner's turn at a sign-in (see Handing a key or the computer to the owner below);
a permission card for an action that needs the owner's yes and an editable email card for a draft a member wrote (see Asking
the owner's yes and writing mail for them below).
A member's main conversation lists what colleagues asked or handed it where each came, as one closed
centred line; previews, search, unread state, other conversations and every model read exclude those
exchanges, and room turns. Room answers stay in their room/thread and have no shortcut into private history.
Raphael's single persistent quick-question conversation is created on the first global
voice question, pinned above formal conversations as 快速提問 / Quick questions / クイック質問.
Its fixed title and permanent identity have no rename/delete menu; individual messages
can still be deleted. Opening it and typing continues with the quick model and effort.
Alt+Space and a question typed there name the quick destination; nothing reaches quick or
formal by default (see Quick questions below).

## How to get to it (user POV)

From Home, click Raphael in the rail or open a member report. Select Raphael or another member in the sidebar (compact width:
member picker). Raphael stays first with his original large bare emblem and name. The column's top-left arrow toggles a remembered 56px face strip, including at compact width. Other expanded rows retain their 40px faces, time and preview. Click the stacked member/conversation title to toggle details. The title displays
the current session only; conversation creation, search, switching and row menus
live in the details list. The top-right gear opens Settings. Use sidebar search, or
the compact picker's search action, for messages.

## Driving it with look

```powershell
npm run look -- composer-task-stop chat running empty details raphael-details details-closed conversation-filter new-conversation row-menu rename delete --theme both --width 1120,720,1920x1080
npm run look -- quick-conversation-list quick-conversation quick-question-result quick-formal-isolation quick-stop --theme both --width 1120,720,1920x1080
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
in the assignee's isolated work turn; results return only to the requesting source. A quick
`ask_member` question is answered in the same turn and draws the same 已詢問 line, with no task behind it.
Source-document deadlines never schedule work.
The room handoff scenes in [Team and rooms](team-and-rooms.md) cover the shared event renderer;
private and room result destinations are also checked by offline core tests.

Every private/API/CLI/member turn receives the current public TeamContextSnapshot with self,
count, stable IDs, roles, primary/chief and secret-free app connection states. The primary is the owner's chosen primary contact (see [Members](members.md): Raphael
until another is chosen), read from that snapshot fresh on every turn: the primary is told it hands work it is given to the colleague it belongs to with the one hand-off
(S03.primary-dispatch), another member forwards work that is not its own to that colleague or, when it cannot tell whose it is, to the primary, and Raphael alone administers the team. Meeting requests
use room_meeting; one centred grey line with the group icon, outside Raphael's message, opens the room, and quick ask_member answers are
folded into the requesting assistant reply. Room history remains outside private turns.

```powershell
npm run look -- meeting-open-room meeting-chief-event member-mentions-bots-apps --theme both --width 1120,720,1920x1080
```


## Consulting a colleague and the shared memory (P14)

Grok Bot's two sides of a question: the asker's chat shows 「已詢問 X」 (handoff-line.ts), which opens on
the question that was sent and the answer, and the asked member's own chat shows 「訊息來自 Y」 with Y's
face where the question came (exchange-line.ts); several in a row from one colleague on one day fold into
「N 則訊息往來：Y」, as Grok Bot words it, opening on each question and answer with each speaker named once,
never a pasted transcript or a per-line label. Both ways of asking leave the same record, the asked
member's exchange turn: `ask_member` answers in the asker's turn, a `handoff` in consult mode runs as the
asked member's task with its own notes and conversation, and a private chat turn that consulted that way
comes back once with the answer. Shared notes name whom the owner told them to and when, in every memory
panel and in every member's prompt; what the owner told one member stays its own until a colleague asks.

```powershell
npm run look -- bot-ask-centred bot-message-from-centred bot-exchange-count bot-exchange-expanded global-private-memory --theme both --width 1120,720,1920x1080
```

The scenes seed 小森 asking 小晴 through the IPC fixture (`colleagues`, `sharedMemories`); the mock lists
a member's exchanges in its main conversation as the core does. Model behaviour is covered by Guardian
S04 and S12: offline with honest fake brains on both brain paths and both ways of asking, live on the API
brain. Rendered fixtures do not prove a live consult.

## Quick questions (P19)

The Alt+Space hotkey (and Raphael's own voice button) sends `agent.ask` with
`destination: "quick"`; a question typed in the quick conversation sends the same destination
with its conversation ID, and a formal thread sends only its ID. The core refuses a spoken
question to Raphael that names neither, a quick request naming another conversation and a
formal request naming the quick one. The quick model and effort answer quick turns, CLI quick
turns keep no native session, and each turn records the model that actually answered (Raphael's
face tooltip). A request replaces only the YES/NO questions waiting in its own conversation, so
quick use leaves a formal approval waiting and adds no formal rows. A spoken answer with public
progress has no reply message: the thread shows its text after the work line.

```powershell
npm run look -- quick-question-result quick-formal-isolation quick-stop quick-model-selection --theme both --width 1120,720,1920x1080
```

`quick-question-result` announces an Alt+Space answer through the native `AgentTurn` event while
the quick conversation is open and checks its answer and quick model. `quick-formal-isolation`
sends one formal and one quick question through the real composer and checks the destination
each names at the native boundary and that neither thread shows the other. `quick-stop` presses
Stop in the gold slot on a running quick question; only that session stops and the formal turn
is untouched. `quick-model-selection` (Settings › Models) picks a Codex model and High effort for
quick questions and checks that Raphael's answer model stays. The existing `quick-conversation`,
`quick-model-menu` and `quick-effort-menu` scenes cover opening the stored quick thread and the two
quick setting menus. Rendered fixtures do not prove the native hotkey, routing or a live model:
core tests and Guardian S16 cover those, and the native Alt+Space entry needs its own live check.

## Connecting an app from the chat (P08a)

When a typed request in Raphael's or a member's private chat needs Gmail or Google Calendar and the app is not
connected (or its sign-in expired), the brain calls `connect_app`. The turn shows that app's card below its reply
and calls nothing else. Connect (`integrations.card_connect`) answers at once and signs in in the background
through the owner's own Google authorization (loopback, PKCE, `state`) or Composio; Cancel
(`integrations.card_cancel`) ends it. The card reads pending, authorizing, connected with the real tool count,
failed with its reason (Open Connections when nothing is set up), cancelled, or expired after 24 hours; an expired
grant shows Reconnect. When every card of the turn is connected, the request runs once more in the same thread as a
`connector` turn: one centred line with the app's mark names the apps in place of the owner's words, the apps'
tools are offered, and an action the task did before its card is refused if repeated with the same arguments. A
replayed callback or a second Connect resumes nothing. No URL, code, `state` or token reaches the card, a model,
the history or a log, and each member's TeamContextSnapshot counts the app's usable tools.

```powershell
npm run look -- chat-connect-gmail chat-connect-calendar connector-authorizing connector-expired connector-reconnected --theme both --width 1120,720,1920x1080
```

The scenes press the real card buttons; the IPC fixture reports the card's states as the core does. They cannot
prove a sign-in. Core tests (`core/tests/test_connect_cards.py`) and Guardian `S14.gmail` run the core's own
loopback sign-in against an automatic Google fixture on both brain paths. A real Google account stays an
owner-approved smoke, and Instagram (P08d) keeps the parent S14 open.

## Handing a key or the computer to the owner from the chat (P08b)

Two more cards pause the task that asked for them, in a private chat typed at this computer (Raphael's or a member's),
and resume it once. A room, an exchange between members, a quick question, a spoken request and a message from a phone
never get either, and never carry their payload.

**The secret card.** A connection of the owner's own declares a secret (a header or an environment variable) that the
vault holds no value for: a call of its tool fails with `missing_secret` and the roster shows it as `needs_secret`.
The brain calls `request_secret` with the connection (its id or name from the roster) and, if it declares several,
the secret's name; it never names a place in the vault. The turn shows the connection's card below its reply and calls
nothing else: the connection's own mark, the secret's name, a masked field and 安全儲存. The key goes from the field
to `integrations.card_save`, straight into that slot of the vault (an Authorization header given as a bare token is
kept as a bearer); the field is emptied before the call, and the key is in no row, notification, reply, exception,
history, room, file or log, and the model learns only that a card is shown. Saved and resumed are two states: the card
reads 已儲存 and then 工作已繼續; when the resume failed it reads 金鑰已儲存，這項工作沒有繼續 with 再試一次
(`integrations.card_resume`), which carries the task on without asking for the key again and without repeating what
the failed attempt did. A replayed save does not touch the vault and resumes nothing; Stop, expiry (24 hours) and a
cancelled card never resume; a resume cut off by a restart is saved with `interrupted`.

**The computer card.** A computer task (`computer`) that reaches a password field, a sign-in or a payment does not end
with the hand-off line: it pauses and the turn shows 電腦 · 你正在控制 with 請在「Google Chrome」登入你的帳號，完成後交還控制,
停止 and the primary 我完成了. The computer is the owner's own PC, so the card names the window to use and there is no
開啟電腦 step. While the owner has it the run holds a human lease: it takes itself off the desktop (the overlay and the
watch on the owner's input are down) and asks the shell for no look, step or key, and the owner's own input ends
nothing. 我完成了 (`integrations.card_done`) answers at once and gives the computer back, once: the run looks at the
windows again, checks that the form is gone (once; when it is still there the card says 看起來還沒完成，請再試一次 and the
computer stays the owner's), and carries on with the same task, repeating nothing. When the run ends the task
resumes once in its thread, opening with 電腦 已交還控制，繼續原本的工作. 停止 (`integrations.card_stop`), Stop, a newer
computer task, the time (30 minutes) and closing end the run; a replayed 我完成了, the app closing or the core
restarting (the card is 連線中斷) never resume it or give control back by themselves. Spoken, quick and room runs keep
the hand-off line. A step that needs a yes after the owner gave the computer back asks in a permission card (below).

```powershell
npm run look -- chat-secret-masked chat-secret-saved-not-resumed chat-computer-takeover chat-human-control chat-handback --theme both --width 1120,720,1920x1080
```

The scenes press the real card buttons and type a made-up key into the real masked field; the IPC fixture reports the
cards' states as the core does and receives the key in `integrations.card_save` only (the rendered checks scan the page
and every call for it). They cannot prove the vault, a sign-in or a desktop. Core tests (`core/tests/test_secret_cards.py`,
`core/tests/computer/test_handback*.py`) and Guardian `S15.secret` and `S15.computer` run the production cards, a recorded
MCP server that opens only to the saved key and a fake desktop whose page asks for a sign-in, on both brain paths.
`S15.permission` (below) is the third half of S15. A real key and a real sign-in stay owner-approved native
smokes: type a test key into the card, read the connection's result, and sign in on an isolated test account.

## Asking the owner's yes and writing mail for them (P08c)

**The permission card.** When the gate says an action needs the owner's yes (a connection's tool that changes something
while 先詢問 is on or a rule of theirs says to ask first, the first use of an app on the computer, a step on it that sends,
deletes or buys), a private chat typed at this computer shows a card in that turn as well as the overlay's question: the
service (its own mark, or the computer's), the exact action in plain words as the question asks it, which of the owner's
rules had it asked, and 拒絕, 一律允許 and the primary 允許一次. It is a connector card with a second row for its answers.
The question is the one `agent.confirm` has always answered (the shell holds it); the card follows it
(`agent.permissions`) and the gold card stays away while a card waits on that question, so it is answered once. A
call that cannot be undone (T3) keeps the gold card that is held to confirm. 一律允許 keeps a narrow rule for that one
tool (`safety_rules.scope`, Always) and is offered only where it can take effect: an owner's Ask first always wins
over an Always, so where a rule asks the card names the rule and offers no 一律允許, and a rule of the owner's that is
Never refuses the call without a card. A yes is bound to the digest of the action asked about: changed inputs, a replay,
a card of another action, a restart and time never use an old approval. A question someone sits at lives 30 seconds; the
question of a task that waits unattended (the run that carries on after the owner gave the computer back, a background
task) lives about ten minutes, and the former answers through `integrations.card_allow`, `card_always` and `card_deny`
with the card's digest. A no is for the whole request: the tool is not tried again, no other tool of a connection that
changes something is tried in its place, and the computer and the CLI's own permission question are refused after it,
on the API loop, on the CLI brain's MCP path, on the computer run and in the background worker alike (Guardian
`S15.permission`). Rooms, exchanges between members, quick questions, spoken requests and messages from a phone get no card.

**The email card.** A member that writes an email does not send it: `email_draft` puts a draft in the owner's chat with
寄件者 (a dropdown of the connections that send mail), 收件者, 副本, 主旨 and the body, all editable, and 捨棄 and the
primary 傳送. Every edit is saved as a new version with a new digest on top of the digest the card holds
(`integrations.draft_save`, in order, each on the digest the one before came back with); 傳送 first saves what is
typed, then sends the digest of that version and nothing else (`integrations.draft_send`): once (a single conditional
claim), through the one gate with the owner's click as the yes, a rule of the owner's that is Never still refusing
it; an old digest, a replay and a double click send nothing, and a send that failed is not tried again by itself (an
edit makes a version that can go). 捨棄 touches nothing outside. A member can offer the draft to a colleague with a
choice card (「這封回信可以交給{name}寄嗎？」, 不用 or 可以); on yes the first chat keeps one centred line
(已轉交給 ◎ {name}) and the same version, with its digest, is an editable card in the colleague's private chat
(a `draft` turn that shows no words of its own; the card says whose draft it is); handing over never sends. A member's
profile (Permissions) has a switch for writing mail as a draft first, on by default: while it is on, a fast-tier
request cannot send mail with a connection's tool and is told to write the draft; when it is off the member sends
through the gate like any tool, and a rule of the owner's that asks first has the question asked (the permission card
names the rule). A routine or a background task keeps sending through the gate. Rooms, exchanges, quick questions,
spoken requests and messages from a phone are told to write it in the member's private chat and get no draft.

```powershell
npm run look -- local-permission-card draft-preference-ask-first email-draft-edit email-draft-discard email-draft-handed-off --theme both --width 1120,720,1920x1080
```

`local-permission-card` shows Raphael's chat with a pending card for a Gmail send (three answers, the primary last, the
gold card away). `draft-preference-ask-first` first opens 小晴's profile to check the draft switch is a real switch and
off, then shows the card her rule has asked for: it names the rule and has no 一律允許, and there is no draft card.
`email-draft-edit` types in the copy and the subject (two saves, the second on the digest the first came back with,
the card at 第 3 版 holding the newest digest); `email-draft-discard` presses 捨棄 (one quiet line, nothing saved or
sent); `email-draft-handed-off` answers 可以 on the choice card (one centred 已轉交給 小森 line in 小晴's chat), then
opens 小森's chat: the same version, editable, with 來自 小晴 的草稿. None of them presses 傳送, and the checks require
that nothing was sent. The IPC fixture (`permissionCard`, `memberDraftFirst`, `mailDraft`, `mailOffer`) reports the
states and the digests as the core does and keeps no mail. Rendered scenes cannot prove the gate, the shell's
question or a real send: core tests (`core/tests/agent/test_permission_cards.py`, `test_drafts.py`,
`test_draft_tool.py`, `core/tests/computer/test_handback_permission.py`) and Guardian `S15.permission` and
`S05.draft-handoff` run the production cards, gates and the recorded connector on both brain paths with every send
recorded and none real. `S05.gmail` (P08d) keeps the parent S05 open. A real mailbox stays an owner-approved native
smoke.

## P04 task lifecycle

Background narration uses the typed worker.progress status channel and is never appended to
agent_messages. Legacy stored progress remains readable. Terminal replies are separately validated
and published atomically with the task verdict and result outbox. Private Stop calls
agent.history.stop and preserves completed history and tool evidence; ack, running and owner-question
waiting all retain Stop in the existing gold slot. Microphone release never follows task Stop.
The shared composer tests cover IME Enter, Stop acknowledgement races, independent send completion
and the return to the voice emblem/tooltip at idle. Native microphone/controller behavior remains
unverified by rendered fixtures.
