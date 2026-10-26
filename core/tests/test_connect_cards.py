"""Connector cards through the production connector RPCs, the real loopback sign-in and an
automatic Google fixture. Nothing reaches a network or a real account."""

import asyncio
import json
import logging

import httpx
import pytest

from raphael_core.agent.history import AgentHistory
from raphael_core.agent.team_context import snapshot
from raphael_core.connectors.apps import APPS
from raphael_core.connectors.cards import CardStore, ConnectorCards
from raphael_core.connectors.service import ConnectorsService
from raphael_core.evaluate.google_fixture import CLIENT_ID, GoogleFixture
from raphael_core.integrations.service import IntegrationsService
from raphael_core.integrations.vault import MemoryVault
from raphael_core.protocol import RpcError
from raphael_core.router import Router

from test_connectors import Platform


class Rig:
    def __init__(self, tmp_path, conn, fixture, *, clock=None, transport=None, browser=None):
        self.conn, self.fixture = conn, fixture
        self.vault = MemoryVault()
        self.hub = IntegrationsService(conn, tmp_path, self.vault)
        self.router = Router()
        self.hub.register(self.router)
        self.connectors = ConnectorsService(tmp_path, self.vault, self.hub, transport=transport or fixture.transport,
                                            open_browser=browser or fixture.browser, sleep=lambda _: asyncio.sleep(0))
        self.connectors.register(self.router)
        self.events = []
        self.resumed = []
        self.now = clock or [1_800_000_000_000]
        self.cards = ConnectorCards(conn, self.router, self.connectors, notify=lambda m, p: self.events.append((m, p)),
                                    now_ms=lambda: self.now[0])
        self.cards.register(self.router)

        async def resume(turn_id):
            self.resumed.append(turn_id)
        self.cards.resume = resume
        self.turn = AgentHistory(conn).add(session_id="s", question="請找主旨含代號的信", route="answer", prefix="",
                                           text="", status="running", source="app", member_id=0)

    async def configure(self):
        await self.router.call("connectors.google.configure", {"client_id": CLIENT_ID, "gmail": "read", "calendar": "read"})

    def card(self, app="gmail"):
        return self.cards.show(turn_id=self.turn, member_id=0, conversation_id=None, app=APPS[app], language="zh-TW")

    async def connect(self, card):
        result = await self.router.call("integrations.card_connect", {"id": card.id})
        await self.cards.idle()
        return result, self.cards.store.get(card.id)


@pytest.fixture
def fixture():
    return GoogleFixture("n0nce")


async def test_a_card_signs_in_through_the_loopback_and_resumes_its_task_once(tmp_path, conn, fixture, caplog):
    caplog.set_level(logging.DEBUG)
    rig = Rig(tmp_path, conn, fixture)
    await rig.configure()
    card = rig.card()
    assert card.state == "pending" and not card.reconnect
    started, card = await rig.connect(card)
    assert started["started"] and started["card"]["state"] == "authorizing"
    assert (card.state, card.tool_count, card.resumed) == ("connected", 2, True)
    assert rig.resumed == [rig.turn]
    assert fixture.sign_ins == [{"url_digest": fixture.sign_ins[0]["url_digest"], "authorization_endpoint": True,
                                 "client": True, "pkce": True, "state_present": True, "loopback": True}]
    assert rig.cards.ready_tools(APPS["gmail"]) == ["gmail_search", "gmail_get_message"]
    # A replayed callback reaches nothing; Connect again neither signs in nor resumes.
    assert await fixture.replay() is None
    again, card = await rig.connect(card)
    assert not again["started"] and card.state == "connected"
    assert rig.resumed == [rig.turn] and fixture.opened == 1
    # What the window and the logs see holds no part of the sign-in.
    seen = json.dumps(rig.events) + json.dumps(card.view()) + caplog.text + json.dumps(
        [list(row) for row in conn.execute("SELECT * FROM connect_cards")])
    assert fixture.secrets and not any(secret in seen for secret in fixture.secrets)
    assert ("agent.turn", {"turn_id": rig.turn, "member_id": 0}) in rig.events


