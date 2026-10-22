"""Fixtures for the background worker's tests: a worker that lives in the test (a `Worker` joined to
the host by two queues, so no process starts), the plugins as the window would set them up, and a
model that plays the replies it is told to. The database fixture `conn` is the whole suite's.

No test here spends money, presses a key or touches the network.
"""

import asyncio
import inspect
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from mcp import Client

from raphael_core.agent.gate import ToolGate
from raphael_core.agent.policy import Tier
from raphael_core.agent.tools import Tools
from raphael_core.background.host import WorkerHost
from raphael_core.background.run import RunLimits
from raphael_core.background.service import BackgroundService
from raphael_core.brain.openrouter import ChatToolCall, ChatTurn, TextCompletion
from raphael_core.integrations.client import McpConnector
from raphael_core.integrations.service import IntegrationsService
from raphael_core.integrations.vault import MemoryVault
from raphael_core.router import Router as RpcRouter
from raphael_core.worker.channel import Peer
from raphael_core.worker.runner import Worker

# The fake servers of the plugins' own tests (tests/integrations/fake_server.py).
from integrations import fake_server  # isort: skip

NOW_MS = int(datetime(2026, 10, 21, 6, 5, tzinfo=timezone.utc).timestamp() * 1000)
LOCAL_NOW = datetime(2026, 10, 21, 14, 5, 9, tzinfo=timezone(timedelta(hours=8)))
GEOCODING = {"results": [{"name": "Taipei", "latitude": 25.04, "longitude": 121.53}]}
FORECAST = {
    "current": {"temperature_2m": 24.1, "weather_code": 2},
    "daily": {
        "temperature_2m_max": [27.3, 26.0],
        "temperature_2m_min": [21.0, 20.5],
        "precipitation_probability_max": [40, 70],
        "weather_code": [3, 61],
    },
}

TASK_TEXT = "PRIVATE-TASK-TEXT look things up for me"
ARGUMENT = "PRIVATE-ARGUMENT"
RESULT = "found PRIVATE-ARGUMENT"  # what the fake server's `look` returns for it


async def until(predicate, timeout=5.0):
    """Wait until `predicate()` is true; a test that waits for ever is a failed test."""
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, "timed out waiting for a condition"
        await asyncio.sleep(0.005)


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


async def _lines(queue: asyncio.Queue):
    while (line := await queue.get()) is not None:
        yield line


class LoopbackLink:
    """A worker that runs in this event loop: the host's peer on one end of two queues, a `Worker`
    on the other. `kill` is the worker dying."""

    def __init__(self):
        self.to_worker: asyncio.Queue = asyncio.Queue()
        self.to_host: asyncio.Queue = asyncio.Queue()
        self.peer = Peer(lambda line: self.to_worker.put_nowait(line))
        self.worker_peer = Peer(lambda line: self.to_host.put_nowait(line))
        self.worker = Worker(self.worker_peer)
        self.killed = False
        self._tasks = [
            asyncio.ensure_future(self.peer.serve(_lines(self.to_host))),
            asyncio.ensure_future(self.worker_peer.serve(_lines(self.to_worker))),
        ]

    async def stop(self):
        self.kill()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    def kill(self):
        self.killed = True
        self.to_worker.put_nowait(None)
        self.to_host.put_nowait(None)


class Links:
    """`connect` for a WorkerHost: makes loopback workers and keeps them."""

    def __init__(self):
        self.made: list[LoopbackLink] = []

    async def __call__(self):
        link = LoopbackLink()
        self.made.append(link)
        return link

    @property
    def last(self) -> LoopbackLink:
        return self.made[-1]


def call(name, arguments, id=None):
    import json

    raw = json.dumps(arguments)
    return ChatToolCall(id or f"call_{name}_{raw}"[:60], name, arguments, raw)


def turn(content="", calls=(), cost=0.001, model="fake/model", usage_known=True):
    return ChatTurn(content, list(calls), "stop", model, cost, 100, 10, 50, usage_known)


def report(text="All looked up.", prefix="告", detail="", cost=0.001):
    import json

    return turn(json.dumps({"prefix": prefix, "text": text, "detail": detail}), cost=cost)


class FakeModel:
    """Stands in for the OpenRouter client. The replies of `chat_tools` are queued; one that is an
    exception is raised, one that is a function is called with the keyword arguments (and awaited if
    it is a coroutine function). The arguments of every call are kept, with the conversation as it
    was at the time. `chat_text` answers a web search."""

    def __init__(self):
        self.replies = []
        self.chat_calls = []
        self.text_calls = []
        self.search_text = "Searched."
        self.search_cost = 0.01

    async def chat_tools(self, **kwargs):
        import copy

        self.chat_calls.append({**kwargs, "messages": copy.deepcopy(kwargs["messages"])})
        assert self.replies, "an unexpected model call"
        answer = self.replies.pop(0)
        if callable(answer):
            answer = answer(**kwargs)
            if inspect.isawaitable(answer):
                answer = await answer
        if isinstance(answer, BaseException):
            raise answer
        return answer

    async def chat_text(self, **kwargs):
        self.text_calls.append(kwargs)
        return TextCompletion(
            text=self.search_text, model="fake/search", cost_usd=self.search_cost,
            prompt_tokens=10, completion_tokens=5, latency_ms=10, sources=[],
        )  # fmt: skip


