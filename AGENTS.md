# Working in Raphael

- Use English for code, comments and commit messages; talk to the maintainer in Traditional Chinese (繁體中文).
- Run `npm run verify` from `app/` before handing off changes.
- Verify UI with `npm run look -- --all --theme both --width 1120,720` from `app/`.
  Drive the real app entry, inspect every contact sheet and relevant full-size image,
  and report failures and evidence in your hand-off or pull request description.
- A passing build or test suite alone never proves user-facing behavior.
- Put deterministic steps in scripts. Extend the existing `verify` and `look`
  workflow instead of creating another feature-specific harness.
- A failure seen twice becomes a lint or test rule.
- Read [verify-raphael](.agents/skills/verify-raphael/SKILL.md) and its feature map.
  Keep the map current when behavior or navigation changes.
- Preserve unrelated work and formatting. Report incomplete checks plainly.

## Definition of done

Follow [product rules](.agents/product/RULES.md), [architecture](.agents/product/ARCHITECTURE.md)
and [verify-raphael](.agents/skills/verify-raphael/SKILL.md). From `app/`, run
`npm run verify -- --core --rust` and, for UI, `npm run look -- --all --theme both --width 1120,720,1920x1080`,
inspect every contact sheet and relevant full-size image, then `npm run judge -- <look run>`.
Brain/tool changes also require `npm run verify -- --live` in an authorized live environment.
CI enforces repository contracts and rendered checks on every PR; missing secrets are an
explicit SKIP, never live/design acceptance. Use the [PR contract](.github/pull_request_template.md)
for commands, exits, rule IDs, evidence and anything unverified. Details stay in the skill.
