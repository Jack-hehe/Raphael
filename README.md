# Raphael

Speak anywhere you can type: Raphael turns your voice into clean text and answers when you ask.

## Features

- **Dictation in any app.** Press a key, speak, and the text appears where your cursor is.
- **Clean text.** Filler words and false starts are dropped and punctuation is added, so "let's meet at two… no, three" becomes "Let's meet at three."
- **A dictionary that learns.** Names and jargon keep your spelling, and corrections you make after a dictation are remembered.
- **Cloud or on-device speech recognition.** Use a cloud model through OpenRouter or a free local model.
- **A voice assistant.** Ask a question, open an app or a website, control volume and media, or hand over a task that takes several steps.
- **Connected services.** Sign in to services such as Notion, Linear and Todoist so the assistant can work with them.
- **Multilingual.** Dictation in Chinese, Cantonese, English, Japanese and Korean; the interface in English, Traditional Chinese and Japanese.

## Architecture

```text
UI (TypeScript)        app/src         main window, settings, overlay
  │ Tauri commands and events
Shell (Rust, Tauri 2)  app/src-tauri   hotkeys, paste, tray
  │ JSON-RPC over stdio
Core (Python)          core            audio, speech recognition, cleanup, assistant
```

## Building from source

Requirements: Windows 10 or 11 (x64), [Node.js](https://nodejs.org) 22.12+, [Rust](https://rustup.rs) 1.98, [uv](https://docs.astral.sh/uv/) and the MSVC C++ build tools. Cleanup, cloud recognition and the assistant need an [OpenRouter](https://openrouter.ai) API key.

```powershell
git clone https://github.com/Jack-hehe/Raphael.git
cd Raphael\core
uv sync --locked --dev
cd ..\app
npm ci
npm run tauri -- dev
```

Development builds run as a separate app next to an installed release: they keep their settings, database, logs and webview data in `Raphael Dev` folders, and they share only the downloaded speech models. Set `RAPHAEL_DATA_DIR` to point a build at another data folder.

Run the tests:

```powershell
# app/
npm test
# core/
$env:PYTHON_KEYRING_BACKEND = "keyring.backends.fail.Keyring"
uv run --locked pytest -q
# app/src-tauri/
cargo test --locked
```

## License

[MIT](LICENSE). Raphael builds on [Tauri](https://tauri.app), [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx), [OpenCC](https://github.com/BYVoid/OpenCC) and [handy-keys](https://github.com/handy-computer/handy-keys), and its paste code is adapted from [Handy](https://github.com/cjpais/Handy).
