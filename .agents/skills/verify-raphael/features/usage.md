# Usage

## Sub-features

Spend overview, member expansion, period/grouping bars, CSV export, empty state
and Claude/Codex/Gemini CLI allowances. No per-run list or trace navigation.

## How to get to it (user POV)

Open Settings > Models > Usage. Change period/grouping, expand members and export
CSV. CLI allowances are farther down the same pane.

## Driving it with look

```powershell
npm run look -- usage usage-members usage-period usage-group-day usage-group-provider usage-group-routine usage-empty usage-allowances --theme both --width 1120,720
```

Keep summary/group/allowance PNGs and report. Inspect monetary labels and overflow.
For accounting work assert fixture totals/filtering. Native acceptance needs an
actual harmless turn/routine and a sanitized CSV if export changed. Reconcile
observable records; rendered charts cannot prove billing accuracy.

## Gotchas

Three CLI allowances are synthetic. Scroll before driving lower sections.
`usage-empty` is isolated. Look cannot prove provider billing, quota refresh or CSV
filesystem writes. Aggregate accounting and CSV still include historical run data;
the removed usage.trace RPC has no UI or native allowlist entry.