async def test_a_cancelled_sign_in_never_connects_and_can_be_tried_again(tmp_path, conn, fixture):
    fixture.mode = "hold"
    rig = Rig(tmp_path, conn, fixture)
    await rig.configure()
    card = rig.card()
    await rig.router.call("integrations.card_connect", {"id": card.id})
    while not fixture.opened:
        await asyncio.sleep(0.01)
    assert rig.cards.store.get(card.id).state == "authorizing"
    assert (await rig.router.call("integrations.card_cancel", {"id": card.id}))["cancelled"]
    await rig.cards.idle()
    card = rig.cards.store.get(card.id)
    assert card.state == "cancelled" and not rig.cards.ready_tools(APPS["gmail"]) and not rig.resumed
    fixture.mode = "approve"
    _, card = await rig.connect(card)
    assert card.state == "connected" and rig.resumed == [rig.turn]


@pytest.mark.parametrize("mode,error", [("deny", "denied"), ("forged", "state")])
async def test_a_refused_or_forged_callback_fails_without_connecting(tmp_path, conn, fixture, mode, error):
    fixture.mode = mode
    rig = Rig(tmp_path, conn, fixture)
    await rig.configure()
    _, card = await rig.connect(rig.card())
    assert (card.state, card.error, card.resumed) == ("failed", error, False)
    assert not rig.cards.ready_tools(APPS["gmail"]) and not rig.resumed


async def test_a_grant_without_the_apps_scope_is_never_connected(tmp_path, conn):
    fixture = GoogleFixture("n0nce", scopes="calendar-only")
    rig = Rig(tmp_path, conn, fixture)
    await rig.configure()
    gmail, calendar = rig.card("gmail"), rig.card("googlecalendar")
    _, gmail = await rig.connect(gmail)
    assert (gmail.state, gmail.error) == ("failed", "scope")
    # The same grant covers Calendar: its card connects without a second sign-in.
    _, calendar = await rig.connect(calendar)
    assert (calendar.state, calendar.tool_count) == ("connected", 1) and fixture.opened == 1
    assert not rig.resumed  # the turn still waits for Gmail


async def test_a_turn_with_two_cards_resumes_once_when_both_are_connected(tmp_path, conn, fixture):
    rig = Rig(tmp_path, conn, fixture)
    await rig.configure()
    gmail, calendar = rig.card("gmail"), rig.card("googlecalendar")
    await rig.connect(gmail)
    assert not rig.resumed
    await rig.connect(calendar)
    assert rig.resumed == [rig.turn]
    assert rig.card("gmail") is None  # connected: no new card


async def test_an_expired_card_starts_no_sign_in(tmp_path, conn, fixture):
    rig = Rig(tmp_path, conn, fixture)
    await rig.configure()
    card = rig.card()
    rig.now[0] += 24 * 3600 * 1000
    result, card = await rig.connect(card)
    assert not result["started"] and card.state == "expired"
    assert fixture.opened == 0 and not rig.resumed
    assert rig.cards.views([rig.turn])[rig.turn][0]["state"] == "expired"


async def test_without_a_google_or_composio_setup_the_card_fails_and_no_browser_opens(tmp_path, conn, fixture):
    rig = Rig(tmp_path, conn, fixture)
    _, card = await rig.connect(rig.card())
    assert (card.state, card.error) == ("failed", "not_configured") and fixture.opened == 0


async def test_composio_connects_the_same_card_when_it_is_the_setup(tmp_path, conn):
    opened = []

    async def browser(url):
        opened.append(url)
    platform = Platform()
    rig = Rig(tmp_path, conn, GoogleFixture("n0nce"), transport=httpx.MockTransport(platform), browser=browser)
    rig.vault.set("connectors:composio:key", "ak_test")
    _, card = await rig.connect(rig.card())
    assert (card.state, card.tool_count) == ("connected", 3) and len(opened) == 1
    assert rig.resumed == [rig.turn]


