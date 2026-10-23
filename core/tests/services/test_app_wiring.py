"""The services inside the whole core: their methods are the app's, the assistant and the worker
stand on them, and a message from a chat app hands its files on to `agent.ask`. With the fake LLM
every service is offline."""

from types import SimpleNamespace

import pytest

from raphael_core.app import App
from raphael_core.services.attachments import checked

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.fixture
async def app(tmp_path):
    made = App(data_dir=tmp_path, fake_llm=True, load_kit=lambda: SimpleNamespace())
    yield made
    await made.aclose()


async def test_the_services_methods_are_registered(app):
    assert {"services.status", "services.set_key", "services.clear_key"} <= set(app.methods())
    status = await app.call("services.status", {})
    assert status["search"]["service"] == "openrouter"
    await app.call("services.set_key", {"service": "brave", "key": "a-key"})
    status = await app.call("services.status", {})
    brave = next(o for o in status["search"]["options"] if o["id"] == "brave")
    assert brave["has_key"] is True and "a-key" not in str(status)


async def test_the_assistant_and_the_worker_stand_on_the_same_services(app):
    assert app.agent._tools.services is app.services
    assert app.background._tools.services is app.services


async def test_a_photo_from_a_chat_is_asked_about_with_its_file_and_lands_in_the_history(app):
    photo = checked("photo.png", "image/png", PNG)
    text = await app._ask_from_messaging("telegram:42", "", "zh-TW", "telegram", attachments=[photo])
    assert text.strip()
    (turn,) = (await app.call("agent.history.list", {}))["turns"]
    assert turn["question"] == "看這個"
    assert turn["attachments"] == [{"name": "photo.png", "mime": "image/png"}]
    assert [s["tool"] for s in turn["steps"]] == ["read_attachment"]


async def test_an_address_in_a_request_reaches_no_network_with_the_fake_llm(app):
    await app.call(
        "agent.ask",
        {"session_id": "s", "text": "看 https://example.com/", "ui_language": "en", "source": "app"},
    )
    (turn,) = (await app.call("agent.history.list", {}))["turns"]
    (step,) = turn["steps"]
    assert (step["tool"], step["outcome"], step["reason"]) == ("web_read", "error", "network")
