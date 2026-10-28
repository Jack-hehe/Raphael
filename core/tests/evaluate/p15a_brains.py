"""One production App with a brain whose every turn is watched, for the cells of apps and skills (P15a): what the
brain was offered, what it was told, and a moment in the middle of a turn at which the owner can do something.

`A` is the API loop; `C` is a Codex-like CLI whose child speaks MCP to Raphael's one authenticated server. Both
are driven by the same `act(user, tools, results)`: it sees the owner's words, the names of the tools it was
offered and what the tools returned so far, and answers with a final text and the calls it makes. A call is made
whether or not the name was offered (a brain remembers a tool from earlier in the conversation), and a call that
no tool answers comes back as an error text, as a real brain sees it."""
import httpx

from raphael_core.agent.cli.drivers import Event
from raphael_core.evaluate.guardian import Budget
from raphael_core.evaluate.guardian_driver import RpcDriver

from card_brains import API, CLI, Brain, FakeBrains

JUDGE_YES = ("Send the synthetic test mail",)


class Stage:
    def __init__(self, tmp_path, monkeypatch, brain, act):
        self.brain, self.act = brain, act
        self.offered, self.prompts = [], []  # per round of the model: the tools it was offered, and what it was told
        self.hook = None  # runs once, while the model is thinking, before its first answer of a turn
        stage = self

        class Api(Brain):
            async def chat_tools(self, **kwargs):
                messages = kwargs["messages"]
                stage.offered.append([tool["function"]["name"] for tool in kwargs["tools"]])
                stage.prompts.append("\n".join(str(message.get("content")) for message in messages))
                if stage.hook is not None and not any(message.get("role") == "tool" for message in messages):
                    work, stage.hook = stage.hook, None
                    await work()
                return await super().chat_tools(**kwargs)

        if brain == "C":
            self._cli(monkeypatch)
        self.client = Api(act, judge_yes=JUDGE_YES)
        self.driver = RpcDriver(tmp_path, CLI if brain == "C" else API, self.client, Budget(80), brains=FakeBrains(tmp_path))
        self.app = self.driver.app

    def _cli(self, monkeypatch):
        stage = self

        class Driver:
            def __init__(self, name, command, *, model, **kwargs):
                self.model = model

            async def run(self, *, system, user, endpoint, token, session=None, **kwargs):
                yield Event("model", self.model)
                yield Event("session", session or f"thread-{id(self)}")
                async with httpx.AsyncClient(headers={"Authorization": "Bearer " + token}, trust_env=False, timeout=30) as http:
                    listed = (await http.post(endpoint, json={"jsonrpc": "2.0", "id": 0, "method": "tools/list", "params": {}})).json()
                    tools = [tool["name"] for tool in listed["result"]["tools"]]
                    stage.offered.append(tools)
                    stage.prompts.append(system + "\n" + user)
                    if stage.hook is not None:
                        work, stage.hook = stage.hook, None
                        await work()
                    results, text = [], "我沒有完成這項工作。"
                    for _ in range(12):
                        text, calls = stage.act(user, tools, results)
                        if not calls:
                            break
                        for name, arguments in calls:
                            body = (await http.post(endpoint, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                                                    "params": {"name": name, "arguments": arguments}})).json()
                            results.append(body["result"]["content"][0]["text"] if "result" in body else "error: " + body["error"]["message"])
                yield Event("final", text)

            async def interrupt(self):
                pass
        monkeypatch.setattr("raphael_core.agent.cli.loop.Driver", Driver)

    def after_listing(self, work):
        """`work` (an async function: the owner's click) happens once, after the brain was told what it may use and before
        it uses any of it."""
        self.hook = work

    async def aclose(self):
        await self.driver.aclose()
