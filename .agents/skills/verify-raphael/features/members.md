# Members

## Sub-features

Remembered member-column collapse, original Raphael emblem, identity/personality, profile editing, avatar selection, memory, routines, skills,
commands and voice settings, and the Permissions tab's two switches: ask before contacting colleagues, and write mail as a draft for the owner to send
(`draft_first`, on by default; see the P08c section of [Conversation](conversation.md)). Members have independent conversations.
The owner's primary contact (P15b-1, the last section) wears a star in the sidebar and in its details.

## How to get to it (user POV)

In Conversation the top-left arrow collapses the member column to a 56px face strip; the same arrow expands it. Raphael stays first with his original large bare emblem and name when expanded. Collapsed faces retain localized tooltips, active selection and unread/waiting dots, including members in folded groups. The choice is remembered in localStorage, with an in-memory fallback when storage is unavailable. Compact windows initially use the face strip; the member picker still exposes rooms and search. An open details overlay temporarily hides an expanded compact column so the title remains reachable; closing details restores it without changing the saved choice. The details-closed scene explicitly expands the column before testing the title.

Select a member, then click the stacked header title for details.
At compact width use the member picker to select the member first. The same title
closes the panel; its close button updates the same remembered session state. Details has the avatar, edit pencil and resource
panes. Team's creation menu is another entry point to member creation; verify the
complete flow for creation changes.

## Driving it with look

```powershell
npm run look -- members-expanded members-collapsed members-collapsed-hover members-reexpanded --theme both --width 1120,720,1920x1080
npm run look -- details member-avatar member-edit details-memory details-routines details-skills details-commands details-voice --theme both --width 1120,720,1920x1080
npm run look -- team-new-menu member-text --theme both --width 1120,720,1920x1080
```

Keep details/resource-pane/avatar-menu/edit-dialog PNGs; check layering, focus and
scroll access. Current scenes open editing but do not save. For creation work extend
shared scenes to create a synthetic member, open its avatar picker while the dialog
is open, save, open its conversation and reopen details. Assert identity/avatar and
selection. Native proof must also relaunch the isolated dev instance and confirm
persistence, then remove test data.

