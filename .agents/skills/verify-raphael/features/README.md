# Raphael feature map

Use [the verification workflow](../SKILL.md) first. Commands run from `app/` and
request both themes and each feature's documented widths; Home/calendar/dictation/members/conversation/team also require 1920x1080. Scene definitions in `app/scripts/look/scenes/`
are the executable source. Native journeys cover behavior the fixture cannot prove.

| Feature | User-facing scope |
| --- | --- |
| [Home](home.md) | Startup overview, pending work, reports, calendar, usage and dictation totals |
| [Conversation](conversation.md) | Threads, details panel, replies, public progress, search and composer |
| [Members](members.md) | Identity, resources, voice and Raphael's approved team administration |
| [Automations](automations.md) | Home-owned day/week calendar, schedules, advanced options and deletion |
| [Team and rooms](team-and-rooms.md) | Grouped board, member navigation, rooms and mentions |
| [Dictation](dictation.md) | Overview, history, dictionary, shortcuts, microphone and paste |
| [Settings and appearance](settings-appearance.md) | Profile/preferences, skins, languages, models and app information |
| [Connections and MCP](connections-mcp.md) | MCP setup/discovery, managed connectors core and Home's private Google calendar read |
| [Usage](usage.md) | Spend overview, grouping, CSV and CLI allowances |
| [Onboarding](onboarding.md) | First launch and entry into the usable app |

Keep entry points, prerequisites and observable end states current. If data is
missing, add it at the existing fixture boundary or report the failure; do not drop
the journey. Native/manual steps remain pending until exercised. This map alone is
not verification proof: a run's report and images are.
