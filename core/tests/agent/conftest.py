"""Fixtures and helpers shared by the voice assistant's tests."""

import asyncio
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


class FakeOpenRouter:
    """Stands in for the OpenRouter client, which the assistant uses three ways.

    The answers are queued by kind (`decisions`, `replies` for complete_json, `texts` for
    chat_text); each call takes the first of its queue. An answer that is an exception is raised,
    and one that is a function is called with the keyword arguments of the call. A call with
    nothing queued fails the test. `delay` is how long each call takes, in seconds, and the
    arguments of every call are kept in `decide_calls`, `json_calls` and `text_calls`.
    """

    def __init__(self):
        self.decisions = []
        self.replies = []
        self.texts = []
        self.decide_calls = []
        self.json_calls = []
        self.text_calls = []
        self.delay = 0.0

    async def _answer(self, queue, calls, kwargs, kind):
        calls.append(kwargs)
        if self.delay:
            await asyncio.sleep(self.delay)
        assert queue, f"an unexpected {kind} call"
        answer = queue.pop(0)
        if callable(answer):
            answer = answer(**kwargs)
        if isinstance(answer, BaseException):
            raise answer
        return answer

    async def decide(self, **kwargs):
        return await self._answer(self.decisions, self.decide_calls, kwargs, "decide")

    async def complete_json(self, **kwargs):
        return await self._answer(self.replies, self.json_calls, kwargs, "complete_json")

    async def chat_text(self, **kwargs):
        return await self._answer(self.texts, self.text_calls, kwargs, "chat_text")

    @property
    def calls(self):
        return len(self.decide_calls) + len(self.json_calls) + len(self.text_calls)


@pytest.fixture
def client():
    return FakeOpenRouter()
