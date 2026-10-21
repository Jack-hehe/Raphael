"""`python -m raphael_core`: the core, speaking JSON-RPC on stdin and stdout.

The Tauri app runs this as a child process. Environment:

- RAPHAEL_DATA_DIR: where the database lives (and the speech models, unless RAPHAEL_MODELS_DIR
  says otherwise); the default is Raphael's folder in the user's application data, with the
  models in Raphael's machine-local folder.
- RAPHAEL_FAKE_AUDIO: a WAV file that the recorder plays in real time in place of the microphone.
- RAPHAEL_FAKE_LLM=1: no OpenRouter; the cleaned text is the transcript as it was, at no cost.
  (The end-to-end tests use the last two.)

`--worker` makes this process the background worker instead (`raphael_core.worker.runner`): the core
starts it that way for the tasks it hands over. The branch is the first thing `main` does, before the
app module (OpenCC, ONNX Runtime) is imported, so a worker starts in a fraction of a second and has
none of the core's libraries in it.

stdout is taken for the protocol (`claim_stdout`) before anything native is imported: the app
module brings in OpenCC, and its start-up thread ONNX Runtime and PortAudio. Whatever they write to
fd 1 goes to stderr, with the log.
"""

import logging
import os
import sys
from pathlib import Path

from raphael_core.stdio_server import claim_stdout, run_stdio

# Long enough for numpy to load from a cold disk; past this the core serves without it.
LIBRARIES_TIMEOUT_S = 60.0


def main() -> None:
    if "--worker" in sys.argv[1:]:
        from raphael_core.worker.runner import main as worker_main  # light: no native libraries

        worker_main()
        return
    logging.basicConfig(
        stream=sys.stderr, level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    protocol = claim_stdout()
    from raphael_core import app as app_module  # native libraries: only after the claim

    data_dir = os.environ.get("RAPHAEL_DATA_DIR")
    fake_audio = os.environ.get("RAPHAEL_FAKE_AUDIO")
    app = app_module.build_app(
        data_dir=Path(data_dir) if data_dir else None,
        fake_audio=Path(fake_audio) if fake_audio else None,
        fake_llm=os.environ.get("RAPHAEL_FAKE_LLM") == "1",
    )
    # numpy hangs on Windows if it first loads while stdin is being read: see app.py.
    app.wait_for_libraries(LIBRARIES_TIMEOUT_S)
    # Until stdin closes or core.shutdown is answered; either way the app is closed on the way out.
    run_stdio(app, on_close=app.aclose, output=protocol, stop=app.wait_for_shutdown)
    if app.startup_running:
        # A thread inside ONNX Runtime (loading a model) cannot be stopped, and letting the
        # interpreter tear down around it risks a crash on the way out. The app is closing anyway.
        logging.shutdown()
        os._exit(0)


if __name__ == "__main__":
    main()
