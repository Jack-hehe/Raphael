# Onboarding

## Sub-features

Welcome, optional abilities, setup progression and entry into the usable app with
first-run state saved.

## How to get to it (user POV)

Launch with a fresh isolated data directory. Follow Next through welcome/ability
choices to the Home overview. Never reset an installed user's onboarding state.

## Driving it with look

```powershell
npm run look -- onboarding onboarding-abilities onboarding-done after-onboarding --theme both --width 1120,720
```

Keep welcome/completed-main-screen PNGs and report. Completion uses actual Next
clicks and disables every optional ability. Check responsive content, reachable
buttons and resulting Home navigation/status report. For onboarding changes add intermediate
scenes and exercise enabled abilities. Native proof needs an authorized fresh
`RAPHAEL_DATA_DIR`: complete setup, relaunch and verify onboarding stays dismissed
and chosen settings persist. Keep sanitized readback and stop the owned dev process.

## Gotchas

Each scene begins fresh. The onboarding variant overrides the ordinary onboarded
device setting. All-disabled completion proves only that branch. Credentials,
downloads, permission prompts, ability activation and native persistence remain
separate checks.

## UI audit regression coverage

The primary footer action fills the available centered action row (R-UI-37),
including welcome and completion, while secondary actions stay to its left.
Headings use the 28px onboarding scale. The welcome and completion screens omit redundant
subtitles; essential tutorial guidance remains visible (R-UI-02/37).

`onboarding-abilities` and `onboarding-done` capture the intermediate choice and
completion screens. Shared rendered checks reject small headlines and narrow
primary actions, with failing bad-state coverage in audit.test.mjs.

UI hand-off also runs these named scenes at 1920x1080 in both themes, as
required by the shared guardian hand-off gate.

Ability choices form one vertical checklist (R-UI-35), including at full-screen
widths. The rendered audit rejects side-by-side ability choices.

## First Bot conversation setup (P05a)

Open the first Bot's empty conversation before its profile has been confirmed.
Three cards collect name, personality and first work. Each supports two suggestions
and free text; Back preserves answers. Cancel dismisses the cards for the session
without saving. Review sends an ordinary localized conversation request and opens
the shared editable team proposal. Confirmation saves the profile on identity 0;
the resulting centered rename line appears once without a duplicate answer avatar.

```powershell
npm run look -- first-bot-choice first-bot-free-text admin-draft-onboarding bot-renamed-centred --theme both --width 1120,720,1920x1080
```

The fixture proves choice/free-text navigation, no pre-confirmation writes and the
shared confirmation boundary. Core tests recreate MembersService to read back the
saved name, personality and instructions. Native desktop relaunch is unverified.
