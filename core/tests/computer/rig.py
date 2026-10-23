"""The assistant's RPC methods with a fake model, for the tests of the `operate` route."""

from raphael_core.agent.device import NullActions
from raphael_core.agent.routing import Router as Decider
from raphael_core.agent.service import AgentService
from raphael_core.brain.openrouter import Completion, Decision
from raphael_core.brain.secrets import MemoryKeyStore
from raphael_core.router import Router


class AssistantModel:
    """The model as the whole assistant uses it: Jev's decisions (`decisions`), the JSON answers
    (`replies`) and the run's turns (`chats`: a ChatTurn, an exception, or an async function of the
    call's keyword arguments). The keyword arguments of every chat_tools call are in `chat_calls`."""

    def __init__(self):
        self.decisions = []
        self.replies = []
        self.chats = []
        self.chat_calls = []

    async def decide(self, **kwargs):
        assert self.decisions, "an unexpected decide call"
        return self.decisions.pop(0)

    async def complete_json(self, **kwargs):
        assert self.replies, "an unexpected complete_json call"
        return self.replies.pop(0)

    async def chat_tools(self, **kwargs):
        self.chat_calls.append(kwargs)
        assert self.chats, "an unexpected chat_tools call"
        turn = self.chats.pop(0)
        if callable(turn):
            turn = await turn(**kwargs)
        if isinstance(turn, BaseException):
            raise turn
        return turn


def decision(route, command="none"):
    def choice(name):
        return {"type": "choice", "choice": name, "confidence": 0.93}

    flags = ("needs_weather", "needs_search", "needs_time")
    answers = {
        "route": choice(route),
        "command": choice(command),
        **{name: {"type": "noul", "noul": 0.0} for name in flags},
    }
    return Decision(answers=answers, cost_usd=0.00004, latency_ms=600)


def completion(data):
    return Completion(data=data, model="openai/gpt-6-luna", cost_usd=0.0003, prompt_tokens=300,
                      completion_tokens=20, latency_ms=900)  # fmt: skip


class _Background:
    """A background worker that is never handed anything."""

    def brief_for_assistant(self, limit=3, *, member_id=0):
        return []

    def tasks_for_turns(self, turn_ids):
        return {}

    def prune(self, older_than_ms):
        pass

    def clear(self):
        pass


class AssistantRig:
    """The assistant, with a run on the fake desktop when `computer` is given."""

    def __init__(self, conn, client, computer=None):
        keys = MemoryKeyStore()
        keys.set("sk-or-v1-test")
        self.service = AgentService(
            conn, client, keys, actions=NullActions(), background=_Background(), computer=computer,
            decider=Decider(client, jev_total_s=1.0, jev_first_s=0.6, luna_s=1.0),
        )  # fmt: skip
        self.rpc = Router()
        self.service.register(self.rpc)

    async def call(self, method, params=None):
        return await self.rpc.call(method, params)

    async def ask(self, text, *, session="s1", language="zh-TW"):
        params = {"session_id": session, "text": text, "ui_language": language}
        return await self.call("agent.ask", params)

    async def confirm(self, confirm_id, approved=True):
        return await self.call("agent.confirm", {"id": confirm_id, "approved": approved})

    async def turns(self):
        return (await self.call("agent.history.list", {}))["turns"]
