"""Fixtures for the tests of Raphael's memory: a memory folder in the test's own temporary folder,
a local clock that the test moves, and a model that answers from a queue."""

import inspect
from datetime import datetime, timedelta, timezone

import pytest

from raphael_core.agent.history import AgentHistory
from raphael_core.brain.openrouter import Completion
from raphael_core.memory.store import MemoryStore

TZ = timezone(timedelta(hours=8))
START = datetime(2026, 10, 22, 21, 40, 0, tzinfo=TZ)
START_MS = int(START.timestamp() * 1000)


class Clock:
    """The local time (`now()`), the wall clock in milliseconds (`ms()`) and a monotonic clock in
    seconds (`mono()`), all moved together by `advance`."""

    def __init__(self, at=START):
        self.at = at
        self.seconds = 1000.0

    def now(self):
        return self.at

    def ms(self):
        return int(self.at.timestamp() * 1000)

    def mono(self):
        return self.seconds

    def advance(self, **delta):
        step = timedelta(**delta)
        self.at += step
        self.seconds += step.total_seconds()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store(tmp_path, clock):
    return MemoryStore(tmp_path / "memory", now=clock.now)


@pytest.fixture
def history(conn, clock):
    return AgentHistory(conn, clock.ms)


def completion(data, cost=0.0004, model="openai/gpt-6-luna"):
    return Completion(
        data=data, model=model, cost_usd=cost, prompt_tokens=900, completion_tokens=60,
        latency_ms=800,
    )  # fmt: skip


class FakeModel:
    """complete_json answers from `replies`, first first: an exception is raised, a function is
    called with the call's arguments. Every call is kept in `calls`. A call with nothing queued
    fails the test."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    async def complete_json(self, **kwargs):
        self.calls.append(kwargs)
        assert self.replies, "an unexpected model call"
        answer = self.replies.pop(0)
        if callable(answer):
            answer = answer(**kwargs)
            if inspect.isawaitable(answer):
                answer = await answer
        if isinstance(answer, BaseException):
            raise answer
        return answer if isinstance(answer, Completion) else completion(answer)


def add_turn(history, question, text="好。", *, route="chat", status="done", detail=None):
    return history.add(
        session_id="s", question=question, route=route, prefix="答", text=text, detail=detail,
        status=status,
    )  # fmt: skip
