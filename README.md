# Raphael

A Windows dictation assistant built with Tauri, TypeScript and a local Python core.
Press Right Alt to dictate, then paste cleaned text into the focused application.
The app includes history, a personal dictionary, correction learning and a transparent
progress overlay. The current milestone is the initial dictation workflow.

## Development

Use Windows x64, Node.js 22, Rust 1.98, Python 3.12.14 and uv. Windows builds require
the MSVC C++ build tools and WebView2 runtime.

```powershell
cd core
uv sync --locked --dev
cd ../app
npm ci
npm run tauri -- dev
```

Configure the OpenRouter key in the app. Credentials are stored in Windows Credential
Manager, while settings, history and dictionary entries stay in the user's local data
directory. Cloud transcription sends recorded speech to the selected provider; local
transcription uses downloaded models on this computer.

## Checks and builds

Run `npm test` and `npm run build` in `app/`. Run `uv run pytest -q -W error` in `core/`.
Run `cargo test --locked`, `cargo clippy --locked --all-targets -- -D warnings` and
`cargo fmt --check` in `app/src-tauri/`. The patched keyboard listener has a separate
suite: `cargo test --locked --lib --manifest-path vendor/handy-keys/Cargo.toml`.

For a development executable, run `node scripts/tauri.mjs build --debug --no-bundle` in `app/`.
The Python core is currently started from the source checkout; this command does not
produce a self-contained installer.

See [CI coverage](docs/ci.md) and [Windows manual checks](docs/superpowers/phase1-manual-checks.md).
Native focus, microphone, IME and editor behavior require interactive acceptance in
addition to automated tests.

## Private assets

Personal artwork, source recordings, speech models, generated private icons and secrets
are excluded from Git. Builds work without personal artwork using the public fallbacks.
See [private artwork configuration](docs/private-icons.md) for optional local assets.
