# Home overview

## Sub-features

Startup and first-run landing, Raphael's status report, pending questions across
conversations, today's routines, latest unread member reports, today's calendar,
monthly spend/budget, fourteen-day spend series, subscription allowances and 
dictation charts. The twelve-column board pairs today's timeline with pending work
and reports; three chart cards follow. Today contains routine occurrences and connected
calendar events for the current date. Pending contains questions/approvals; unread
reports belong in Reports.

## How to get to it (user POV)

Launch the app, complete onboarding, click Home (first in the rail), or click the
Raphael brand. A report opens its member's conversation at that exact turn;
routines open Automations. Spend and allowance buttons open Usage, and the dictation card icon opens Dictation. The Today icon and timeline items open the calendar. The status report keeps Raphael's emblem visible and turning: it plays his loop (the one of the 拉斐爾 page) while the window allows motion and is the still picture under reduced motion, following the preference as it changes. Nothing sits right of the report except its one fixing icon when something is wrong: asking Raphael is done on his own page (owner, 2026-10-25), so the hero has no ask icon.
The bottom of the sidebar holds 連結應用程式, above Settings, with the number of connected apps
(a badge on its icon in the 拉斐爾 page's rail and in the sidebar at 880px and narrower); it opens
Settings > Connections (see [Connections and MCP](connections-mcp.md)).

## Driving it with look

```powershell
npm run look -- home home-empty home-720 home-report home-routine home-calendar home-many home-motion --theme both --width 1120,720,1920x1080
```

Scenes run with reduced motion, so every home scene above shows the emblem's still picture, and its `home-overview` check asserts that (and that the hero has no ask icon).
`home-motion` allows motion (`motion: true`, with `freeze-motion` for the CSS animations) and asserts that the page asks for the loop and that the loop is an animation (a WebP of 144 frames over 6 s):
the loop plays by itself, which a canvas cannot sample, so its screenshot shows whichever frame the clock was at. A refresh of Home does not replace the emblem's node, so his turn is not started over (pages/home.test.ts).

The Today card spans seven columns on desktop, pending/reports span five, and each chart spans four. Compact windows stack the board.

Inspect every contact sheet and the full-size home/report images. The home-720
scene scrolls to the lower usage and dictation area at all three widths. The home-report
assertion checks member history, conversation 11, around_id 2 and the rendered turn.
The after-onboarding scene also asserts that Home is selected and first in the rail.
Every home scene's `home-overview` check also asserts that each page is named on one line in the
sidebar; at 720px 連結應用程式 shows its number as a badge on its icon so that its name fits.

Timeline actions keep visible token-based frames (R-UI-18), including calendar events.

## Gotchas

All records come from home.overview; no new persistent store is introduced. Counts
cover all available records; payload limits are 12 today items, 8 reports and 24
calendar occurrences. Subscription windows can be unavailable and show that state.
Google Calendar uses connectors.status with refresh=false and the existing private
calendar_list_events tool only when ACTIVE and enabled. It reads the primary calendar,
expands recurring events and orders by start time, with a seven-day window, 24-result
limit and twelve-second timeout. Unavailable, disabled, missing-scope or expired
connections produce no events; routine occurrences remain available. Home refreshes
on ConnectionsChanged. All-day dates use the calendar's time zone. Reports remain
separate from the timeline. Offline connector tests exercise the real service and an
HTTP double; browser fixtures prove rendered navigation, not live
provider usage, OAuth, native persistence or scheduler execution. For native proof,
use an authorized isolated data directory, create a harmless routine, receive a
member report offscreen, then verify its arrival/read state and exact-turn jump.
