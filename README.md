# Raphael

[![CI](https://github.com/Jack-hehe/Raphael/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/Jack-hehe/Raphael/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/Jack-hehe/Raphael)](https://github.com/Jack-hehe/Raphael/releases/latest)

Raphael is a voice assistant for Windows. Press a key and speak, and a clean, punctuated version of what you said appears wherever you were typing.

Dictation is the first milestone. Assistant features come next: carrying out tasks through the services you connect, with your approval before anything sensitive, and spoken replies.

## Features

- **Works where you type.** Press Right Alt to start and stop dictating, or switch to hold-to-talk. Esc cancels. The text is pasted into the app you were using.
- **Keeps what you meant.** Raphael removes filler words, adds punctuation and applies the corrections you make while speaking. A question you dictate stays a question; Raphael doesn't answer it.
- **Learns your vocabulary.** Add names and jargon to a personal dictionary, and Raphael learns new terms from the corrections you make after a paste.
- **Cloud or on-device recognition.** Use cloud speech recognition through OpenRouter (the default), or a downloadable on-device model that keeps audio on your computer.
- **Multilingual.** Choose up to five dictation languages, including Chinese (Taiwan, Hong Kong and Mainland usage), Cantonese, English, Japanese and Korean. The interface is available in English, Traditional Chinese and Japanese.
- **Clear costs.** Review past dictations, track usage and set a monthly spending limit.

## Installation

1. Download `Raphael-<version>-windows-x64-setup.exe` from the [latest release](https://github.com/Jack-hehe/Raphael/releases/latest). Raphael supports Windows 10 and 11 on x64.
2. Run the installer. Everything Raphael needs is included; the installer fetches the WebView2 runtime if your system lacks it.
3. Follow the first-run guide to choose a microphone, download a speech model and add an [OpenRouter](https://openrouter.ai) API key, which is used for cleanup and cloud recognition.

Raphael then runs from the system tray.

> [!NOTE]
> Installers are not code-signed yet, so SmartScreen may warn about an unknown publisher. You can check a download against `SHA256SUMS.txt` from the same release.

## Privacy

- **Your API key and calendar addresses** are kept in Windows Credential Manager, never in files, logs or the database.
- **Your data** (settings, history and dictionary) stays on your computer, in `%APPDATA%\Raphael`.
- **Speech recognition** runs in the cloud by default: the recording and dictionary hints are sent to OpenRouter. Choose on-device recognition in Settings to keep audio on your computer.
- **Cleanup:** with an API key set and budget remaining, Raphael sends the transcript to OpenRouter, together with dictionary hints and replacement rules, your language settings and the name of the app you are dictating into. This also happens with on-device recognition, so Raphael is not fully offline.
- **Correction learning** looks only at the field Raphael just pasted into, for up to 60 seconds. It reads the field's text or, where an app does not expose it, the edits you type there. Password fields are skipped, and you can turn learning off in Settings.
- **No telemetry.** Besides OpenRouter, Raphael connects only to Hugging Face and GitHub to download speech models, and to the calendar addresses you add.

## Architecture

Raphael is a [Tauri 2](https://tauri.app) app with a Python core.

```text
UI (TypeScript, WebView2)      main window, settings, overlay
        │  Tauri commands and events
Desktop shell (Rust)           hotkeys, focus, paste, correction learning, tray
        │  JSON-RPC over stdio
Core (Python 3.12)             audio capture, speech recognition, cleanup,
                               dictionary, history, spending
```

| Directory | Contents |
| --- | --- |
| [`app/src`](app/src) | Main window and overlay (TypeScript, no framework) |
| [`app/src-tauri`](app/src-tauri) | Desktop shell (Rust) |
| [`core`](core) | Python core |
| [`eval`](eval) | Evaluation sets for cleanup and speech recognition |
| [`docs`](docs) | CI, release and manual testing guides |

## Building from source

You need Windows 10 or 11 (x64), [Node.js](https://nodejs.org) 22.12 or later, [Rust](https://rustup.rs) 1.98, [uv](https://docs.astral.sh/uv/) and the MSVC C++ build tools. uv installs the pinned Python version.

```powershell
git clone https://github.com/Jack-hehe/Raphael.git
cd Raphael\core
uv sync --locked --dev
cd ..\app
npm ci
npm run tauri -- dev
```

Development builds run the Python core from the source tree. [docs/windows-release.md](docs/windows-release.md) describes how the installer is built.

## Testing

```powershell
# Frontend (app/)
npm test
npm run build

# Python core (core/); the fail-closed keyring keeps tests away from your real credentials
$env:PYTHON_KEYRING_BACKEND = "keyring.backends.fail.Keyring"
uv run pytest -q -W error

# Desktop shell (app/src-tauri/); needs the frontend build above
cargo test --locked
cargo clippy --locked --all-targets -- -D warnings
```

CI runs these checks on Windows for every push and pull request ([docs/ci.md](docs/ci.md)). Tests that need the downloaded speech models run only with `RAPHAEL_TEST_MODELS=1`. Behaviour that depends on real microphones, input methods and editors is covered by the [manual checklist](docs/windows-manual-checks.md).

## Contributing

Bug reports and suggestions are welcome in [GitHub Issues](https://github.com/Jack-hehe/Raphael/issues).

## License

Raphael is released under the [MIT License](LICENSE). The license covers this repository's code and public assets; it grants no rights to the series' characters, names or artwork.

## Acknowledgements

Raphael builds on [Tauri](https://tauri.app), [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx), [OpenCC](https://github.com/BYVoid/OpenCC) and [handy-keys](https://github.com/handy-computer/handy-keys), and its paste code is adapted from [Handy](https://github.com/cjpais/Handy). Each release includes the third-party licenses in `THIRD_PARTY_NOTICES.txt`.

Raphael is an unofficial fan project inspired by Raphael, the skill from *That Time I Got Reincarnated as a Slime*. It is not affiliated with the rights holders, and this repository contains no artwork, audio or voice models from the series.
