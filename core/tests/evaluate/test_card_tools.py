"""Where the tools behind the secret card and the computer card may be used: a private chat typed at this
computer, and nowhere else. A room, an exchange between members, a quick question, a spoken request and a
message from a phone never get a card, and a connection the owner does not have has none to fill."""

import pytest

from raphael_core.agent import service as agent_service
from raphael_core.agent.harness_tools import HarnessToolError
from raphael_core.agent.ledger import Ledger
from raphael_core.agent.service import RESUMED, TYPED, _Ask
from raphael_core.evaluate.guardian import Budget
from raphael_core.evaluate.guardian_driver import RpcDriver
from raphael_core.evaluate.secret_fixture import SERVER_NAME, SecretServer
from raphael_core.integrations.config import ServerConfig

from test_guardian import FakeBrains
from test_guardian_scenarios import FakeClient


@pytest.fixture
async def driver(tmp_path):
    driver = RpcDriver(tmp_path, "fake/model", FakeClient([]), Budget(10), brains=FakeBrains(tmp_path))
    driver.app.integrations._config.add(SecretServer(driver.nonce).config())
    yield driver
    await driver.aclose()


def ask_of(driver, **fields):
    turn = driver.app.agent._history.add(session_id="s", question="q", route="answer", prefix="", text="", status="running",
                                         source="app", member_id=0)
    return _Ask(session_id="s", text="q", language="zh-TW", source=TYPED, turn_id=turn, **fields)


def rows(driver, table):
    return driver.app._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


@pytest.mark.parametrize("fields", [
    {"scope": "room", "room_id": 1},
    {"scope": "exchange", "peer": 1},
    {"quick": True},
    {"source": "voice"},
    {"source": "telegram"},
    {"source": "member"},
])
async def test_a_secret_card_is_never_shown_outside_a_private_typed_chat(driver, fields):
    ask = ask_of(driver)
    for name, value in fields.items():
        setattr(ask, name, value)
    with pytest.raises(HarnessToolError, match="private_chat_only"):
        driver.app.agent._show_secret(ask, {"connection": SERVER_NAME})
    assert rows(driver, "secret_cards") == 0 and ask.waiting_secrets == []


@pytest.mark.parametrize("source", [TYPED, RESUMED])
async def test_a_secret_card_is_shown_in_a_private_typed_chat_and_in_the_chat_a_task_resumed_in(driver, source):
    ask = ask_of(driver)
    ask.source = source
    shown = driver.app.agent._show_secret(ask, {"connection": SERVER_NAME})
    assert shown["card"] == "shown" and shown["secrets"] == "Authorization" and ask.waiting_secrets == [SERVER_NAME]
    assert rows(driver, "secret_cards") == 1
    # The model is told the names it gave and nothing of the vault or the key.
    assert "gsk-" not in str(shown) and "vault" in shown["note"]


async def test_a_secret_card_names_only_a_connection_the_owner_has_and_a_secret_it_declares(driver):
    agent = driver.app.agent
    for arguments, kind in (({"connection": "nothing"}, "unknown_connection"), ({"connection": ""}, None),
                            ({"connection": SERVER_NAME, "secret": "Cookie"}, "unknown_secret"),
                            ({"connection": "connectors:composio:key"}, "unknown_connection"),
                            ({"connection": 5}, None)):
        ask = ask_of(driver)
        with pytest.raises((HarnessToolError, ValueError)) as raised:
            agent._show_secret(ask, arguments)
        assert kind is None or str(raised.value) == kind
    # A connection whose secret is in the vault already has no card.
    driver.app.vault.set("guardian-weather:header:Authorization", "kept")
    ask = ask_of(driver)
    assert agent._show_secret(ask, {"connection": SERVER_NAME})["missing"] == 0
    assert rows(driver, "secret_cards") == 0 and ask.waiting_secrets == []


async def test_a_managed_connection_gets_no_secret_card(driver):
    driver.app.integrations._config.add(ServerConfig("google", "Google (private)", "http", url="https://example.invalid/mcp",
                                                    managed_by="google", headers=["x-api-key"]))
    with pytest.raises(HarnessToolError, match="unknown_connection"):
        driver.app.agent._show_secret(ask_of(driver), {"connection": "Google (private)"})


@pytest.mark.parametrize("fields,expected", [
    ({}, True),
    ({"source": RESUMED}, True),
    ({"source": "voice"}, False),
    ({"source": "telegram"}, False),
    ({"quick": True}, False),
    ({"peer": 1, "scope": "exchange"}, False),
    ({"scope": "room", "room_id": 1}, False),
])
async def test_the_owners_turn_at_the_computer_is_offered_only_to_a_private_typed_chat(driver, monkeypatch, fields, expected):
    agent = driver.app.agent
    seen = {}

    class Stop(Exception):
        pass

    class Recorder:
        def __init__(self, **kwargs):
            seen.update(kwargs)
            raise Stop

    monkeypatch.setattr(agent_service, "ComputerRun", Recorder)
    ask = ask_of(driver)
    for name, value in fields.items():
        setattr(ask, name, value)
    ask.ledger = Ledger(agent._spend)
    if ask.source not in agent_service._AT_THIS_COMPUTER:
        # A request from elsewhere cannot operate this computer at all.
        result = await agent._operate(ask, agent._read_settings(), ask.ledger, narration=object(), request="x")
        assert result["status"] == "done" and not seen
        return
    with pytest.raises(Stop):
        await agent._operate(ask, agent._read_settings(), ask.ledger, narration=object(), request="x")
    assert seen["handback"] is expected
    # A run without a narrated turn around it (the spoken route) never pauses either.
    seen.clear()
    with pytest.raises(Stop):
        await agent._operate(ask, agent._read_settings(), ask.ledger, request="x")
    assert seen["handback"] is False
