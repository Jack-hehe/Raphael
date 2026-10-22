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
    text = await app._ask_from_messaging("telegram:42", "你好", "zh-TW")
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
    text = await app._ask_from_messaging("telegram:42", "open it", "en")
    assert "confirmation" in text.lower()
    assert calls == ["agent.ask", "agent.confirm"]


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
    assert sent == [("telegram:42", "《告》finished\n\nmore")]


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
