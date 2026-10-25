# Settings and appearance

## Sub-features

Owner profile/preferences, themes/skins, models/providers, dictation, assistant,
messaging, data management and About. Usage and MCP have separate maps.

## How to get to it (user POV)

Open the navigation gear, then a settings pane. Appearance has skin previews/theme
controls; General has owner name/about. The owner navigation menu and owner photo
were removed deliberately.

## Driving it with look

```powershell
npm run look -- profile appearance models dictation agent messaging data about skins --theme both --width 1120,720
npm run look -- skin-default skin-paper skin-graphite skin-lagoon skin-dusk skin-midnight skin-meadow skin-ink skin-catppuccin --theme both --width 1120,720
npm run look -- appearance --theme both --width 1120,720 --skin lagoon --locale ja
```

Keep pane/skin PNGs; inspect wrapping, focus, scroll access, active selections and
preview/popover contrast. Use `--locale en` and `--locale ja` for translation work;
raw-key checks do not prove translation quality. Extend scenes through change/reopen
for persistence. Native provider/model, messaging, import/export, autostart and data
deletion need isolated real journeys and observable side effects.

Safety-rule editing/deletion and always-allowed app removal use each row's overflow
menu (R-UI-11). Memory rows use the same policy and retain Undo.

## Gotchas

Both theme and skin matter; a preview is not the whole app in that skin. Contrast
tests cannot prove every rendered combination. Native settings/provider responses
are fixtures. Profile is reached through Settings, not a retired owner menu. Keep
destructive native steps within task authorization and disposable test data.
