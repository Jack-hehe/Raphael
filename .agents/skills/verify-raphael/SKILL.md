---
name: verify-raphael
description: Verify Raphael's real desktop UI entry with the shared look CLI, inspect visual evidence, and check native behavior when a task requires it. Use after UI changes, for feature acceptance, or when reproducing a user-facing regression.
---

# Verify Raphael

Read the relevant [feature map](features/README.md), then follow this workflow.
This follows the project-local verification and feature-map structure of
[pstack](https://github.com/cursor/plugins/blob/main/pstack/skills/create-verification-skill/SKILL.md).
The maintained harness instructions are in [look README](../../../app/scripts/look/README.md).

## Launch

From the repository's `app/` directory:

```powershell
npm run look -- --list
npm run look -- chat --theme both --width 1120,720
```

`look` starts its own Vite server on a random loopback port and installed Edge/Chrome,
loads the real `index.html`, modules and CSS, and closes both after capture. Each
scene has an isolated fixture and each run has its own browser profile. No running
native app, credentials, real database or separate preview server is needed.
Readiness means Home's Raphael report (or onboarding Next button / overlay progress)
is visible. Non-home journeys then enter the conversation through the real rail,
followed by the scene's own prerequisite and resulting-state waits.

Use the installed dependencies; `puppeteer-core` is pinned to `25.13.0`. Do not
reinstall or download a browser as a workaround for a failed run. `LOOK_BROWSER`
can select an already installed Edge/Chrome executable.

## Health checks

1. Check cwd, branch and `git status --short`.
2. `npm run look -- --list` must list the intended scene. Unknown scenes fail.
3. Run the small launch command above. Read its newly printed `report.json`; check
   browser/version, selected themes/widths, per-scene results and top-level errors.
   A process or HTTP listener alone is not readiness.
4. Retain exact launch errors and the report. GPU/renderer access failures or startup
   timeouts mean no UI evidence was captured. Do not reuse older green images,
   disable checks, or classify this as an application defect.

## User-facing behavior

Choose the smallest verification scope that covers the changed behavior and its
affected consumers before running commands. Use the feature map to name those scenes
and state the selection briefly. A shared dialog, for example, needs its conversation,
routine, group and room confirmation scenes, not unrelated settings, onboarding and
dictation journeys. Keep all three sizes and both themes for the selected UI scenes.

```powershell
npm run verify
npm run look -- <affected scenes> --theme both --width 1120,720,1920x1080
```

Use the selected feature matrix for a local UI handoff. Reserve the full matrix for
integration/release acceptance, CI, explicit requests or genuinely app-wide changes
such as global tokens, navigation and shared layout. Sharing a component alone does
not require full-project verification. Add core or Rust checks only when their code,
contracts or behavior change; frontend-only styling does not require either suite.
Documentation-only changes need diff/link review. All commands are from `app/`.
`verify -- --quick` omits contrast and is only an iteration check. Report unavailable
dependencies and exact exits; do not rerun unchanged, unrelated suites to replace a
blocked check.

Scenes use real clicks, input, hover, menus and navigation. Extend
`app/scripts/look/scenes/*.mjs` and the one `fixture.json` when a journey is missing.
Mock only the existing Tauri IPC boundary (`mock-ipc.js`). Unsupported IPC remains
a failure even when caught by the app. Do not mock page modules or inject fake UI.
Keep prerequisite failures visible; fix stale tool selectors, not application code,
when the task is verification only. Removed owner menu/photo controls have no scenes.

The fixture proves UI behavior with synthetic native responses. It cannot prove
microphone capture, STT, global hotkeys, paste, persistence, provider calls, OAuth,
MCP servers or OS dialogs. Follow the feature's native journey when needed and
authorized by the task. The documented native launch command is
`npm run tauri -- dev`; use a dedicated `RAPHAEL_DATA_DIR` inside the worktree for
test data. Never drive a shared installed session or alter the user's credentials.
Record unexercised native steps as unverified.

## Evidence

- Keep the new `.look/run-*/report.json`, every PNG, `contacts.json` and every
  `contact-*.png`; default output is `app/.look/`. Verify logs and summaries live
  in `.look/verify-*/`. Never treat zero results as a pass.
- Inspect **every** contact sheet yourself. Open full-size PNGs for small controls,
  overlays, long text, clipped content and all reported failures. Review light/dark
  and 1120/720; passing automated checks do not imply visual acceptance.
- Preserve action-to-result evidence: scene names, commands, resulting state and
  boundary calls/side effects where relevant. Add deterministic assertions to the
  shared workflow when a journey needs more than a screenshot.
- Record date, branch, exact commands/exits, run paths, inspected sheet names,
  findings and incomplete/native checks in your hand-off or pull request description. Separate tool
  faults, environment blockers and app defects. Defects need reproduction,
  expected/observed behavior, affected combinations and image/report pointers.
- A failure seen twice becomes a lint/test rule. Keep regression tests alongside
  the shared runner. DOM tests are not browser/layout evidence.

## Cleanup

The CLI closes only its own browser and Vite server in `finally`. Check the final
report for cleanup errors and confirm PNGs/reports still exist after exit. Never
kill processes by name. If interrupted, identify only the instance/profile/port
started by this run before stopping it. Keep evidence; do not wipe `.look/` during
a handoff. Stop only a native dev process that this task started; leave the user's
installed app and other worktrees untouched.


## Guardian hand-off gates

Read [RULES.md](../../product/RULES.md) and [ARCHITECTURE.md](../../product/ARCHITECTURE.md).
Their named tests run in the ordinary Vitest/pytest suites on every PR, not local hooks.
From app/, the complete integration offline check is `npm run verify -- --core --rust`.
Local changes use the affected frontend/core/Rust boundaries described above.

For local UI hand-off, capture the selected affected scenes at both themes and all
three sizes. Full UI integration acceptance uses
`npm run look -- --all --theme both --width 1120,720,1920x1080`.
The 1920 viewport is 1080px high. Inspect every generated contact sheet and relevant
full-size image, then run `npm run judge -- <look run dir> --references <external png/jpg paths...>`
(or `npm run verify -- --judge <look run dir>` with RAPHAEL_JUDGE_REFERENCES as a JSON
array of external paths). Judge reviews the same selected look run; it must not widen
a local change into a full-project review. Judge is required after look and before UI hand-off,
or must be recorded as blocked/SKIP with the reason when unavailable.
It refuses failed/incomplete look evidence, uses judge rules plus each scene's
feature entry, and fails scores below 4, hard violations, malformed reviews and
budget exhaustion. Reports and summaries live in app/.look/judge-*/; cache identity
includes candidate bytes, model, rules, feature and reference bytes.

Judge uses Windows Credential Manager locally; hosted GitHub Actions alone accepts
OPENROUTER_API_KEY. No secret is ever logged. Its default vision model is configurable
with RAPHAEL_JUDGE_MODEL or --model. Costs vary by model and image size; --max-cost-usd,
--request-ceiling and --max-requests bound admission. Unknown/over-ceiling reported
costs stop the run. The provider controls final billing; the configured per-request
ceiling is a conservative reservation, not a provider-enforced credit limit.
Use an account/key spending limit as the billing backstop. Cache hits cost zero.
Reference images stay outside git; CI downloads public HTTPS images using a pinned
URL/hash manifest in the repository variable RAPHAEL_JUDGE_REFERENCE_MANIFEST.
Missing configuration fails when judge is enabled. No maintainer-specific path is
required. Missing fork secrets produce an explicit SKIP notice, never design PASS.

For brain/tool-path hand-off, the maintainer runs `npm run verify -- --live` with
installed/logged-in subscriptions and the OS key. RAPHAEL_LIVE_CONFIG can select a
JSON models list; core/guardian-models.json is the portable API example. Each turn
uses production loops/tool definitions/MCP with a recording executor and synthetic
files. It performs no real member, routine, memory, URL or deletion action. A model
request still uses network/subscription/API budget. Missing login/key is SKIP;
a failed scenario exits 1 and an entirely skipped/capped matrix exits 2. Reports
live in core/.live/run-*/report.json. Native CLI-internal model round trips are not
observable; the hard run cap counts API calls and submitted CLI turns, plus per-cell
timeouts. The nightly API-only workflow needs no CLI login and is explicitly opt-in.

Use the PR template to record rule IDs, commands/exits, look/judge/live artifacts,
images inspected and all incomplete/native acceptance. Never infer deployed or native
acceptance from offline gates. See GUARDIAN-NOTES.md for repository settings and costs.
