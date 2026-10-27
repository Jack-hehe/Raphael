"""S16 through production RPC, loops and authenticated MCP, with honest fake brains on both sides: each says only
the codes it can see, so a quick question that lands in, reads or answers for the formal chat shows in what it says."""
import re

import pytest

from raphael_core.agent.cli.drivers import Event
from raphael_core.agent.conversations import Conversations
from raphael_core.agent.history import AgentHistory
from raphael_core.agent.service import AgentService
from raphael_core.brain.openrouter import ChatTurn
from raphael_core.evaluate.guardian import Budget
from raphael_core.evaluate.guardian_driver import RpcDriver
from raphael_core.evaluate.guardian_registry import inventory
from raphael_core.evaluate.guardian_scenarios import cells
from raphael_core.evaluate.guardian_suite import run_cell

from agent.conftest import decision
from test_guardian import FakeBrains
from test_guardian_scenarios import FakeClient

CODE = re.compile(r"\b(?:QK|FM)-[0-9a-f]+\b")
S16 = next(cell for cell in cells() if cell.id == "S16")
API, CLI = "fake/quick-model", "cli:codex:owner-model?effort=high"


def honest(system, user):
    """Repeat a code it is told; asked for one, name only those its own context shows."""
    question = user.split("<question>", 1)[1].split("</question>", 1)[0] if "<question>" in user else user
    if told := CODE.search(question):
        return f"收到，本次代號是 {told.group()}。"
    kind = "FM" if "正式聊天的代號" in question else "QK"
    seen = [code for code in dict.fromkeys(CODE.findall(system + user.replace(question, ""))) if code.startswith(kind)]
    return "代號是 " + "、".join(seen) + "。" if seen else "我不知道。"


class HonestBrain(FakeClient):
    def __init__(self):
        super().__init__([])

    async def decide(self, **kwargs):
        return decision("unclear")

    async def chat_tools(self, **kwargs):
        self.requests.append(kwargs)
        messages = kwargs["messages"]
        user = "\n".join(str(message.get("content") or "") for message in messages[1:])
        return ChatTurn(honest(str(messages[0]["content"]), user), [], kwargs["model"], .001, 10, 10, 1)


def honest_cli(monkeypatch, sessions=None):
    class Driver:
        def __init__(self, name, command, *, model, **kwargs):
            self.model = model

        async def run(self, *, system, user, session, **kwargs):
            if sessions is not None:
                sessions.append(session)
            yield Event("model", self.model)
            yield Event("session", session or f"thread-{id(self)}")
            yield Event("final", honest(system, user))

        async def interrupt(self):
            pass
    monkeypatch.setattr("raphael_core.agent.cli.loop.Driver", Driver)


def brains_for(brain):
    """The run's own model asks the quick questions; the other brain's answers the formal chat."""
    return (API, CLI) if brain == "A" else (CLI, API)


@pytest.mark.parametrize("brain", ["A", "C"])
async def test_s16_passes_on_both_brains_through_the_suite(tmp_path, monkeypatch, brain):
    sessions = []
    honest_cli(monkeypatch, sessions)
    model, contrast = brains_for(brain)
    result = await run_cell(S16, brain, model, tmp_path, client=HonestBrain(), brains=FakeBrains(tmp_path),
                            budget=Budget(40), seconds=30, run_id="offline-s16", head="test-head",
                            registry=await inventory(), contrast=contrast)
    assert result["status"] == "PASS", result
    assert result["assertions"] == list(S16.assertions)
    assert result["actual_model"] == (model if brain == "A" else "owner-model")
    assert result["settings"] == {"quick_model": model.partition("?effort=")[0],
                                  "quick_effort": "high" if brain == "C" else "low", "agent_model": contrast}
    # The CLI side (quick on C, formal on A) never resumes a session in a quick turn; formal resumes its own.
    assert sessions[0] is None and (brain == "C" or sessions[-1] is not None)


