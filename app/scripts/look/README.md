# Look and verify

From `app/`:

```sh
npm run look -- --list
npm run look -- chat details --theme both --width 1120,720
npm run look -- --all --theme both --width 1120,720
npm run look -- appearance --skin lagoon --locale ja --out .look/review
npm run verify
npm run verify -- --out ../.fold/app/.look
npm run verify -- --quick
npm run verify -- --core --rust
```

Look needs an installed Edge/Chrome and the pinned **puppeteer-core@25.13.0** devDependency.
It never downloads a browser or installs packages. Preserve the package/lockfile pin.
`LOOK_BROWSER` optionally selects an executable. Node 22 or later is required.

Each run creates `.look/run-*/` with viewport PNGs, paginated PNG contact sheets,
`contacts.json` and `report.json`. `--out` changes the parent directory. A nonzero exit
means a scene, runtime, layout, raw translation key, non-finite work duration, or visible native select/time-input
check failed; missing dependencies produce a `blocked` report and a nonzero exit.
Inspect contact sheets and full-size PNGs as well as the automated checks.

The real `index.html`, `main.ts` and CSS run through Vite. Overlay scenes select
the production `overlay.html` entry. Only the Tauri IPC boundary is
replaced by `mock-ipc.js`. Unsupported calls are failures, including rejections caught by
the app. External browser requests are blocked and reported. Every scene gets a fresh
copy of `fixture.json`: public synthetic Traditional Chinese content, local/embedded avatars,
conversations, work/replies/attachments, team/rooms, schedules/runs, usage/CLI allowances,
MCP servers, providers/models and dictation data. Browser dates are fixed to the fixture's
`now`, in Asia/Taipei. Change data in that file, not in a second mock.

The normal entry opens Home and waits for Raphael's status strip. Existing non-home
journeys then click the real Raphael rail destination before driving their steps.
Home scenes cover rich/empty data, the lower strip, expansion, reports and routines.

Add a `scene(name, description, steps, fixtureOverrides)` to a module in `scenes/` and
export it from `scenes/index.mjs`. Steps include click, type, hover, key, wait, scroll,
member/room selection and drag/drop. Use real UI selectors and wait for the resulting
state; do not replace app modules or inject a separate preview page. Fixture overrides
can replace collections; `running`, `problem`, `onlyRaphael`, `usageEmpty`
and `onboarding` select boundary variants. `overlayEvents` replays the native overlay
channel contract; `advance-time` advances the shared fixed clock for production timers. All scenarios appear in `--list`.

Use `check(name)` for browser assertions that need rendered geometry. The shared
`checks.mjs` guards title/panel state, conversation-list overflow, equal 32px
composer buttons, percentage captions, complete calendar-chip times, conversation
design and vertical dictation navigation. Successful measurements are saved in each
result's `assertions`. Every settings journey checks the dialog radius and close-control
hit target, then waits for the requested navigation entry to become current. Keep prerequisite
data in the shared fixture: usage expansion needs more than five historical member
groups, and a dictation heatmap day must have a nonzero count before it can be opened.

Coverage merges auto's automation/team/room matrix, nav's settings/MCP/usage matrix,
polish's composers/skins, layout's dictation/notices/onboarding and narrate's chat states.
Conversation management opens through the title-controlled details panel. Usage
retains aggregate summaries, grouping, allowances and CSV; per-run navigation is removed. The deliberately removed owner menu/photo controls have no
scenes. There are 139 current scenes (556 combinations for both themes and widths).
Team administration scenes cover full, long and eight-member batch approvals, held deletion approval,
live member selection after approval, and an unchanged roster after rejection.
Answer scenes select expandable replies, avoiding collapsed work's hidden messages;
the driver chooses the first visible match. Favicon requests return an empty 204.

Verify always runs TypeScript and Vitest; its default also runs contrast. `--quick`
excludes contrast; `--core` adds offline pytest and `--rust` adds offline locked cargo
tests. Optional checks use the existing environment/cache and fail if unavailable.
Use `--out dir` to select a writable evidence directory for look or verify.
Logs, exit codes and a JSON summary live in `.look/verify-*/` by default; independent checks
continue after a failure. These tools do not build, install, launch or deploy the native app.
