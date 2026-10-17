"""`python -m raphael_core`: the core, speaking JSON-RPC on stdin and stdout.

The Tauri app runs this as a child process. Environment:

- RAPHAEL_DATA_DIR: where the database lives (and the speech models, unless RAPHAEL_MODELS_DIR
  says otherwise); the default is Raphael's folder in the user's application data.
- RAPHAEL_FAKE_AUDIO: a WAV file that the recorder plays in real time in place of the microphone.
- RAPHAEL_FAKE_LLM=1: no OpenRouter; the cleaned text is the transcript as it was, at no cost.
  (The end-to-end tests use the last two.)
"""

import logging
import os
import sys
from pathlib import Path

from raphael_core.app import build_app
from raphael_core.stdio_server import run_stdio

# Long enough for numpy to load from a cold disk; past this the core serves without it.
LIBRARIES_TIMEOUT_S = 60.0


def main() -> None:
    logging.basicConfig(
        stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    data_dir = os.environ.get("RAPHAEL_DATA_DIR")
    fake_audio = os.environ.get("RAPHAEL_FAKE_AUDIO")
    app = build_app(
        data_dir=Path(data_dir) if data_dir else None,
        fake_audio=Path(fake_audio) if fake_audio else None,
        fake_llm=os.environ.get("RAPHAEL_FAKE_LLM") == "1",
    )
    # numpy hangs on Windows if it first loads while stdin is being read: see app.py.
    app.wait_for_libraries(LIBRARIES_TIMEOUT_S)
    run_stdio(app, on_close=app.aclose)
    if app.startup_running:
        # A thread inside ONNX Runtime (loading a model) cannot be stopped, and letting the
        # interpreter tear down around it risks a crash on the way out. The app is closing anyway.
        logging.shutdown()
        os._exit(0)


if __name__ == "__main__":
    main()
