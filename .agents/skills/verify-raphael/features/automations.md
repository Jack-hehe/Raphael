# Automations

## Sub-features

List/empty examples, week/day calendar, once/daily/weekdays/weekly/every schedules,
destination/end conditions, edit/delete and calendar drop prefill.

## How to get to it (user POV)

Choose Automations. Switch List/Calendar and Week/Day in the header. Use Add, a card's
edit action, an empty-state example, or a member drop onto a calendar slot. Advanced
fields are under More. Member details also exposes routines; run evidence is in
Settings > Models > Usage.

## Driving it with look

```powershell
npm run look -- automations-list automations-hover automations-empty automations-calendar automations-day --theme both --width 1120,720
npm run look -- automation-once automation-daily automation-weekdays automation-weekly automation-every automation-hours-menu --theme both --width 1120,720
npm run look -- automation-destination automation-end-menu automation-max-runs automation-until automation-edit automation-delete automation-example automation-drag-prefill automation-more-bottom automation-runs --theme both --width 1120,720
```

Keep dialog/calendar/menu PNGs; inspect bottom fields at 720, menu placement, labels
and calendar scroll. Current mutation scenes capture states, not completed saves.
For scheduling work extend scenes through save/reopen and assert fixture records.
In an authorized isolated native run, schedule a harmless task, observe its actual
due-time execution/usage trace, disable/delete it and verify it does not run again.

## Gotchas

Time is fixed to 2026-10-24, Asia/Taipei. Synthetic drop proves the handler's result,
not physical pointer dragging. Mock routines never execute jobs or OS wake behavior.
A calendar PNG cannot prove scheduler reliability. The old separate run-log UI is
gone; `automation-runs` uses unified Usage.
