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

```powershell
npm run verify
npm run look -- --all --theme both --width 1120,720
```

Run mapped feature commands while iterating; use the full matrix for UI handoff.
All commands are from `app/`. `verify -- --quick` omits contrast and is only an
iteration check. `verify -- --core --rust` additionally runs offline core/Rust tests
when that scope needs them; report unavailable dependencies and exact exits.

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
