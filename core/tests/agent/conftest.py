"""Fixtures and helpers shared by the voice assistant's tests."""

import sqlite3
import threading
from collections.abc import Iterator

import pytest

from raphael_core.brain import db


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = db.connect(":memory:")
    yield connection
    connection.close()


class FakeActions:
    """A device that records what it was asked, and on which thread.

    `calls` holds (name, *arguments); `threads` the thread each call ran on. `apps` is what the
    Start menu lists (`list_start_apps` counts its calls in `listed`). A name in `fail` makes that
    action raise the error it maps to.
    """

    def __init__(self, apps=()):
        self.apps = list(apps)
        self.calls = []
        self.threads = []
        self.listed = 0
        self.fail = {}

    def _do(self, name, *args):
        self.calls.append((name, *args))
        self.threads.append(threading.get_ident())
        if name in self.fail:
            raise self.fail[name]

    def open_url(self, url):
        self._do("open_url", url)

    def press_key(self, name, times=1):
        self._do("press_key", name, times)

    def lock_screen(self):
        self._do("lock_screen")

    def list_start_apps(self):
        self.listed += 1
        self._do("list_start_apps")
        return list(self.apps)

    def launch_app(self, app_id):
        self._do("launch_app", app_id)

    def copy_to_clipboard(self, text):
        self._do("copy_to_clipboard", text)


@pytest.fixture
def actions():
    return FakeActions()
