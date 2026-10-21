# Raphael

Raphael is a voice assistant for Windows. Press a key and speak: what you say is cleaned up and typed wherever your cursor is. Hold a second key and Raphael answers questions, opens what you ask for and carries out tasks through the services you connect.

## What it does

- **Dictation anywhere.** Press Right Alt to start and stop, or hold it to talk. The text is pasted into the app you are using, and music or video pauses while you speak. Esc cancels.
- **Clean text.** Filler words and self-corrections are removed and punctuation is added, so "let's meet at two… no, three" becomes "Let's meet at three."
- **Your vocabulary.** A personal dictionary keeps names and jargon spelled your way. Raphael adds the terms you use often and learns from the corrections you make after a paste. Words can also be imported from a CSV or text file.
- **Cloud or on-device recognition.** Speech can be recognised through OpenRouter, or on your computer by one of several free local models, each downloaded only when you choose it.
- **An assistant.** Press Right Alt + Space and ask. Raphael answers from its own knowledge, the weather and web search. It opens any app, website or Windows settings page by name, and controls volume, media playback and the lock screen. Requests that take several steps run in the background, and Raphael reports back when they are done.
- **Connected services.** A store of services with official MCP servers, such as Notion, Linear, Atlassian and Todoist, connects with one sign-in in the browser. Every tool a service offers can be switched on or off.
- **Multilingual.** Dictation in Chinese (Taiwan, Hong Kong and Mainland usage), Cantonese, English, Japanese and Korean. The interface comes in English, Traditional Chinese and Japanese.
- **Local data.** API keys and sign-ins are kept in Windows Credential Manager. Settings, history and the dictionary are kept in `%APPDATA%\Raphael`. Usage is tracked against a monthly spending limit you set.

## How it works

Raphael is a [Tauri 2](https://tauri.app) app with a Python core.

```text
UI (TypeScript, WebView2)      main window, settings, overlay
        │  Tauri commands and events
Desktop shell (Rust)           hotkeys, focus, paste, correction learning,
                               media pause, tray
        │  JSON-RPC over stdio
Core (Python 3.12)             audio capture, speech recognition, cleanup,
                               dictionary, history, assistant, connections,
                               background worker
```

| Directory | Contents |
| --- | --- |
| [`app/src`](app/src) | Main window and overlay (TypeScript, no framework) |
| [`app/public/brand`](app/public/brand) | In-app artwork |
| [`app/src-tauri`](app/src-tauri) | Desktop shell (Rust) |
| [`app/src-tauri/icons`](app/src-tauri/icons) | Application, tray and installer icons, generated from `app-icon.png` |
| [`core`](core) | Python core |

## Building from source

You need Windows 10 or 11 (x64), [Node.js](https://nodejs.org) 22.12 or later, [Rust](https://rustup.rs) 1.98, [uv](https://docs.astral.sh/uv/) and the MSVC C++ build tools. An [OpenRouter](https://openrouter.ai) API key enables cleanup, cloud recognition and the assistant.

```powershell
git clone https://github.com/Jack-hehe/Raphael.git
cd Raphael\core
uv sync --locked --dev
cd ..\app
npm ci
npm run tauri -- dev
```

To build the Windows installer, which bundles a frozen copy of the core, run the following from `core/`:

```powershell
uv sync --locked --group packaging
uv run --locked --no-sync python packaging/build.py
```

Then run this from `app/`:

```powershell
npm ci --ignore-scripts
node scripts/collect-release-notices.mjs
node scripts/tauri.mjs build --config src-tauri/tauri.release.conf.json --bundles nsis --ci -- --locked
```

Tests:

```powershell
# app/
npm test
# core/
$env:PYTHON_KEYRING_BACKEND = "keyring.backends.fail.Keyring"
uv run --locked pytest -q -W error
# app/src-tauri/
cargo test --locked
```

## License

[MIT](LICENSE). Raphael builds on [Tauri](https://tauri.app), [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx), [OpenCC](https://github.com/BYVoid/OpenCC) and [handy-keys](https://github.com/handy-computer/handy-keys). Its paste code is adapted from [Handy](https://github.com/cjpais/Handy).
