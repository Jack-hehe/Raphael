# Onboarding

## Sub-features

Welcome, optional abilities, setup progression and entry into the usable app with
first-run state saved.

## How to get to it (user POV)

Launch with a fresh isolated data directory. Follow Next through welcome/ability
choices to the conversation UI. Never reset an installed user's onboarding state.

## Driving it with look

```powershell
npm run look -- onboarding after-onboarding --theme both --width 1120,720
```

Keep welcome/completed-main-screen PNGs and report. Completion uses actual Next
clicks and disables every optional ability. Check responsive content, reachable
buttons and resulting navigation/composer. For onboarding changes add intermediate
scenes and exercise enabled abilities. Native proof needs an authorized fresh
`RAPHAEL_DATA_DIR`: complete setup, relaunch and verify onboarding stays dismissed
and chosen settings persist. Keep sanitized readback and stop the owned dev process.

## Gotchas

Each scene begins fresh. The onboarding variant overrides the ordinary onboarded
device setting. All-disabled completion proves only that branch. Credentials,
downloads, permission prompts, ability activation and native persistence remain
separate checks.