class Meteo:
    """Open-Meteo, as an httpx client."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.http = httpx.AsyncClient(transport=httpx.MockTransport(self._handle))

    async def _handle(self, request):
        self.requests.append(request)
        body = GEOCODING if request.url.host.startswith("geocoding") else FORECAST
        return httpx.Response(200, json=body)


class Servers:
    """The opener of the plugins: a server is in-process, found by its name."""

    def __init__(self):
        self.by_name = {}
        self.opened = 0  # connections made
        self.active = 0  # connections open now

    def __call__(self, server, secrets):
        return self._open(server)

    @asynccontextmanager
    async def _open(self, server):
        async with Client(self.by_name[server.name]) as client:
            self.opened += 1
            self.active += 1
            try:
                yield client
            finally:
                self.active -= 1


class StrictHub(IntegrationsService):
    """A hub whose tools can have a tier above the one of a tool that is on, to test what a task is
    kept from (a tool that would have to ask: the worker cannot)."""

    strict: dict[str, Tier]

    def _tier_for(self, tool):
        return self.strict.get(tool.name, super()._tier_for(tool))


class Plugins:
    """The plugins as the window would set them up, for a test to connect servers to."""

    def __init__(self, conn, directory):
        self.vault = MemoryVault()
        self.servers = Servers()
        connector = McpConnector(self.vault, self.servers)
        self.hub = StrictHub(conn, directory, self.vault, connector=connector, call_timeout_s=3.0)
        self.hub.strict = {}
        self.rpc = RpcRouter()
        self.hub.register(self.rpc)

    async def connect(self, name="Notes", **only):
        """Connect a server, read its tools and leave on the tools named (one given as None is
        off too) and no others; returns its id. Every tool of a new server is on, so the rest
        are turned off here."""
        self.servers.by_name[name] = fake_server.build()
        added = await self.rpc.call(
            "integrations.add", {"name": name, "transport": "stdio", "command": "unused"}
        )
        server_id = added["server"]["id"]
        await self.rpc.call("integrations.refresh", {"id": server_id})
        tools = (await self.rpc.call("integrations.tools", {"id": server_id}))["tools"]
        for tool in tools:
            await self.set_on(server_id, tool["name"], only.get(tool["name"]) is not None)
        # a tier above the one of a tool that is on: the tool would have to ask
        self.hub.strict.update({tool: Tier[tier] for tool, tier in only.items() if tier in ("T2", "T3")})
        return server_id

    async def set_on(self, server_id, tool, on):
        await self.rpc.call(
            "integrations.set_tool_enabled", {"id": server_id, "tool": tool, "enabled": on}
        )

    def audit(self):
        """Every row of the audit, oldest first: (server, tool, tier, approver, outcome, task_id)."""
        return [
            (e.server, e.tool, e.tier, e.approver, e.outcome, e.task_id)
            for e in reversed(self.hub._audit.recent(200))
        ]


@pytest.fixture(autouse=True)
def calls():
    """What the fake servers' tools were asked to do, by tool name; empty at the start."""
    fake_server.CALLS.clear()
    yield fake_server.CALLS
    fake_server.CALLS.clear()


@pytest.fixture
def plugins(conn, tmp_path):
    return Plugins(conn, tmp_path)


class Rig:
    """A background service with a worker in the test, a model that plays what it is told to, and
    the plugins; `notifications` is what the app would be sent."""

    def __init__(self, conn, plugins, *, limits=RunLimits(), host=None, links=None):
        self.conn = conn
        self.plugins = plugins
        self.client = FakeModel()
        self.clock = Clock()
        self.wall = Clock(NOW_MS)
        self.meteo = Meteo()
        self.notifications: list[tuple[str, dict]] = []
        self.links = links if links is not None else Links()
        self.gate = ToolGate(plugins.hub, conn=conn, now_ms=self.wall)
        self.tools = Tools(self.client, http=self.meteo.http, now=lambda: LOCAL_NOW)
        self.host = host if host is not None else WorkerHost(connect=self.links, grace_s=0.3)
        self.service = BackgroundService(
            conn,
            self.client,
            self.gate,
            self.tools,
            notify=lambda method, params: self.notifications.append((method, params)),
            host=self.host,
            limits=limits,
            now_ms=self.wall,
            tz=timezone.utc,
        )
        self.rpc = RpcRouter()
        self.service.register(self.rpc)

    def submit(self, text=TASK_TEXT, *, language="en", session="s1", model="fake/model"):
        return self.service.submit(session_id=session, text=text, language=language, model=model)

    def reports(self):
        return [params for method, params in self.notifications if method == "worker.report"]

    def changes(self):
        return [params for method, params in self.notifications if method == "worker.changed"]

    async def wait_for_reports(self, count=1, timeout=10.0):
        await until(lambda: len(self.reports()) >= count, timeout)
        return self.reports()

    def task(self, task_id):
        return self.service._store.get(task_id)

    def steps(self, task_id):
        return self.service._store.steps(task_id)

    async def close(self):
        await self.service.aclose()
        await self.meteo.http.aclose()


@pytest.fixture
async def make_rig(conn, plugins):
    rigs = []

    def make(**kwargs):
        rig = Rig(conn, plugins, **kwargs)
        rigs.append(rig)
        return rig

    yield make
    for rig in rigs:
        await rig.close()
