"""Honest fake brains for the Guardian cells that drive a card in a chat (S05, S15.permission): one for the API loop and one for
the authenticated MCP loop of a CLI brain, both answering from the same `act(user, tools, results)`. Each says only what a
tool returned. The judge of the owner's rules (Jev) answers yes to the rules that `judge_yes` names."""
import json

import httpx

from raphael_core.agent.cli.drivers import Event
from raphael_core.brain.openrouter import ChatToolCall, ChatTurn, Decision

from test_guardian import FakeBrains  # noqa: F401  (re-exported for the cells' tests)
from test_guardian_scenarios import FakeClient

API, CLI = "fake/model", "cli:codex:owner-model?effort=high"


def question_of(user):
    return user.split("<question>", 1)[1].split("</question>", 1)[0] if "<question>" in user else user


class Brain(FakeClient):
    """The model of the API brain. `act(user, tools, results, **behaviour)` is what an honest brain does with one prompt."""

    def __init__(self, act, judge_yes=(), **behaviour):
        super().__init__([])
        self.act, self.judge_yes, self.behaviour = act, tuple(judge_yes), behaviour

    async def chat_tools(self, **kwargs):
        self.requests.append(kwargs)
        messages = kwargs["messages"]
        tools = [tool["function"]["name"] for tool in kwargs["tools"]]
        user = str(messages[1]["content"])
        results = [str(message.get("content")) for message in messages if message.get("role") == "tool"]
        text, calls = self.act(user, tools, results, **self.behaviour)
        return ChatTurn(text, [ChatToolCall(str(n), name, arguments, json.dumps(arguments, ensure_ascii=False))
                               for n, (name, arguments) in enumerate(calls)], kwargs["model"], .001, 10, 10, 1)

    async def decide(self, **kwargs):
        yes = {key: 0.93 if any(words in question["instructions"] for words in self.judge_yes) else 0.0
               for key, question in kwargs["questions"].items()}
        return Decision(answers={key: {"type": "noul", "noul": value} for key, value in yes.items()}, cost_usd=0, latency_ms=0)


def cli_brain(monkeypatch, seen, act, **behaviour):
    """A Codex-like CLI whose child speaks MCP to Raphael's one server with the token it was given."""
    class Driver:
        def __init__(self, name, command, *, model, **kwargs):
            self.model = model

        async def run(self, *, system, user, endpoint, token, session=None, **kwargs):
            seen.append(system + user)
            yield Event("model", self.model)
            yield Event("session", session or f"thread-{id(self)}")
            async with httpx.AsyncClient(headers={"Authorization": "Bearer " + token}, trust_env=False, timeout=30) as http:
                listed = (await http.post(endpoint, json={"jsonrpc": "2.0", "id": 0, "method": "tools/list", "params": {}})).json()
                tools = [tool["name"] for tool in listed["result"]["tools"]]
                results, text = [], "我沒有完成這項工作。"
                for _ in range(12):
                    text, calls = act(user, tools, results, **behaviour)
                    if not calls:
                        break
                    for name, arguments in calls:
                        response = await http.post(endpoint, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                                                   "params": {"name": name, "arguments": arguments}})
                        result = response.json()["result"]["content"][0]["text"]
                        seen.append(result)
                        results.append(result)
            yield Event("final", text)

        async def interrupt(self):
            pass
    monkeypatch.setattr("raphael_core.agent.cli.loop.Driver", Driver)
