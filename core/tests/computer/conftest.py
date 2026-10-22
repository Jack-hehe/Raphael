"""Fakes for the run on the owner's desktop: a shell that answers the bridge from a script, and a
model that answers from a queue. No test here reaches a real desktop, UI Automation or a model."""

import asyncio
import itertools
import json

import pytest

from raphael_core.agent.gate import ToolGate
from raphael_core.agent.ledger import Ledger
from raphael_core.brain.openrouter import ChatToolCall, ChatTurn
from raphael_core.brain.spend import SpendTracker
from raphael_core.computer.bridge import ShellBridge
from raphael_core.computer.run import ComputerRun, RunLimits

from .rig import AssistantModel, AssistantRig

CALC = {"exe": "calculatorapp.exe", "name": "Calculator"}


def element(id, role="Button", name="", depth=1, **extra):
    row = {"id": id, "depth": depth, "role": role, "name": name, "enabled": True,
           "actions": ["invoke"], "rect": [10 * id, 20, 40, 30]}  # fmt: skip
    row.update(extra)
    return row


def calculator(window=7, elements=None, **extra):
    """A look at a calculator window: a display, the digits, a Send button, a password box."""
    rows = elements or [
        element(1, "Window", "計算機", depth=0, actions=[]),
        element(2, "Text", "顯示為 0", value="0", actions=[]),
        element(3, "Button", "七"),
        element(4, "Button", "傳送"),
        element(5, "Edit", "密碼", password=True, value="hunter2", actions=["value"]),
        element(6, "Edit", "訊息", focused=True, actions=["value"]),
    ]
    return {"window": window, "app": CALC, "title": "計算機 - 私人帳本", "elements": rows, **extra}


def windows(*, always=False, title="計算機 - 私人帳本"):
    return {"windows": [{"id": 7, **CALC, "title": title, "foreground": True, "always_allowed": always}]}


class FakeShell:
    """The shell as the bridge sees it. `handlers` maps an operation to what it answers: a dict
    (the result), a str (the kind of a failure), or a function of the arguments returning either.
    An operation with no handler is never answered. Every request and every state are kept, and
    the run each said it was for (`request_runs`, `state_runs`)."""

    def __init__(self):
        self.handlers = {}
        self.requests = []  # (op, args)
        self.states = []  # (phase, line)
        self.request_runs = []
        self.state_runs = []
        self.bridge = ShellBridge(self.notify)
        self._tasks = set()

    def notify(self, method, params):
        if method == "computer.state":
            self.states.append((params["phase"], params["line"]))
            self.state_runs.append(params.get("run"))
            return
        assert method == "computer.request", method
        json.dumps(params)  # it must cross the pipe
        self.requests.append((params["op"], params["args"]))
        self.request_runs.append(params.get("run"))
        handler = self.handlers.get(params["op"])
        if handler is None:
            return
        task = asyncio.get_running_loop().create_task(self._answer(params, handler))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _answer(self, params, handler):
        await asyncio.sleep(0)
        answer = handler(params["args"]) if callable(handler) else handler
        if isinstance(answer, str):
            reply = {"id": params["id"], "ok": False, "error": answer}
        else:
            reply = {"id": params["id"], "ok": True, "result": answer}
        await self.bridge._reply(reply)

    def ops(self):
        return [op for op, _ in self.requests]


@pytest.fixture
def shell():
    return FakeShell()


_ids = itertools.count(1)


def call(tool, /, **arguments):
    return ChatToolCall(id=f"call-{next(_ids)}", name=tool, arguments=arguments,
                        raw_arguments=json.dumps(arguments))  # fmt: skip


def reply(*calls, content="", cost=0.001):
    return ChatTurn(content=content, calls=list(calls), model="openai/gpt-6-luna", cost_usd=cost,
                    prompt_tokens=100, completion_tokens=10, latency_ms=300)  # fmt: skip


class FakeModel:
    """The model of a run: each `chat_tools` takes the next of `turns` (a ChatTurn, an exception,
    or a function of the messages). The messages of every call are kept, copied."""

    def __init__(self, *turns):
        self.turns = list(turns)
        self.calls = []

    async def chat_tools(self, *, messages, tools, **kwargs):
        self.calls.append({"messages": json.loads(json.dumps(messages)), "tools": tools, **kwargs})
        assert self.turns, "an unexpected model call"
        turn = self.turns.pop(0)
        if callable(turn):
            turn = turn(messages)
            if asyncio.iscoroutine(turn):
                turn = await turn
        if isinstance(turn, BaseException):
            raise turn
        return turn


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def make_run(conn, shell):
    """A run of the request "在小算盤按七" against the fake shell, with `model`'s replies."""

    def make(model, *, language="zh-TW", limits=RunLimits(), over_budget=lambda: False,
             open_app=None, clock=None):  # fmt: skip
        async def no_app(name):
            return None

        return ComputerRun(
            session_id="s-1",
            language=language,
            request="在小算盤按七",
            model="openai/gpt-6-luna",
            client=model,
            bridge=shell.bridge,
            gate=ToolGate(None, conn=conn),
            ledger=Ledger(SpendTracker(conn, now_ms=lambda: 1_000)),
            over_budget=over_budget,
            open_app=open_app or no_app,
            limits=limits,
            **({} if clock is None else {"clock": clock}),
        )

    return make


async def drive(run, answers=()):
    """Run `run` to its end, answering its questions in turn with `answers` ((approved, always)
    pairs). Returns the questions it asked and how it ended."""
    answers = list(answers)
    asked = []
    run.start()
    while True:
        event = await asyncio.wait_for(run.next_event(), 5)
        if hasattr(event, "status"):
            return asked, event
        asked.append(event)
        approved, always = answers.pop(0) if answers else (False, False)
        assert run.answer(event.id, approved, always)


# --- the assistant, for the `operate` route ---------------------------------------------------------


@pytest.fixture
def client():
    return AssistantModel()


@pytest.fixture
async def make_rig(conn, client):
    rigs = []

    def make(computer=None):
        rig = AssistantRig(conn, client, computer)
        rigs.append(rig)
        return rig

    yield make
    for rig in rigs:
        await rig.service.aclose()


@pytest.fixture
def rig(make_rig):
    return make_rig()
