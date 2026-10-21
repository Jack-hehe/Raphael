"""Evaluation tools: command-line programs the user runs on their own recordings.

They pick the speech-to-text engine and the cleanup model by measurement. They are separate
programs, not part of the app: nothing in the app imports this package, and their output goes to the
terminal (stdout) and to files under eval/data, never into the JSON-RPC channel of the core process.
"""

import sys


def use_utf8_output() -> None:
    """Write UTF-8 to stdout and stderr, whatever the console's code page: the messages are
    Traditional Chinese, and when the output is redirected Windows would use the ANSI code page."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")