Memory, routine, skill and command rows put edit/delete in their overflow menus
(R-UI-11). A shared note's row names whom the owner told it to and when (`global-private-memory`). Routine switches remain on their rows. Existing edit, approval and undo
behavior remains in the shared editors and controllers.

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
npm run look -- admin-approval admin-approval-batch admin-approval-long admin-delete-approval admin-approved admin-denied --theme both --width 1120,720,1920x1080
npm run look -- member-draft member-draft-edit member-draft-done --theme both --width 1120,720,1920x1080
```

The scenes retain full approval fields, allow long instructions to scroll by
keyboard, keep actions below the details, select the member after approval and
assert that rejection leaves the roster unchanged. Deletion uses the existing T3
held approval even in Direct mode. The boundary fixture does not prove model
selection or persistence: core tests drive API turns and authenticated HTTP MCP
turns through the real member service. Live provider/CLI and native relaunch
checks remain separate.

Every action mode and brain collects the entire turn's reversible member/group changes in one card,
including eight-member requests across multiple model rounds or sequential CLI MCP
calls. Every member lists name, role, blurb and group. Approval applies the entire
transaction; rejection or expiry applies none. An invalid operation rolls everything
back without publishing partial roster changes. Deletions retain per-member T3 cards.
Chinese and Japanese labels use full-width colons; English uses colon-space.
The admin-approval-batch scene checks all eight entries, internal keyboard scrolling
and the one pair of actions in both themes and widths. When structured `drafts` are
present on `team_admin_batch`, the conversation renders `.draft-panel` with an
editable `.draft-card` per proposed member (face/preset picker, name, role, blurb,
group dropdown, expandable personality/instructions, remove button) and primary
count CTA (`建立 N 位成員`), then settles into an expandable `.draft-done` summary
(`已建立 COO、PM、RD`) once confirmed (`member-draft`, `member-draft-edit`, `member-draft-done`).

## Codex subscription isolation

Codex brains use a private home and empty working directory for each conversation.
Only Raphael's MCP server is configured; its tools are direct, with personal apps,
plugins, hooks, skill discovery, memories and project documents disabled. Existing
personal Codex rollouts are not imported. Isolated sessions resume independently.

Offline proof: core/tests/agent/test_cli_brains.py checks the actual fake child's
command, environment, login snapshot, cleanup and session migration. The login
snapshot has no refresh token; an expired access token requires the owner's Codex
to refresh its login before the next Raphael turn. Browser fixtures cannot prove
this boundary. For live acceptance on a Codex brain, ask Raphael to create members
and check the turn's MCP steps show team_overview and member_create, not other tools.

A subscription turn that ends with HTTP 429 (Codex "Too Many Requests", Claude Code
`api_error_status` 429) is rate-limited unless the quota windows say the plan is used up.
A turn that has not started a tool, asked a permission or called Raphael's tools runs again
after 20 s and 60 s with one progress line per wait (a room or background turn, which no one sits
waiting on, after 30, 60, 120 and 240 s); quick questions and turns that already
acted end at once with a specific rate-limit message, and a background task keeps that message
with the reason `cli_rate_limited`. Offline proof: core/tests/agent/test_cli_brains.py,
core/tests/background/test_room_cli.py and core/tests/members/test_cli_brains.py.

## The primary contact (P15b-1)

The owner chooses which member is their primary contact, the Bot that new work is brought to and that hands it to the colleague it belongs to.
Raphael is the primary until another is chosen, and exactly one is at a time. Choosing is 設為主要 in a member's menu (the row's more button
or a right click): it opens 「選擇主要成員」, a list of real buttons (Raphael and the other visible members, not the primary now) with the member whose menu
it was chosen already, 取消 and the primary 設為主要. The primary wears a round star at the corner of its face in the column, the face strip and the details.
Raphael's row has no menu, so he is chosen again from another member's menu. Raphael stays the chief of staff, in every room and the one wrap-up, whoever
the primary is: nothing about rooms, tasks, exchanges or conversations changes when the primary does. The primary does not move the owner's own entry points
either (the video and the spec say nothing about them): the 拉斐爾 page still opens on Raphael, Alt+Space quick questions stay his (P19), and a new conversation lists
him first; the owner reaches the primary by selecting its row, and it is the other Bots and the brains that route new work to it.

```powershell
npm run look -- primary-contact-menu primary-contact-picker primary-contact-set primary-contact-strip --theme both --width 1120,720,1920x1080
npm run look -- members-expanded members-collapsed members-collapsed-hover members-reexpanded details raphael-details --theme both --width 1120,720,1920x1080
```

`primary-contact-menu` opens 小森's menu and checks that 設為主要, with the star icon, is one item right after 移到分組. `primary-contact-picker` opens the window from it while 小晴 is the primary (fixture key `primaryMember`) and checks the title, that
the members are buttons of one radio group with no native control, that 小晴 is not offered and Raphael is, one choice with its check, 取消 just left
of the primary 設為主要, and that nothing was set yet. `primary-contact-set` chooses 小晴 and presses 設為主要: one `members.set_primary` call, the star on
her row (and no longer on Raphael's) and on the face in her details, and no room, routine, task or conversation call. `primary-contact-strip` collapses the column with 小晴 as the primary: the star is on her face alone and her name and tooltip say 「小晴 · 主要聯絡人」. The scenes of the
column and the details show Raphael's star while he is the primary. The IPC double keeps `primaryId` as the core keeps the flag: the list's
`team_context.primary_id` says it, a hidden or deleted primary is Raphael again, and a hidden member cannot be chosen.

The core keeps the flag on the member's row (schema v39, a unique index) and the roster is the one place it is read from: the snapshot's `primary_id`
(and `chief_id`, always 0) is in its revision, and every prompt (an API turn, a resumed CLI thread's per-turn team block, a background task or
routine, each room turn) is built from it fresh. Offline proof: core/tests/members/test_primary.py (one at a time and its persistence, hide and delete hand it
back to Raphael, nothing else changes, each room keeps Raphael and concludes once whenever the primary changes, a roster that kept the old one
is told apart on each path), core/tests/background/test_primary.py, and Guardian S02 (every Bot names the new primary and Raphael as the chief after the
owner changes it; a brain that keeps the old one, never says it or mixes the two up fails on both brain paths), S03.primary-dispatch (the primary hands
the work to 信件助理 itself) and S07.meeting (held with a primary that is not Raphael). Live brains and the native shell (the persisted choice
after a relaunch, the allowlisted `members.set_primary`) need their own acceptance.

## Editable proposals and first Bot setup (P05/P05a)

API and sequential authenticated CLI/MCP calls queue the same turn-end proposal.
Pending tool responses have no created IDs and explicitly prohibit claiming success.
Owner confirmation rechecks edited arguments against current safety rules and applies
the transaction once. Cancel, expiry and a superseding turn discard it. Repeated
updates to one target merge their fields into one card. Nested member work cannot
administer the roster; deletion retains its separate held T3 confirmation.

The conversation creation picker offers Create from description for a nonempty
search. It opens the chief's ordinary composer with a proposal request. First Bot
setup collects name, personality and first work through choices or free text, then
uses the same draft. Confirmation saves the profile on identity 0 and renders one
centered rename line. A personalized name never grants another member admin tools.
Group proposal names are editable and follow the existing native confirmation route.

```powershell
npm run look -- admin-description-draft admin-cli-draft-edit admin-draft-cancel admin-draft-apply-result first-bot-choice first-bot-free-text admin-draft-onboarding bot-renamed-centred --theme both --width 1120,720,1920x1080
```

Core evidence: tests/members/test_admin_proposals.py covers both fake API and
sequential HTTP MCP paths, edits, direct/ask modes, cancellation, expiry, resend,
atomic rollback, target scope, current safety, saved first Bot identity and replay.
Guardian S01 runs the production loops for both fake brains. Browser IPC fixtures
prove rendered interactions only; live brains and native persistence remain separate.