async def test_an_expired_sign_in_makes_a_reconnect_card(tmp_path, conn, fixture):
    rig = Rig(tmp_path, conn, fixture)
    await rig.configure()
    await rig.connect(rig.card())
    fixture.expire_access()
    rig.connectors.google.forget_access()
    with pytest.raises(Exception):
        await rig.connectors.call_google("gmail_search", {"q": "n0nce"})
    assert rig.connectors.store.connectors["google"].status == "EXPIRED"
    turn = AgentHistory(conn).add(session_id="t", question="再找一次", route="answer", prefix="", text="",
                                  status="running", source="app", member_id=0)
    card = rig.cards.show(turn_id=turn, member_id=0, conversation_id=None, app=APPS["gmail"], language="zh-TW")
    assert card.reconnect and card.state == "pending"
    _, card = await rig.connect(card)
    assert (card.state, card.reconnect, card.tool_count) == ("connected", True, 2)
    assert rig.resumed == [rig.turn, turn]


async def test_the_roster_counts_the_apps_tools_after_the_card_connects(tmp_path, conn, fixture):
    rig = Rig(tmp_path, conn, fixture)
    await rig.configure()

    class Team:
        _connections = staticmethod(rig.hub.connections)

        @staticmethod
        def roster():
            return []
    before = snapshot(Team, 0, "en")
    assert [(app["id"], app["state"], app["tools"]) for app in before.apps] == [("google", "needs_sign_in", 0)]
    _, card = await rig.connect(rig.card())
    after = snapshot(Team, 0, "en")
    assert [(app["id"], app["state"], app["tools"]) for app in after.apps] == [("google", "ready", 3)]
    assert after.revision != before.revision and card.tool_count == 2


async def test_an_unknown_inventory_is_never_connected(tmp_path, conn, fixture):
    rig = Rig(tmp_path, conn, fixture)

    class Broken:
        @staticmethod
        def _connections():
            raise OSError("unreadable")

        @staticmethod
        def roster():
            return []
    assert snapshot(Broken, 0, "en").inventory_state == "unknown"

    def unreadable(*args):
        raise ValueError("unreadable store")
    rig.connectors.tool_available = unreadable
    card = rig.card()
    assert card is not None and card.state == "pending"


async def test_a_card_moves_only_along_its_edges(conn):
    store = CardStore(conn, lambda: 1)
    AgentHistory(conn).add(session_id="s", question="q", route="answer", prefix="", text="", status="done", source="app")
    card = store.open(turn_id=1, member_id=0, conversation_id=None, app="gmail", language="en", reconnect=False)
    assert store.open(turn_id=1, member_id=0, conversation_id=None, app="gmail", language="en", reconnect=False).id == card.id
    assert store.move(card.id, "connected") is None and store.move(card.id, "cancelled") is None
    assert store.move(card.id, "authorizing").state == "authorizing"
    assert store.move(card.id, "authorizing") is None
    assert store.move(card.id, "connected", tool_count=2).state == "connected"
    for to in ("failed", "cancelled", "authorizing", "expired"):
        assert store.move(card.id, to) is None
    assert store.claim_resume(1) and not store.claim_resume(1)


async def test_a_sign_in_cut_off_by_a_restart_is_interrupted(conn):
    store = CardStore(conn, lambda: 1)
    AgentHistory(conn).add(session_id="s", question="q", route="answer", prefix="", text="", status="done", source="app")
    card = store.open(turn_id=1, member_id=0, conversation_id=None, app="gmail", language="en", reconnect=False)
    store.move(card.id, "authorizing")
    restarted = CardStore(conn, lambda: 2)
    assert (restarted.get(card.id).state, restarted.get(card.id).error) == ("failed", "interrupted")


async def test_unknown_cards_are_refused(tmp_path, conn, fixture):
    rig = Rig(tmp_path, conn, fixture)
    for params in ({}, {"id": 3}, {"id": "missing"}):
        with pytest.raises(RpcError):
            await rig.router.call("integrations.card_connect", params)
