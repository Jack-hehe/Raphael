"""Replay synthetic quick questions through AgentService, recording all execution.

Offline: uv run python -m raphael_core.tools.quick_latency --fake --count 9
Live (owner only): uv run python -m raphael_core.tools.quick_latency --model cli:codex --count 9
API live models use the existing OpenRouter OS credential adapter. Nothing is opened,
scheduled or spoken. Fake times prove instrumentation, not provider performance.
"""

import argparse
import asyncio
import json
import logging
import tempfile
from pathlib import Path
from contextlib import closing
from unittest.mock import patch

import httpx

from raphael_core.agent.cli.loop import CliLoop
from raphael_core.agent.device import NullActions
from raphael_core.agent.narration import NarratedLoop
from raphael_core.agent.service import AgentService
from raphael_core.brain.db import connect
from raphael_core.brain.openrouter import ChatToolCall, ChatTurn, Decision, OpenRouterClient
from raphael_core.brain.secrets import MemoryKeyStore
from raphael_core.evaluate.credentials import key_provider
from raphael_core.evaluate.guardian import Recorder

QUESTIONS = ("Open YouTube.", "What is two plus two?", "What is on my calendar today?")


class RecordingBackground:
    def __init__(self):
        self.submitted = []

    def submit(self, **kwargs):
        # A delegated question is recorded as a task and never run.
        self.submitted.append(kwargs)
        return len(self.submitted)

    def attach_turn(self, *args):
        pass

    def brief_for_assistant(self, *args, **kwargs):
        return []

    def tasks_for_turns(self, *args):
        return {}

    async def cancel_session(self, *args):
        return False


class RecordingVoice:
    on_state = None

    def speaks(self):
        return True

    def stop(self):
        pass

    async def say(self, *args, **kwargs):
        # Marks the same await-first-sound boundary without producing audio.
        if self.on_state:
            self.on_state(kwargs.get("turn_id"), "playing")
            self.on_state(kwargs.get("turn_id"), "done")


class FakeProvider:
    def __init__(self):
        self.calls = []
        self.question = 0
        self.round = 0
        self.routes = 0

    async def decide(self, **kwargs):
        self.routes += 1
        return Decision(answers={
            "route": {"type": "choice", "choice": "unclear", "confidence": 1.0},
            "command": {"type": "choice", "choice": "none", "confidence": 1.0},
            "needs_weather": {"type": "noul", "noul": 0.0},
            "needs_search": {"type": "noul", "noul": 0.0},
            "needs_time": {"type": "noul", "noul": 0.0},
        }, cost_usd=0.0, latency_ms=0)

    async def chat_tools(self, **kwargs):
        self.calls.append(kwargs)
        self.round += 1
        calls = []
        if self.round == 1 and self.question % 3 != 1:
            name, args = (("open", {"target_kind": "url", "target": "https://www.youtube.com/"})
                          if self.question % 3 == 0 else ("routine_list", {}))
            calls = [ChatToolCall("record-only", name, args, json.dumps(args))]
        content = json.dumps({"progress": "Checking the request.", "prefix": "Answer", "text": "Done.", "say_ja": "完了しました。"})
        return ChatTurn(content, calls, kwargs["model"], 0.0, 10, 5, 0)


class RecordingLoop(NarratedLoop):
    recorders = []

    def __init__(self, **kwargs):
        self.recorder = Recorder(kwargs["tools"])
        self.recorders.append(self.recorder)
        super().__init__(**{**kwargs, "execute": self.recorder})


class RecordingCliLoop(CliLoop):
    recorders = []

    def __init__(self, **kwargs):
        self.recorder = Recorder(kwargs["tools"])
        self.recorders.append(self.recorder)
        super().__init__(**{**kwargs, "execute": self.recorder})


class TraceLog(logging.Handler):
    def __init__(self):
        super().__init__()
        self.traces = []

    def emit(self, record):
        if record.msg == "Quick latency ms: %s":
            self.traces.append(json.loads(record.args[0]))


