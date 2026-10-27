# Look and verify

From `app/`:

```sh
npm run look -- --list
npm run look -- chat details --theme both --width 1120,720
npm run look -- --all --theme both --width 1120,720,1920x1080
npm run look -- composer-learn member-composer-learn room-composer-learn team-composer-learn --theme both --width 1120,720,1920x1080
npm run look -- appearance --skin lagoon --locale ja --out .look/review
npm run verify
npm run verify -- --out ../.fold/app/.look
npm run verify -- --quick
npm run verify -- --core --rust
npm run judge -- .look/run-EXAMPLE --references <external-reference.png>
npm run verify -- --live
```

For local changes, select scenes from the affected feature map and run those at both
themes and 1120/720/1920x1080. A shared confirmation dialog, for example, uses
`npm run look -- delete automation-delete team-delete room-delete --theme both --width 1120,720,1920x1080`.
Shared components need representative affected consumers, not every unrelated page.
Reserve `--all` for integration/release acceptance, CI, explicit requests or genuinely
app-wide changes. Add `verify -- --core` or `verify -- --rust` only when those
boundaries change; documentation-only work needs diff/link review. Judge reviews the
same selected look run. State the selection and any incomplete evidence in the handoff.

Look needs an installed Edge/Chrome and the pinned **puppeteer-core@25.13.0** devDependency.
It never downloads a browser or installs packages. Preserve the package/lockfile pin.
`LOOK_BROWSER` optionally selects an executable. Node 22 or later is required.

`--width` accepts widths (900px height) and explicit `WIDTHxHEIGHT` viewports.
Each run creates `.look/run-*/` with viewport PNGs, paginated PNG contact sheets,
`contacts.json` and `report.json`. `--out` changes the parent directory. A nonzero exit
means a scene, runtime, layout, raw translation key, raw Markdown text, mixed-language copy, non-finite work duration, or visible native select/time-input
or checked product-rule violation failed; missing dependencies produce a `blocked`
report and a nonzero exit. Product rules include duplicate tooltips, visible row
destruction, action frames/order and known untranslated interface copy. These
checks do not replace design review.
Inspect contact sheets and full-size PNGs as well as the automated checks.
The shared audit rejects visible text nodes containing Markdown heading markers,
unchecked task markers, paired bold markers or triple backticks, including outside
chat messages. Hidden pages, content outside scrollports and the text inside an editor
field (a textarea holds a document its owner edits, such as a private skill) are excluded.

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
Home scenes cover rich/empty data, reports, routines and calendar navigation.

Add a `scene(name, description, steps, fixtureOverrides)` to a module in `scenes/` and
export it from `scenes/index.mjs`. Steps include click, type, hover, key, wait, scroll,
member/room selection and drag/drop. Use real UI selectors and wait for the resulting
state; do not replace app modules or inject a separate preview page. Fixture overrides
can replace collections; `running`, `problem`, `onlyRaphael`, `usageEmpty`
and `onboarding` select boundary variants; `permissionCard`, `memberDraftFirst`, `mailDraft` and `mailOffer` seed the permission and email cards of a chat (the Conversation feature map, P08c). `overlayEvents` replays the native overlay
channel contract; `advance-time` advances the shared fixed clock for production timers. Scenes run with reduced motion,
so nothing moves in a still frame; a scene about something that moves sets `motion: true` and holds every animation at
one time with `freeze-motion` (`at` in ms), the same frame each run. All scenarios appear in `--list`.

Use `check(name)` for browser assertions that need rendered geometry. The shared
`checks.mjs` guards title/panel state, conversation-list overflow, the single composer
action slot, percentage captions, complete calendar-chip times and 20px faces,
conversation design and horizontal dictation navigation. Successful measurements are saved in each
result's `assertions`. Every settings journey checks the dialog radius and close-control
hit target, then waits for the requested navigation entry to become current. Keep prerequisite
data in the shared fixture: usage expansion needs more than five historical member
groups, and a dictation heatmap day must have a nonzero count before it can be opened.

Coverage merges auto's automation/team/room matrix, nav's settings/MCP/usage matrix,
polish's composers/skins, layout's dictation/notices/onboarding and narrate's chat states.
Conversation management opens through the title-controlled details panel. Usage
retains aggregate summaries, grouping, allowances and CSV; per-run navigation is removed. The deliberately removed owner menu/photo controls have no
scenes. There are 269 current scenes (1614 combinations across light/dark and 1120, 720, 1920x1080). The member-column scenes cover the restored Raphael row, collapse, face tooltips and re-expansion; dictation tabs use horizontal keyboard navigation. Team mentions and slash commands align to their text character and prefer the space above it, without an empty icon/check column. Calendar checks prohibit horizontal scrolling, keep all seven days visible and exercise responsive month/member/keep-awake controls. Team headers share the page padding and align Add with the title. Geometry checks cover all three sizes. The default Team scene also enforces R-UI-01: group panels use at least 80% of workspace width and 35% of its area at full screen. Model-choice scenes cover cloud/local row spacing, themed borders, readable metadata, scroll access and keyboard selection without downloading.
Team administration scenes cover full, long and eight-member batch approvals, held deletion approval,
live member selection after approval, and an unchanged roster after rejection.
The primary-contact scenes (`primary-contact-menu`, `primary-contact-picker`, `primary-contact-set`, `primary-contact-strip`, `primary-room-chief`) choose the owner's primary contact through the real member menu and
picker; the IPC double keeps `primaryId` (the fixture key `primaryMember` seeds it) and answers `members.set_primary` and the list's `team_context.primary_id` as the core does.
The apps and skills scenes (`scenes/apps.mjs`) enter through the sidebar's connected-apps entry.
The IPC double derives the inventory and the roster's count from the fixture's connections as the
core does, and the `apps-count` check compares the sidebar badge, the tab's number, the rows under it
and the roster; the fixture key `inventoryUnreadable` makes it a core that cannot list the apps
(`apps-unreadable`), and `wait-gone` waits for a row to leave after an uninstall.
Answer scenes select expandable replies, avoiding collapsed work's hidden messages;
the driver chooses the first visible match. Favicon requests return an empty 204.

Subscription account scenes drive the shared row menu through disconnect and reconnect. The
`window-control` driver step checks caption hover/pressed tokens, rounded geometry, glyph
centering, inner margins, labels, keyboard focus and close contrast without closing the page.
Its `hoverOnly` option preserves an open settings modal for the resulting screenshot.

Verify always runs TypeScript and Vitest; its default also runs contrast. `--quick`
excludes contrast; `--core` adds offline pytest and `--rust` adds offline locked cargo
tests. Optional checks use the existing environment/cache and fail if unavailable.
Use `--out dir` to select a writable evidence directory for look or verify.
Logs, exit codes and a JSON summary live in `.look/verify-*/` by default; independent checks
continue after a failure. These tools do not build, install, launch or deploy the native app.

Guardian's required UI sequence is look, inspection, then judge. Judge requires
all three sizes and both themes for every selected scene and a passed look report.
Its rules, feature context, external reference paths, budgets and cache are
documented in the verify-raphael skill and `GUARDIAN-NOTES.md`. The verify alias is
`npm run verify -- --judge <look run>`; set `RAPHAEL_JUDGE_REFERENCES` to a JSON array
of external image paths. Live matrix and judge opt into paid network requests;
ordinary verify/look and their fake-model unit tests remain offline. The judge request cap is 1614 (the full matrix has 1614 images); dollar budgets and per-request admission ceilings still apply. Every PR runs
the repo's contracts and full look matrix in CI. Missing hosted secrets produce
an explicit SKIP notice, never live/design acceptance.
