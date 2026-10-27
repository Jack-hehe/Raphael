# Guardian scenario contracts

`npm run verify -- --live` from `app/` now runs the P00/P22 scenario suite, followed
by the original Guardian cells under the same total submitted-turn budget. The
primary report is `core/.live/run-*/report.json`; the original report is linked
under `legacy`. Legacy simulated mutations never satisfy scenario tool coverage.

The suite always retains S01-S22 for C (Codex CLI) and A (OpenRouter), plus named
operation/transport/auth/branch/tool cells. A parent passes only when every child
passes, and a scenario passes only when both brains pass. Exit 0 means all required
results passed; exit 1 means a failure; exit 2 means incomplete/skipped results
without any failure. An unbuilt contract is `FAIL: not implemented: <PR>`, even
when a brain is missing. An implemented contract without a configured/available
brain or remaining turn budget is SKIP. API-only mode retains skipped C cells.

Set `RAPHAEL_LIVE_CONFIG` to a JSON file containing the owner's exact IDs:

```json
{
  "codex": "cli:codex:YOUR_INSTALLED_MODEL?effort=high",
  "api": "provider/YOUR_PINNED_MODEL"
}
```

A `models` list is also accepted: the first explicit `cli:codex:...` and first API
ID are used. No arbitrary Codex model is inserted. A missing entry stays missing.
The CLI's actual model must be present in its native thread response; a requested
model or history label alone is insufficient. Older CLI versions that omit this
metadata fail with `cli_actual_model_unverified_or_changed`.

For bounded direct runs from `core/`:

```powershell
uv run python -m raphael_core.evaluate.guardian --config models.json --max-turns 80 --seconds 90
uv run python -m raphael_core.evaluate.guardian --legacy-only --config models.json
uv run python -m raphael_core.evaluate.guardian --registry-directory fixtures/registry --manifest reviewed-manifest.json --config models.json
```

The optional registry directory is a sanitized production `ConfigStore` export
containing `integrations.json`. Every cached tool is enumerated, including disabled
tools, before shortlisting. S20 expands every connector, including disabled and
empty-tool connectors, as well as every registered tool. Copies go into each
cell's isolated App. Only synthetic
`guardian-*` connector fixtures are executable by the recording transport; unknown
servers fail closed. Remote catalogs/auth callbacks and the remaining product
operations must be supplied by their feature PRs. An unavailable complete catalog
is an explicit failing inventory cell, never an empty successful catalog.

`guardian_manifest.json` assigns every built-in registration to a specific cell and
required result assertions. New registrations require a mapping. A mapping alone
does not pass: coverage joins the current run, HEAD, requested model, canonical
identity, observed schema, brain, successful production execution and passing
read-back assertion. Discovery, denied calls, old schema/HEAD/run evidence and
driver-initiated setup RPCs never count. Dynamic identities include provider,
server ID and original operation, independently of display names and aliases.

Current executable flows are S17's missing-auth/missing-permission cases and
S21.routines create/list/edit/pause/resume/delete. Routine setup uses RPC; the model
must select the mutation/read tool, and the driver reads the result back through
RPC. All other complete owner contracts remain explicit failures with their
responsible PRs in `guardian_scenarios.py`. The coverage guard itself is executable,
but S22 correctly fails until the mapped calls pass on both brains and every
API/MCP surface is present. There are known API/MCP gaps for CLI memory/approval
and computer subtools; they are reported rather than excluded.

Each App has its own data/memory/workspace/session paths. Production model loops,
services, approval state, gates, stores and RPC serialization remain authoritative.
The recording connector is installed at the external transport boundary, after
ToolGate and IntegrationsService checks. Device actions also record locally.
Accepted, rejected and expired approvals are tested against this boundary. Reports
contain argument/text digests, classification, scope, costs/usage, timings and
test-mode receipts, never credentials or provider exception text. Unavailable
measurements remain null. Injected test runs are labeled `injected_offline_test`.

`--real-smoke CONNECTOR` always refuses without
`--owner-approved-real-smoke`. Even with that authorization it currently reports
the missing connector smoke adapter; it never falls back to a real write.

Offline tests use transport fakes with production RPC/loops/MCP. The shared
`live-scenario-owner-view` look scene uses the same public history/message shape
to check S17's actionable failure and separate progress through the real renderer.
That fixture is not live model, connector, native or design acceptance.

## S01 team setup acceptance (P05)

S01 uses the production AgentService loops for both brains. It checks that a
five-member team is proposed without roster mutation, cancels and replays the old
confirmation, then requests the team again, edits the first prompt, applies once
and reads back the saved prompt. Replaying approval must not change the roster.
Only committed admin operations count toward tool coverage. The offline fake API
and sequential authenticated CLI/MCP tests are deterministic evidence; they do not
establish live provider acceptance.

## S02, S03 and S07 with a primary contact (P15b-1)

The owner's primary contact is Raphael until they choose another member (`members.set_primary`). S02 asks every Bot for 「主要窗口：名字」, 「幕僚長：名字」 and
「已連結應用程式：N 個」, one line each, then changes the primary to 行銷 (with the rename and the removed app) and asks every Bot again: each reply must name
the roster's primary (`roster_primary_missing`, `roster_primary_stale`, `roster_primary_wrong`) and Raphael as the chief of staff (`roster_chief_missing`,
`roster_chief_wrong`). `S03.primary-dispatch` makes 行銷 the primary and tells it, in its own chat, to have 信件助理 read the mail now: one durable task
from the primary to 信件助理 (`dispatcher_not_primary`), the primary's centred 已轉交給 line (`handoff_line_missing`), one receipt, so the primary did not read the
mail itself (`primary_did_the_work`), and the usual immediate-start, scope, result and no-routine checks. `S07.meeting` is held with HiKari as the primary: Raphael
is in the room and writes its one wrap-up as before, and every room turn must carry the roster with that primary and Raphael as the chief
(`meeting_turn_primary_unknown`). Offline honest fake brains and a lying brain for each check are in core/tests/evaluate/test_p02_scenarios.py and
test_guardian_scenarios.py.

## S16 quick question (P19)

S16 asks as the Alt+Space hotkey does (`agent.ask` with `destination: "quick"`, no
source) on the run's own model as the quick model and effort, while Raphael's formal
chat runs on the other configured brain's model, so a crossed model is visible. It
seeds a formal code, then asks a quick and a formal question at the same moment twice:
quick turns must stay in the quick conversation on the quick model with no stored CLI
session, formal turns in their thread on their own model, neither side may name the
other's code, and members and rooms gain nothing. The cell records the settings it ran
with. Without the other brain (or in API-only mode, where no CLI starts) it is SKIP
"formal contrast brain unavailable". Offline honest fake brains on both brains and one
negative test per check live in `core/tests/evaluate/test_p19_scenarios.py`; the native
Alt+Space entry still needs its own live check.
