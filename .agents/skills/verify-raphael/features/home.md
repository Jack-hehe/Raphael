# Home overview

## Sub-features

Startup and first-run landing, Raphael's status report, pending questions across
conversations, today's routines, latest unread member reports, seven-day calendar,
monthly spend/budget, fourteen-day spend series, subscription allowances and four
dictation totals. The briefing is the selected layout. Today contains only pending
questions/approvals and routines due today; unread reports belong only in Reports.

## How to get to it (user POV)

Launch the app, complete onboarding, click Home (first in the rail), or click the
Raphael brand. A report opens its member's conversation at that exact turn;
routines open Automations. Spend and allowance buttons open Usage, and the small
dictation strip opens Dictation. The status report keeps Raphael's emblem visible.

## Driving it with look

```powershell
npm run look -- home home-empty home-720 home-report home-routine home-expand --theme both --width 1120,720
```

Inspect every contact sheet and the full-size home/report images. The home-720
scene scrolls to the lower usage and dictation area at both widths. The home-report
assertion checks member history, conversation 11, around_id 2 and the rendered turn.
The after-onboarding scene also asserts that Home is selected and first in the rail.

## Gotchas

All records come from home.overview; no new persistent store is introduced. Counts
cover all available records; payload limits are 12 today items, 8 reports and 24
calendar occurrences. Subscription windows can be unavailable and show that state.
Google Calendar uses connectors.status with refresh=false and the existing private
calendar_list_events tool only when ACTIVE and enabled. It reads the primary calendar,
expands recurring events and orders by start time, with a seven-day window, 24-result
limit and twelve-second timeout. Unavailable, disabled, missing-scope or expired
connections produce no events; routine occurrences remain available. Home refreshes
on ConnectionsChanged. All-day dates use the calendar's time zone. Today never gains
calendar events or reports. Offline connector tests exercise the real service and an
HTTP double; browser fixtures prove rendered navigation, not live
provider usage, OAuth, native persistence or scheduler execution. For native proof,
use an authorized isolated data directory, create a harmless routine, receive a
member report offscreen, then verify its arrival/read state and exact-turn jump.
