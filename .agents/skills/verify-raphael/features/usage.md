# Usage

## Sub-features

Spend/runs, member expansion, period/grouping, run details, expanded tool groups,
automation history, empty state and Claude/Codex/Gemini CLI allowances.

## How to get to it (user POV)

Open Settings > Models > Usage. Change period/grouping, expand members, scroll to
the run list and open a run. Routine runs show automation details. CLI allowances
are farther down the same pane.

## Driving it with look

```powershell
npm run look -- usage usage-members usage-period usage-group-day usage-group-provider usage-group-routine usage-run-hover usage-empty --theme both --width 1120,720
npm run look -- usage-trace usage-trace-expanded automation-runs usage-allowances --theme both --width 1120,720
```

Keep summary/group/list/trace/allowance PNGs and report. Inspect popovers, nesting,
tool status, monetary labels and overflow. For accounting work add assertions for
fixture totals/filtering. Native acceptance needs an actual harmless turn/routine,
its persisted usage entry/trace and sanitized CSV if export changed. Reconcile
observable records; rendered charts cannot prove billing accuracy.

## Gotchas

Fixture run 1 is the primary turn trace; routine traces are separately keyed by
kind/id. Three CLI allowances are synthetic. Scroll before driving lower sections.
`usage-empty` is isolated. Look cannot prove provider billing, quota refresh or CSV
filesystem writes.
