"""The messaging service inside the whole core: a message goes through the same entry as a spoken
question, and a report of the background worker reaches the chat that handed the task over."""

import asyncio
from types import SimpleNamespace

import pytest

from raphael_core.app import App


@pytest.fixture
async def app(tmp_path):
    made = App(data_dir=tmp_path, fake_llm=True, load_kit=lambda: SimpleNamespace())
    yield made
    await made.aclose()


async def test_the_telegram_methods_are_registered(app):
    assert "messaging.telegram.status" in app.methods()
    assert (await app.call("messaging.telegram.status", {}))["connected"] is False


async def test_a_message_is_asked_like_a_spoken_question_and_lands_in_the_history(app):
    text = await app._ask_from_messaging("telegram:42", "你好", "zh-TW", "telegram")
    assert text.strip()
    turns = (await app.call("agent.history.list", {}))["turns"]
    assert [t["session_id"] for t in turns] == ["telegram:42"]
    assert turns[0]["question"] == "你好"
    assert turns[0]["source"] == "telegram"


async def test_a_question_that_needs_a_yes_is_refused_not_left_open(app, monkeypatch):
    calls = []
    real = app.call

    async def call(method, params=None):
        calls.append(method)
        if method == "agent.ask":
            return {
                "status": "confirm",
                "turn_id": 0,
                "confirm": {"id": "c1", "prefix": "《問》", "text": "開啟嗎？"},
            }
        return await real(method, params)

    monkeypatch.setattr(app, "call", call)
    text = await app._ask_from_messaging("telegram:42", "open it", "en", "telegram")
    assert "confirmation" in text.lower()
    assert calls == ["agent.ask", "agent.confirm"]


async def test_the_files_of_a_message_go_to_the_assistant_as_the_app_sends_them(app, monkeypatch):
    from raphael_core.services.attachments import checked, from_wire

    sent = []

    async def call(method, params=None):
        sent.append((method, params))
        return {"status": "done", "prefix": "《答》", "text": "a cat"}

    monkeypatch.setattr(app, "call", call)
    photo = checked("photo.jpg", "image/jpeg", b"\xff\xd8\xff\xe0" + b"\x00" * 16)
    pdf = checked("a.pdf", "application/pdf", b"%PDF-1.7\n")
    text = await app._ask_from_messaging("telegram:42", "", "en", "telegram", attachments=[photo, pdf])
    assert text == "《答。a cat》"
    [(method, params)] = sent
    assert method == "agent.ask"
    assert params["text"] == "" and params["source"] == "telegram"
    assert from_wire(params["attachments"]) == [photo, pdf]


async def test_a_message_without_files_asks_as_before(app, monkeypatch):
    sent = []

    async def call(method, params=None):
        sent.append(params)
        return {"status": "done", "prefix": "", "text": "hi"}

    monkeypatch.setattr(app, "call", call)
    await app._ask_from_messaging("telegram:42", "hello", "en", "telegram")
    assert "attachments" not in sent[0]


async def test_the_report_of_a_task_from_telegram_is_sent_to_that_chat(app, monkeypatch):
    sent = []

    async def report(session, text):
        sent.append((session, text))

    monkeypatch.setattr(app.messaging, "report", report)
    store = app.background._store
    task_id = store.create(session_id="telegram:42", language="en", text="do")
    store.finish(task_id, "done", prefix="《告》", text="finished", detail="more", reason=None)
    app._worker_notice("worker.report", {"task_id": task_id})
    await asyncio.sleep(0)
    assert sent == [("telegram:42", "《告。finished》")]


async def test_the_report_of_a_spoken_task_is_not_sent(app, monkeypatch):
    sent = []

    async def report(session, text):
        sent.append(session)

    monkeypatch.setattr(app.messaging, "report", report)
    store = app.background._store
    task_id = store.create(session_id="overlay-1", language="en", text="do")
    store.finish(task_id, "done", prefix="《告》", text="finished", detail=None, reason=None)
    app._worker_notice("worker.report", {"task_id": task_id})
    await asyncio.sleep(0)
    assert sent == []


async def test_stop_with_nothing_running_says_so(app):
    assert await app._stop_from_messaging("telegram:42") is False


async def test_stop_from_a_chat_stops_that_chats_tasks_and_turn_only(app, monkeypatch):
    stopped = []

    async def cancel_session(session):
        stopped.append(("worker", session))
        return False

    monkeypatch.setattr(app.background, "cancel_session", cancel_session)
    monkeypatch.setattr(app.agent, "stop_session", lambda s: stopped.append(("agent", s)) or True)
    assert await app._stop_from_messaging("telegram:42") is True
    assert stopped == [("agent", "telegram:42"), ("worker", "telegram:42")]


async def test_a_voice_message_is_heard_with_the_dictation_engine_and_normalized(app, monkeypatch):
    import numpy as np

    from raphael_core.messaging import voice

    heard = []

    class Engine:
        def transcribe(self, samples, hints=()):
            heard.append((samples.size, list(hints)))
            return "你好"

    class Segmenter:
        def __init__(self, path, max_segment_s):
            self.max = max_segment_s

        def accept(self, samples):
            return [samples]

        def flush(self):
            return []

    monkeypatch.setattr(voice, "decoded_blocks", lambda audio: iter([np.zeros(1600, dtype="float32")]))
    monkeypatch.setattr(app, "_dictation_engine", lambda: Engine())
    app._kit = __import__("concurrent.futures").futures.Future()
    app._kit.set_result(SimpleNamespace(Segmenter=Segmenter))
    await app.call("dictionary.add", {"word": "Raphael"})
    text = await app._transcribe_from_messaging(b"opus", "en")
    assert text == "你好"
    assert heard == [(1600, ["Raphael"])]
