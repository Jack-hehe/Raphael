"""Quick questions own one persistent context and use the ordinary public turn stream."""

import json
import asyncio
import sqlite3
from dataclasses import replace

import pytest

from raphael_core.agent.conversations import Conversations
from raphael_core.brain.db import connect
from raphael_core.protocol import RpcError
from .test_service import make_rig  # noqa: F401
from .test_narration import chat
from .conftest import decision


@pytest.mark.parametrize("saved,levels,expected", [("medium", ("low", "high"), "low"), ("none", ("low", "high"), "low"), ("high", (), "none")])
async def test_api_effort_respects_selected_models_supported_levels(make_rig, client, saved, levels, expected):
    from .test_plugin_model import llm
    entry = replace(llm("openai/quick"), reasoning_efforts=levels)
    rig = make_rig(models=lambda: [entry])
    rig.settings.set("quick_model", entry.id)
    rig.settings.set("quick_effort", saved)
    client.chats.append(chat('{"text":"Hello","prefix":"Answer"}'))
    await rig.call("agent.ask", {"session_id": "effort", "text": "Hello", "ui_language": "en",
        "source": "app", "conversation_id": rig.service.conversations.quick()})
    assert client.chat_calls[-1]["reasoning_effort"] == expected


def test_database_migration_preserves_formal_conversations_and_enforces_one_quick(tmp_path):
    from raphael_core.brain import db
    path = tmp_path / "v27.db"
    with sqlite3.connect(path) as raw:
        for version in range(1, 28):
            step = db._MIGRATIONS[version]
            step(raw) if callable(step) else raw.executescript(step)
        raw.execute("UPDATE conversations SET title='Formal' WHERE id=1")
    conn = connect(path)
    try:
        store = Conversations(conn)
        assert store.get(1)["title"] == "Formal" and store.get(1)["kind"] == "standard"
        quick = store.quick()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO conversations(member_id,title,created_at_ms,updated_at_ms,is_default,kind) VALUES(0,'',1,1,0,'quick')")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE conversations SET member_id=1 WHERE id=?", (quick,))
    finally:
        conn.close()


async def test_voice_reuses_quick_context_and_ignores_explicit_formal_thread(make_rig, client):
    notices = []
    rig = make_rig(notify=lambda method, params: notices.append((method, params)))
    formal = rig.service.conversations.main()
    rig.seed("formal secret", "private project", conversation_id=formal)
    for n in range(2):
        client.decisions.append(decision("unclear"))
        client.chats.append(chat('{"progress":"Checking the clock.","text":"Noon","prefix":"Answer"}'))
        result = await rig.call("agent.ask", {"session_id": f"quick-{n}", "text": f"question {n}",
            "ui_language": "en", "conversation_id": formal})
        assert result["status"] == "done"
    quick = rig.service.conversations.quick()
    assert quick != formal
    assert [t.question for t in rig.history.list(conversation_id=quick)] == ["question 1", "question 0"]
    assert "formal secret" not in json.dumps(client.chat_calls)
    assert "question 0" in json.dumps(client.chat_calls[-1])
    assert len(client.decide_calls) == 2
    assert not client.json_calls
    progress = [p for m, p in notices if m == "agent.progress"]
    assert len(progress) == 2
    assert progress[0]["text"] == rig.history.messages([result["turn_id"]])[result["turn_id"]][0]["text"]
    assert (await rig.call("conversations.list"))["conversations"][0]["kind"] == "quick"


@pytest.mark.parametrize("selected,expected", [("", "openai/gpt-6-luna"), ("openai/quick", "openai/quick")])
async def test_model_and_effort_follow_kind_for_voice_and_typed_continuation(make_rig, client, selected, expected):
    rig = make_rig()
    rig.settings.set("quick_model", selected)
    rig.settings.set("quick_effort", "medium")
    quick = rig.service.conversations.quick()
    for source in ("voice", "app"):
        if source == "voice":
            client.decisions.append(decision("unclear"))
        client.chats.append(chat('{"text":"Hello","prefix":"Answer"}'))
        await rig.call("agent.ask", {"session_id": source, "text": "Hello", "ui_language": "en",
            "source": source, "conversation_id": quick})
        assert client.chat_calls[-1]["model"] == expected
        assert client.chat_calls[-1]["reasoning_effort"] == "medium"
    formal = rig.service.conversations.main()
    client.chats.append(chat('{"text":"Hello","prefix":"Answer"}'))
    await rig.call("agent.ask", {"session_id": "formal", "text": "Hello", "ui_language": "en",
        "source": "app", "conversation_id": formal})
    assert client.chat_calls[-1]["reasoning_effort"] == "none"


