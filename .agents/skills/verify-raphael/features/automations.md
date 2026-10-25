# Automations

## Sub-features

Home-owned week/day/empty calendar, once/daily/weekdays/weekly/every schedules,
destination/end conditions, edit/delete and calendar drop prefill.

## How to get to it (user POV)

From Home click Today's icon or a timeline item. Home remains highlighted in the rail; there is no separate Automations entry. Switch Day/Week in the toolbar. Use Add, an event, or a member drop onto a calendar slot. All seven days fit the content width without horizontal scrolling. Above 960px of content width, the month, draggable members and Keep awake sit on the right. At smaller widths a calendar icon in the toolbar opens the same controls in a dismissible popover. Picking a date or starting a member drag dismisses it. Mini month weekdays use narrow names (one Chinese character each); event titles truncate with full tooltips and times remain complete. Visible event faces remain 20px squares. Advanced
fields are under More. Member details also exposes routines; aggregate usage and CSV are in
Settings > Models > Usage.

## Driving it with look

```powershell
npm run look -- automations-hover automations-empty automations-calendar automations-day --theme both --width 1120,720,1920x1080
npm run look -- automations-tools automations-tools-month automations-tools-date automations-tools-awake --theme both --width 1120,720,1920x1080
npm run look -- automation-once automation-daily automation-weekdays automation-weekly automation-every automation-hours-menu --theme both --width 1120,720,1920x1080
npm run look -- automation-destination automation-end-menu automation-max-runs automation-until automation-edit automation-delete automation-drag-prefill automation-more-bottom --theme both --width 1120,720,1920x1080
```

Keep dialog/calendar/menu PNGs; inspect bottom fields at 720, menu placement, labels
and vertical calendar scroll. `calendar-shell` asserts no horizontal grid overflow and that every day fits. Tools scenes cover month navigation, date selection, keep-awake and the responsive panel. The drag-prefill scene starts the drag from the visible side panel or popover. Current routine mutation scenes capture states, not completed saves.
For scheduling work extend scenes through save/reopen and assert fixture records.
In an authorized isolated native run, schedule a harmless task, observe its actual
due-time execution and persisted accounting, disable/delete it and verify it does not run again.

## Gotchas

Time is fixed to 2026-10-24, Asia/Taipei. Synthetic drop proves the handler's result,
not physical pointer dragging. Mock routines never execute jobs or OS wake behavior.
A calendar PNG cannot prove scheduler reliability. The old separate run-log UI is
gone; Usage retains aggregate summaries and CSV only.