async def test_s16_passes_on_the_codex_brain_alone_with_a_second_codex_model(tmp_path, monkeypatch):
    # The owner keeps conversation tests on the Codex quota: live, a second Codex model on the formal side failed
    # as a changed model because the suite expected the run's own model in every CLI turn.
    honest_cli(monkeypatch)
    formal = "cli:codex:formal-model?effort=low"
    result = await run_cell(S16, "C", CLI, tmp_path, client=HonestBrain(), brains=FakeBrains(tmp_path),
                            budget=Budget(40), seconds=30, run_id="offline-s16", head="test-head",
                            registry=await inventory(), contrast=formal)
    assert result["status"] == "PASS", result
    assert result["settings"]["agent_model"] == formal


async def run_s16(tmp_path, brain="A"):
    model, contrast = brains_for(brain)
    driver = RpcDriver(tmp_path, model, HonestBrain(), Budget(40), brains=FakeBrains(tmp_path), contrast=contrast)
    try:
        reasons, _ = await driver.execute(S16)
        return reasons
    finally:
        await driver.aclose()


async def test_a_quick_question_written_to_the_formal_chat_fails(tmp_path, monkeypatch):
    honest_cli(monkeypatch)
    monkeypatch.setattr(Conversations, "destination", lambda self, member, conversation, *, quick: self.resolve(member, conversation))
    assert {"quick_destination", "formal_unchanged"} <= set(await run_s16(tmp_path))


async def test_a_quick_question_answered_by_the_formal_model_fails(tmp_path, monkeypatch):
    honest_cli(monkeypatch)
    formal_settings = AgentService._read_settings
    monkeypatch.setattr(AgentService, "_read_settings", lambda self, speaker, conversation_id=None: formal_settings(self, speaker))
    assert "quick_actual_model" in await run_s16(tmp_path)


async def test_a_quick_question_that_leaves_a_row_in_the_formal_chat_fails(tmp_path, monkeypatch):
    honest_cli(monkeypatch)
    add = AgentHistory.add

    def mirrored(self, **kwargs):
        turn = add(self, **kwargs)
        conversations = Conversations(self._conn)
        if kwargs.get("conversation_id") == conversations.quick():
            add(self, **{**kwargs, "conversation_id": conversations.main(0)})
        return turn
    monkeypatch.setattr(AgentHistory, "add", mirrored)
    reasons = await run_s16(tmp_path)
    assert "formal_unchanged" in reasons and "quick_destination" not in reasons


@pytest.mark.parametrize("brain", ["A", "C"])
async def test_a_quick_question_that_reads_the_formal_chat_fails(tmp_path, monkeypatch, brain):
    honest_cli(monkeypatch)
    conversation_of = AgentService._conversation_of
    monkeypatch.setattr(AgentService, "_conversation_of", lambda self, member_id, conversation_id=None: conversation_of(self, member_id))
    assert "concurrent_scope" in await run_s16(tmp_path, brain)


async def test_a_quick_cli_turn_that_keeps_a_native_session_fails(tmp_path, monkeypatch):
    from raphael_core.agent.cli.loop import CliLoop
    honest_cli(monkeypatch)
    init = CliLoop.__init__
    monkeypatch.setattr(CliLoop, "__init__", lambda self, **kwargs: init(self, **{**kwargs, "resume_session": True}))
    reasons = await run_s16(tmp_path, "C")
    assert "concurrent_scope" in reasons and "quick_destination" not in reasons


async def test_s16_skips_without_the_other_brain_for_the_formal_chat(tmp_path):
    result = await run_cell(S16, "A", API, tmp_path, client=HonestBrain(), brains=FakeBrains(tmp_path, installed=False),
                            budget=Budget(40), seconds=5, run_id="run", head="head", registry=await inventory(), contrast=CLI)
    assert (result["status"], result["reason"]) == ("SKIP", "formal contrast brain unavailable")
