"""Fixtures and helpers shared by the voice assistant's tests (the database fixture `conn` is the
whole suite's)."""

import asyncio
import inspect
import threading

import pytest

from raphael_core.agent.commands import NONE
from raphael_core.background.tasks import Refused
from raphael_core.brain.openrouter import Completion, Decision, ToolCall, ToolPlan


class FakeBackground:
    """A background worker that notes how it is called and carries nothing out. Every assistant has
    a worker (the app always gives it one); this is the tests'. `refuse` makes `submit` refuse with
    that phrase's kind, `fail` makes it raise, and `briefs` is what it tells the assistant."""

    def __init__(self):
        self.submitted = []
        self.attached = []
        self.cleared = 0
        self.refuse = None
        self.fail = None
        self.briefs = []
        self.files = []  # the attachments of each task submitted
        self.members = []  # whose task each one submitted was (0: Raphael's)
        self.asked_by = []  # the speaker as another asked it, for a request from an exchange

    def submit(self, *, session_id, text, language, model, attachments=(), member_id=0, asked_by=None):
        self.members.append(member_id)
        self.asked_by.append(asked_by)
        if self.refuse:
            raise Refused(self.refuse)
        if self.fail:
            raise self.fail
        self.submitted.append((session_id, text, language, model))
        self.files.append(list(attachments))
        return len(self.submitted)

    def brief_for_assistant(self, limit=3, *, member_id=0):
        return list(self.briefs)

    def attach_turn(self, task_id, turn_id):
        self.attached.append((task_id, turn_id))

    def clear(self):
        self.cleared += 1

    async def cancel_session(self, session_id):
        return any(request[0] == session_id for request in self.submitted)

    def tasks_for_turns(self, turn_ids):
        # Each task as the history shows it with the turn that handed it over.
        brief = {"id": 0, "state": "running", "started_at_ms": 5}
        step = {"kind": "tool", "tool": "look", "server": "Notes", "outcome": "ok", "ms": 12,
                "reason": None, "at_ms": 6}  # fmt: skip
        return {
            turn_id: ({**brief, "id": task_id}, [step])
            for task_id, turn_id in self.attached
            if turn_id in turn_ids
        }


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

    def open_settings(self, uri):
        self._do("open_settings", uri)

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
    """Stands in for the OpenRouter client, which the assistant uses four ways.

    The answers are queued by kind (`decisions`, `replies` for complete_json, `texts` for
    chat_text, `plans` for plan_tools, `chats` for chat_tools, which a run on the desktop uses);
    each call takes the first of its queue. An answer that is
    an exception is raised, and one that is a function is called with the keyword arguments of the
    call (and awaited, if it is a coroutine function: a call that hangs is one that sleeps). A call
    with nothing queued fails the test. `delay` is how long each call takes, in seconds, and the
    arguments of every call are kept in `decide_calls`, `json_calls`, `text_calls` and
    `plan_calls`.
    """

    def __init__(self):
        self.decisions = []
        self.replies = []
        self.texts = []
        self.plans = []
        self.chats = []
        self.decide_calls = []
        self.chat_calls = []
        self.json_calls = []
        self.text_calls = []
        self.plan_calls = []
        self.delay = 0.0

    async def _answer(self, queue, calls, kwargs, kind):
        calls.append(kwargs)
        if self.delay:
            await asyncio.sleep(self.delay)
        assert queue, f"an unexpected {kind} call"
        answer = queue.pop(0)
        if callable(answer):
            answer = answer(**kwargs)
            if inspect.isawaitable(answer):
                answer = await answer
        if isinstance(answer, BaseException):
            raise answer
        return answer

    async def decide(self, **kwargs):
        return await self._answer(self.decisions, self.decide_calls, kwargs, "decide")

    async def complete_json(self, **kwargs):
        return await self._answer(self.replies, self.json_calls, kwargs, "complete_json")

    async def chat_text(self, **kwargs):
        return await self._answer(self.texts, self.text_calls, kwargs, "chat_text")

    async def plan_tools(self, **kwargs):
        return await self._answer(self.plans, self.plan_calls, kwargs, "plan_tools")

    async def chat_tools(self, **kwargs):
        return await self._answer(self.chats, self.chat_calls, kwargs, "chat_tools")

    @property
    def calls(self):
        return (
            len(self.decide_calls)
            + len(self.json_calls)
            + len(self.text_calls)
            + len(self.plan_calls)
        )


@pytest.fixture
def client():
    return FakeOpenRouter()


# --- what Jev and the language model answer ------------------------------------------------------


def choice(name, confidence=0.93):
    return {
        "type": "choice",
        "choice": name,
        "confidence": confidence,
        "probabilities": {name: confidence},
    }


def noul(value):
    return {"type": "noul", "noul": value}


def answers(
    route="chat", command=NONE, weather=0.01, search=0.02, time=0.03, route_confidence=0.93,
    command_confidence=0.9,
):  # fmt: skip
    return {
        "route": choice(route, route_confidence),
        "command": choice(command, command_confidence),
        "needs_weather": noul(weather),
        "needs_search": noul(search),
        "needs_time": noul(time),
    }


def decision(route="chat", command=NONE, *, cost=0.00004, **kwargs):
    return Decision(answers=answers(route, command, **kwargs), cost_usd=cost, latency_ms=600)


def completion(data, cost=0.0003, model="openai/gpt-6-luna"):
    return Completion(
        data=data, model=model, cost_usd=cost, prompt_tokens=300, completion_tokens=20,
        latency_ms=900,
    )  # fmt: skip


def classification(**overrides):
    data = {
        "route": "chat",
        "command": "none",
        "needs_weather": False,
        "needs_search": False,
        "needs_time": False,
        "target": "",
    }
    return {**data, **overrides}


def tool_plan(*calls, cost=0.0004):
    """What the model picks when it is offered the plugins' tools: `calls` are `(alias, arguments)`."""
    return ToolPlan(
        calls=[ToolCall(name, arguments) for name, arguments in calls],
        model="openai/gpt-6-luna",
        cost_usd=cost,
        prompt_tokens=400,
        completion_tokens=20,
        latency_ms=700,
    )