def test_quick_kind_survives_reopen_and_cannot_be_renamed(tmp_path):
    path = tmp_path / "quick.db"
    with connect(path) as conn:
        quick = Conversations(conn).quick()
    with connect(path) as conn:
        store = Conversations(conn)
        assert store.quick() == quick
        assert store.get(quick)["kind"] == "quick"
        with pytest.raises(RpcError, match="quick"):
            store.rename(quick, "A different purpose")


async def test_overlay_uses_only_recent_quick_turns(make_rig, client):
    rig = make_rig()
    quick = rig.service.conversations.quick()
    for n in range(12):
        rig.seed(f"quick-seed-{n:02}", "Earlier answer", conversation_id=quick)
    client.chats.append(chat('{"text":"Hello","prefix":"Answer"}'))
    await rig.call("agent.ask", {"session_id": "overlay", "text": "Hello", "ui_language": "en", "source": "overlay"})
    prompt = json.dumps(client.chat_calls[-1])
    assert "quick-seed-00" not in prompt
    assert "quick-seed-05" not in prompt
    assert "quick-seed-06" in prompt and "quick-seed-11" in prompt
    assert client.chat_calls[-1]["reasoning_effort"] == "low"


async def test_latency_logs_only_timings_and_waits_for_actual_sound(make_rig, client, caplog):
    class DelayedVoice:
        on_state = None

        def speaks(self):
            return True

        def stop(self):
            pass

        async def say(self, *args, **kwargs):
            self.turn = kwargs["turn_id"]
            # The speech service can return before playback when its wait expires.

    voice = DelayedVoice()
    rig = make_rig(speaker=voice)
    client.decisions.append(decision("unclear"))
    client.chats.append(chat('{"progress":"Public step","prefix":"Answer","text":"PRIVATE_WORD_SENTINEL","say_ja":"はい"}'))
    caplog.set_level("INFO", logger="raphael_core.agent.service")
    result = await rig.call("agent.ask", {"session_id": "timed", "text": "QUESTION_SENTINEL", "ui_language": "en", "transcript_ms": 25})
    records = [r for r in caplog.records if r.msg == "Quick latency ms: %s"]
    trace = json.loads(records[-1].args[0])
    assert trace["speech_start"] is None
    assert 25 <= trace["routing"] <= trace["first_step"] <= trace["final_answer"]
    await asyncio.sleep(0)
    voice.on_state(result["turn_id"], "playing")
    voice.on_state(result["turn_id"], "done")
    records = [r for r in caplog.records if r.msg == "Quick latency ms: %s"]
    assert json.loads(records[-1].args[0])["speech_start"] >= trace["final_answer"]
    assert "SENTINEL" not in " ".join(r.getMessage() for r in records)
    assert not rig.service._speech_traces


@pytest.mark.parametrize("source", ["voice", "app"])
async def test_quick_cli_passes_effort_without_resuming_unbounded_native_history(make_rig, client, tmp_path, monkeypatch, source):
    from raphael_core.agent.cli.discovery import CliBrains
    from raphael_core.agent.cli.drivers import Event
    from raphael_core.agent.cli import loop as production

    calls = []

    class Driver:
        def __init__(self, *args, **kwargs):
            self.effort = kwargs["effort"]

        async def run(self, **kwargs):
            calls.append({**kwargs, "effort": self.effort})
            yield Event("session", "new-native-session")
            yield Event("final", "Quick answer.")

        async def interrupt(self):
            pass

    monkeypatch.setattr(production, "Driver", Driver)
    brains = CliBrains(home=tmp_path, environ={}, commands={"codex": ["never-spawn"]})
    monkeypatch.setattr(brains, "available", lambda *args, **kwargs: True)
    brains._models["codex"] = [{"id": "cli:codex:fast", "reasoning_efforts": ["low", "medium"]}]
    rig = make_rig(cli_brains=brains)
    quick = rig.service.conversations.quick()
    rig.settings.set("quick_model", "cli:codex:fast")
    rig.seed("FORMAL_SENTINEL", "Formal work", conversation_id=rig.service.conversations.main())
    for n in range(12):
        rig.seed(f"quick-seed-{n:02}", "Earlier answer", conversation_id=quick)
    model = "cli:codex:fast?effort=low"
    rig.service._cli_sessions.put(0, model, "unbounded-native-history", quick)
    result = await rig.call("agent.ask", {"session_id": "cli", "text": "Hello", "ui_language": "en", "source": source,
        "conversation_id": quick})
    assert result["text"] == "Quick answer."
    assert calls[0]["session"] is None and calls[0]["effort"] == "low"
    assert "FORMAL_SENTINEL" not in calls[0]["user"] and "quick-seed-00" not in calls[0]["user"]
    assert "quick-seed-11" in calls[0]["user"]
    assert rig.service._cli_sessions.get(0, model, quick) == "unbounded-native-history"
    assert not client.decide_calls and not client.json_calls