async def replay(args):
    if not 1 <= args.count <= 200:
        raise ValueError("count must be 1 to 200")
    if args.fake and (args.model or args.formal_model).startswith("cli:"):
        raise ValueError("fake replay requires an API model; no subscription CLI is started offline")
    fake = FakeProvider() if args.fake else None
    RecordingLoop.recorders.clear()
    RecordingCliLoop.recorders.clear()
    keys = MemoryKeyStore()
    keys.set("synthetic" if fake else key_provider() or "")
    client = fake or OpenRouterClient(keys.get)
    trace = TraceLog()
    log = logging.getLogger("raphael_core.agent.service")
    log.setLevel(logging.INFO)
    log.addHandler(trace)
    try:
        with tempfile.TemporaryDirectory(prefix="raphael-quick-") as directory, closing(connect(Path(directory) / "replay.db")) as conn:
            # Model traffic uses its own transport; all ordinary lookup HTTP is blocked.
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(503))) as http:
                service = AgentService(conn, client, keys, actions=NullActions(), http=http,
                    background=RecordingBackground(), speaker=RecordingVoice())
                try:
                    service._settings.set("agent_model", args.formal_model)
                    service._settings.set("quick_model", args.model)
                    service._settings.set("quick_effort", args.effort)
                    formal = service.conversations.main()
                    service._history.add(session_id="formal", question="FORMAL_CONTEXT_SENTINEL", route="answer",
                        prefix="", text="Formal project", status="done", conversation_id=formal)
                    quick = service.conversations.quick()
                    for n in range(12):
                        service._history.add(session_id=f"seed-{n}", question=f"QUICK_SEED_{n:02}", route="answer",
                            prefix="", text="Earlier quick answer", status="done", conversation_id=quick)
                    with patch("raphael_core.agent.service.NarratedLoop", RecordingLoop), patch("raphael_core.agent.service.CliLoop", RecordingCliLoop):
                        for index in range(args.count):
                            if fake:
                                fake.question, fake.round = index, 0
                            result = await service._ask({"session_id": f"replay-{index}", "text": QUESTIONS[index % 3],
                                "source": "voice", "destination": "quick", "ui_language": "en", "transcript_ms": 0})
                            turn = service._history.get(result.get("turn_id"))
                            if turn is None or turn.status != "done":
                                raise RuntimeError("replay turn did not complete")
                            if fake:
                                assert "FORMAL_CONTEXT_SENTINEL" not in json.dumps(fake.calls)
                                assert "QUICK_SEED_00" not in json.dumps(fake.calls)
                                assert "QUICK_SEED_11" in json.dumps(fake.calls[0])
                                assert all(call["model"] == (args.model or args.formal_model) for call in fake.calls)
                                assert all(call["reasoning_effort"] == args.effort for call in fake.calls)
                            print(json.dumps({"question_index": index, "stages_ms": trace.traces[-1]}, sort_keys=True))
                    if fake:
                        assert fake.routes == args.count
                        recorders = RecordingLoop.recorders
                        assert len(recorders) == args.count
                        assert not any(recorder.errors for recorder in recorders)
                        recorded = [name for recorder in recorders for name in recorder.calls]
                        assert recorded.count("open") == len(range(0, args.count, 3))
                        assert recorded.count("routine_list") == len(range(2, args.count, 3))
                        assert all(trace.traces[n]["first_step"] is not None for n in range(args.count))
                        print(json.dumps({"fake": True, "turns": args.count, "scope": "quick-only", "model": args.model or args.formal_model,
                            "effort": args.effort, "model_calls": len(fake.calls), "routing_calls": fake.routes, "recorded_tools": recorded}))
                finally:
                    await service.aclose()
    finally:
        log.removeHandler(trace)
        if not fake:
            await client.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fake", action="store_true")
    parser.add_argument("--count", type=int, default=9)
    parser.add_argument("--model", default="")
    parser.add_argument("--formal-model", default="openai/gpt-6-luna")
    parser.add_argument("--effort", choices=("none", "minimal", "low", "medium", "high", "xhigh", "max"), default="low")
    asyncio.run(replay(parser.parse_args()))
