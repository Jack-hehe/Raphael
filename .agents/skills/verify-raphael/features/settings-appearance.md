# Settings and appearance

## Sub-features

Owner profile/preferences, themes/skins, models/providers, dictation, assistant,
messaging, data management and About. Usage and MCP have separate maps.
Subscription accounts share the room row-action menu: disconnect disables a CLI only
inside Raphael, removes its models, and leaves a framed Connect action. Reconnect reuses
an existing CLI login. Caption controls have rounded hover/pressed surfaces; Close uses
the contrasting close/error palette in every skin.

Model choosers for answers, cleanup, speech and vision share separated selection rows.
Local speech models show languages, size, notes and download state; choosing a row only
previews it until the fixed footer's action is used. Rows keep an 8 px gap, subtle
theme borders and visible keyboard focus, with metadata wrapping inside narrow panes.

## How to get to it (user POV)

Open the navigation gear, then a settings pane. Appearance has skin previews/theme
controls; General has owner name/about. The owner navigation menu and owner photo
were removed deliberately.

## Driving it with look

```powershell
npm run look -- profile appearance models dictation agent messaging data about skins --theme both --width 1120,720
npm run look -- skin-default skin-paper skin-graphite skin-lagoon skin-dusk skin-midnight skin-meadow skin-ink skin-catppuccin --theme both --width 1120,720
npm run look -- appearance --theme both --width 1120,720 --skin lagoon --locale ja
npm run look -- cli-account-menu cli-account-disconnected cli-account-reconnected window-hover-minimize window-hover-maximize window-hover-restore window-hover-close window-hover-modal --theme both --width 1120,720,1920x1080
npm run look -- model-choices-answer model-choices-cleanup model-choices-stt model-choices-local model-choices-local-selected --theme both --width 1120,720,1920x1080
```

Keep pane/skin PNGs; inspect wrapping, focus, scroll access, active selections and
preview/popover contrast. Use `--locale en` and `--locale ja` for translation work;
raw-key checks do not prove translation quality. Extend scenes through change/reopen
for persistence. Native provider/model, messaging, import/export, autostart and data
deletion need isolated real journeys and observable side effects.
CLI credential cleanup and persisted disconnection are covered by temporary-home core
tests; look drives the real account menu through the existing IPC boundary. Check all
caption hover images at full size and keep the driver's centering, margins, pressed,
tooltip, focus and close-contrast assertions. Native OS minimize/maximize/close and a real
CLI login remain separate checks; never log out the user's shared CLI account.

The model-choice scenes open Models and usage, then its answer, cleanup or speech
picker. Speech's local source uses the shared synthetic catalogue, including models
that are not downloaded. Rendered checks reject native outset borders, touching rows,
clipped metadata and overlapping footer actions, reach the last model by scrolling,
and ensure selecting a local model neither downloads it nor saves settings.

Safety-rule editing/deletion and always-allowed app removal use each row's overflow
menu (R-UI-11). Memory rows use the same policy and retain Undo.

## Gotchas

Both theme and skin matter; a preview is not the whole app in that skin. Contrast
tests cannot prove every rendered combination. Native settings/provider responses
are fixtures. Profile is reached through Settings, not a retired owner menu. Keep
destructive native steps within task authorization and disposable test data.
