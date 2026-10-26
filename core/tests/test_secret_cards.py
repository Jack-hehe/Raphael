"""Secret cards through the production connection RPCs and a vault in memory: the key goes from the
call to the vault and nowhere else, the task resumes once, and every refusal leaves the vault alone.
Every key here is generated for the test."""

import json
import logging
import secrets

import pytest

from raphael_core.agent.history import AgentHistory
from raphael_core.agent.team_context import snapshot
from raphael_core.connectors import secret_cards
from raphael_core.connectors.secret_cards import SecretCardStore, SecretCards
from raphael_core.integrations.service import IntegrationsService
from raphael_core.integrations.vault import MAX_SECRET_CHARS, MemoryVault, secret_name
from raphael_core.protocol import RpcError
from raphael_core.router import Router


class CountingVault(MemoryVault):
    """A vault that remembers what was written to it, by name (never the values)."""

    def __init__(self):
        super().__init__()
        self.writes = []
        self.fail = None

    def set(self, name, value):
        if self.fail is not None:
            raise self.fail
        self.writes.append(name)
        super().set(name, value)


class Resume:
    """What the agent supplies: carries a turn on, and says whether it went on and what it did."""

    def __init__(self):
        self.calls = []
        self.outcomes = []

    async def __call__(self, turn_id, attempt):
        self.calls.append((turn_id, attempt))
        return self.outcomes.pop(0) if self.outcomes else Outcome(True)


class Outcome:
    def __init__(self, ok, made=()):
        self.ok, self.made = ok, tuple(made)


class Rig:
    def __init__(self, tmp_path, conn, clock=None):
        self.conn = conn
        self.vault = CountingVault()
        self.hub = IntegrationsService(conn, tmp_path, self.vault)
        self.router = Router()
        self.hub.register(self.router)
        self.events = []
        self.now = clock or [1_800_000_000_000]
        self.cards = SecretCards(conn, self.hub, notify=lambda m, p: self.events.append((m, p)), now_ms=lambda: self.now[0])
        self.cards.register(self.router)
        self.resume = self.cards.resume = Resume()
        self.history = AgentHistory(conn)
        self.turn = self.new_turn()
        self.key = "sk-test-" + secrets.token_hex(16)

    def new_turn(self, session="s"):
        return self.history.add(session_id=session, question="請查一下", route="answer", prefix="", text="",
                                status="running", source="app", member_id=0)

    async def connection(self, name="Weather", *, headers=("Authorization",), env=()):
        """A connection that declares its secrets, with none of them in the vault yet."""
        added = await self.router.call("integrations.add", {
            "name": name, "transport": "http" if headers else "stdio", "url": "https://example.invalid/mcp",
            "command": "never-run", "headers": [{"name": h, "value": "placeholder"} for h in headers],
            "env": [{"name": e, "value": "placeholder"} for e in env]})
        server = added["server"]["id"]
        await self.router.call("integrations.set_enabled", {"id": server, "enabled": True})
        for kind, keys in (("header", headers), ("env", env)):
            for key in keys:
                self.vault.delete(secret_name(server, kind, key))
        self.vault.writes.clear()
        return server

    def show(self, connection, secret=None, turn=None, done=()):
        return self.cards.show(turn_id=turn or self.turn, member_id=0, conversation_id=None, connection=connection,
                               secret=secret, language="zh-TW", done=done)

    async def save(self, card, value=None):
        result = await self.router.call("integrations.card_save", {"id": card.id, "value": self.key if value is None else value})
        await self.cards.idle()
        return result, self.cards.store.get(card.id)

    def everything(self):
        """All the app holds besides the vault: every row, the notifications and the views."""
        rows = [[list(row) for row in self.conn.execute(f'SELECT * FROM "{table}"')]
                for (table,) in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        return json.dumps([rows, self.events, self.cards.views([self.turn])], default=str)


async def test_a_key_goes_straight_to_the_vault_and_its_task_resumes_once(tmp_path, conn, caplog):
    caplog.set_level(logging.DEBUG)
    rig = Rig(tmp_path, conn)
    server = await rig.connection()
    (card,) = rig.show("Weather")
    assert (card.state, card.name, card.secret) == ("pending", "Weather", "Authorization")
    assert rig.hub.missing_secrets(server)[1] == [("header", "Authorization")]
    result, card = await rig.save(card)
    assert result["saved"] and (card.state, card.resumed, card.attempts) == ("resumed", True, 1)
    assert rig.resume.calls == [(rig.turn, 1)]
    # A bare token in an Authorization header is a bearer; the vault holds it under the connection's own name.
    assert rig.vault.get(secret_name(server, "header", "Authorization")) == "Bearer " + rig.key
    assert rig.vault.writes == [secret_name(server, "header", "Authorization")]
    assert rig.hub.missing_secrets(server)[1] == []
    # Nothing but the vault holds it: not a row, a notification, a view, a reply or a log line.
    seen = rig.everything() + json.dumps(result) + caplog.text + json.dumps(await rig.router.call("toolsets.list", {}))
    assert rig.key not in seen
    assert ("agent.turn", {"turn_id": rig.turn, "member_id": 0}) in rig.events
    assert card.view() == {"id": card.id, "name": "Weather", "secret": "Authorization", "state": "resumed", "error": None,
                           "expires_at_ms": card.expires_at_ms, "resumed": True}


async def test_a_value_with_a_scheme_in_it_is_kept_as_typed(tmp_path, conn):
    rig = Rig(tmp_path, conn)
    server = await rig.connection()
    (card,) = rig.show(server)
    await rig.save(card, "Basic " + rig.key)
    assert rig.vault.get(secret_name(server, "header", "Authorization")) == "Basic " + rig.key


async def test_an_environment_variable_is_kept_as_typed(tmp_path, conn):
    rig = Rig(tmp_path, conn)
    server = await rig.connection("Maps", headers=(), env=("MAPS_API_KEY",))
    (card,) = rig.show("maps")
    assert (card.secret, card.kind) == ("MAPS_API_KEY", "env")
    await rig.save(card)
    assert rig.vault.get(secret_name(server, "env", "MAPS_API_KEY")) == rig.key


async def test_a_replayed_save_changes_nothing_and_never_touches_the_vault(tmp_path, conn):
    rig = Rig(tmp_path, conn)
    server = await rig.connection()
    (card,) = rig.show("Weather")
    await rig.save(card)
    stored = rig.vault.get(secret_name(server, "header", "Authorization"))
    again, card = await rig.save(card, "another-" + rig.key)
    assert not again["saved"] and card.state == "resumed"
    assert rig.vault.get(secret_name(server, "header", "Authorization")) == stored
    assert rig.vault.writes == [secret_name(server, "header", "Authorization")]
    assert rig.resume.calls == [(rig.turn, 1)]


async def test_a_failed_resume_leaves_the_key_saved_and_a_retry_does_not_ask_again(tmp_path, conn):
    rig = Rig(tmp_path, conn)
    server = await rig.connection()
    rig.resume.outcomes = [Outcome(False, [("Weather/post", "d1")]), Outcome(True)]
    (card,) = rig.show("Weather", done=[("Weather/earlier", "d0")])
    result, card = await rig.save(card)
    assert (card.state, card.error, card.resumed, card.attempts) == ("saved", "resume_failed", False, 1)
    assert set(card.done) == {("Weather/earlier", "d0"), ("Weather/post", "d1")}  # the attempt's own work is not redone
    assert rig.hub.missing_secrets(server)[1] == []  # the key is saved all the same
    retried = await rig.router.call("integrations.card_resume", {"id": card.id})
    await rig.cards.idle()
    card = rig.cards.store.get(card.id)
    assert retried["resumed"] and (card.state, card.error, card.attempts) == ("resumed", "", 2)
    assert rig.resume.calls == [(rig.turn, 1), (rig.turn, 2)]
    assert rig.vault.writes == [secret_name(server, "header", "Authorization")]  # the key was asked for once
    # A retry on a card that resumed is nothing.
    assert not (await rig.router.call("integrations.card_resume", {"id": card.id}))["resumed"]
    assert len(rig.resume.calls) == 2


async def test_a_resume_that_raises_is_a_resume_that_failed(tmp_path, conn, caplog):
    rig = Rig(tmp_path, conn)
    await rig.connection()
    (card,) = rig.show("Weather")

    async def broken(turn_id, attempt):
        raise RuntimeError("model unreachable " + rig.key)
    rig.cards.resume = broken
    _, card = await rig.save(card)
    assert (card.state, card.error) == ("saved", "resume_failed")
    assert rig.key not in caplog.text and rig.key not in rig.everything()


async def test_a_retry_on_a_card_that_never_failed_resumes_nothing(tmp_path, conn):
    rig = Rig(tmp_path, conn)
    await rig.connection()
    (card,) = rig.show("Weather")
    assert not (await rig.router.call("integrations.card_resume", {"id": card.id}))["resumed"]
    assert rig.resume.calls == [] and rig.cards.store.get(card.id).state == "pending"


async def test_cancel_expiry_and_stop_never_resume(tmp_path, conn):
    rig = Rig(tmp_path, conn)
    server = await rig.connection()
    # Stop: the turn's session is stopped while its card waits.
    (card,) = rig.show("Weather")
    rig.cards.cancel_session("s")
    assert rig.cards.store.get(card.id).state == "cancelled"
    result, card = await rig.save(card)
    assert not result["saved"] and card.state == "cancelled"
    assert rig.vault.writes == [] and rig.resume.calls == []
    # Expiry: a card whose time ran out is refused.
    turn = rig.new_turn("t")
    (late,) = rig.show("Weather", turn=turn)
    rig.now[0] += 24 * 3600 * 1000
    result, late = await rig.save(late)
    assert not result["saved"] and late.state == "expired"
    assert rig.vault.writes == [] and rig.resume.calls == []
    assert rig.cards.views([turn])[turn][0]["state"] == "expired"
    assert rig.hub.missing_secrets(server)[1] == [("header", "Authorization")]


async def test_a_saved_card_that_waits_for_another_expires_or_is_stopped_without_resuming(tmp_path, conn):
    rig = Rig(tmp_path, conn)
    server = await rig.connection("Weather", headers=("Authorization", "X-Org"))
    first, second = rig.show(server)
    _, first = await rig.save(first)
    assert first.state == "saved" and rig.resume.calls == []  # the turn still waits for the other key
    rig.cards.cancel_session("s")
    assert [card.state for card in rig.cards.cards_of(rig.turn)] == ["cancelled", "cancelled"]
    result, second = await rig.save(second)
    assert not result["saved"] and rig.resume.calls == []
    # Expiry takes the same card.
    other = await rig.connection("Maps", headers=("Authorization", "X-Org"))
    turn = rig.new_turn("t")
    one, two = rig.show(other, turn=turn)
    _, one = await rig.save(one)
    rig.now[0] += 24 * 3600 * 1000
    assert rig.cards.store.get(one.id).state == "expired" and rig.resume.calls == []


async def test_a_turn_with_two_keys_resumes_once_when_both_are_saved(tmp_path, conn):
    rig = Rig(tmp_path, conn)
    server = await rig.connection("Weather", headers=("Authorization", "X-Org"))
    first, second = rig.show("Weather")
    assert (first.secret, second.secret) == ("Authorization", "X-Org")
    assert [c.id for c in rig.show("Weather")] == [first.id, second.id]  # a task asks once
    _, first = await rig.save(first)
    assert rig.resume.calls == []
    _, second = await rig.save(second)
    assert rig.resume.calls == [(rig.turn, 1)]
    assert {card.state for card in rig.cards.cards_of(rig.turn)} == {"resumed"}
    assert rig.hub.missing_secrets(server)[1] == []


async def test_only_the_named_secret_of_a_connection_gets_a_card(tmp_path, conn):
    rig = Rig(tmp_path, conn)
    await rig.connection("Weather", headers=("Authorization", "X-Org"))
    (card,) = rig.show("Weather", secret="x-org")
    assert card.secret == "X-Org"
    with pytest.raises(LookupError):
        rig.show("Weather", secret="Cookie")


async def test_a_card_fills_only_a_slot_a_connection_declares(tmp_path, conn):
    rig = Rig(tmp_path, conn)
    server = await rig.connection()
    vault_names = rig.vault.names()
    # The model names a connection, never a place in the vault.
    for reference in ("connectors:composio:key", "Raphael plugin", "openrouter", server + ":header:Authorization", ""):
        with pytest.raises(LookupError):
            rig.show(reference)
    assert rig.vault.names() == vault_names and rig.cards.store.for_turns([rig.turn]) == {}
    # Nothing is missing once the key is there: no card.
    rig.vault.set(secret_name(server, "header", "Authorization"), "kept")
    assert rig.show("Weather") == []


async def test_a_managed_connection_has_no_secret_card(tmp_path, conn):
    rig = Rig(tmp_path, conn)
    from raphael_core.integrations.config import ServerConfig
    rig.hub._config.add(ServerConfig("google", "Google (private)", "http", url="https://example.invalid/mcp",
                                    managed_by="google", headers=["x-api-key"]))
    with pytest.raises(LookupError):
        rig.show("google")
    with pytest.raises(LookupError):
        rig.show("Google (private)")


@pytest.mark.parametrize("value,error", [("", "invalid"), ("   ", "invalid"), ("two\nlines", "invalid"),
                                         ("x" * (MAX_SECRET_CHARS + 1), "too_long")])
async def test_a_value_the_vault_cannot_hold_fails_and_can_be_typed_again(tmp_path, conn, value, error):
    rig = Rig(tmp_path, conn)
    server = await rig.connection()
    (card,) = rig.show("Weather")
    result, card = await rig.save(card, value)
    assert not result["saved"] and (card.state, card.error) == ("failed", error)
    assert rig.vault.get(secret_name(server, "header", "Authorization")) is None and rig.resume.calls == []
    assert value.strip() == "" or value not in rig.everything()
    result, card = await rig.save(card)  # typed again
    assert result["saved"] and card.state == "resumed"


async def test_a_vault_that_fails_says_only_that_it_did(tmp_path, conn, caplog):
    rig = Rig(tmp_path, conn)
    await rig.connection()
    rig.vault.fail = RuntimeError("the vault said " + rig.key)
    (card,) = rig.show("Weather")
    result, card = await rig.save(card)
    assert (card.state, card.error) == ("failed", "vault") and not result["saved"]
    assert rig.key not in caplog.text + rig.everything() + json.dumps(result)


async def test_a_connection_removed_after_its_card_was_shown_keeps_nothing(tmp_path, conn):
    rig = Rig(tmp_path, conn)
    server = await rig.connection()
    (card,) = rig.show("Weather")
    await rig.router.call("integrations.disconnect", {"id": server})
    result, card = await rig.save(card)
    assert (card.state, card.error) == ("failed", "gone") and rig.vault.names() == []


async def test_a_value_that_is_not_text_and_unknown_cards_are_refused(tmp_path, conn):
    rig = Rig(tmp_path, conn)
    await rig.connection()
    (card,) = rig.show("Weather")
    for params in ({}, {"id": 3}, {"id": "missing", "value": "x"}, {"id": card.id}, {"id": card.id, "value": 5},
                   {"id": card.id, "value": ["a"]}):
        with pytest.raises(RpcError):
            await rig.router.call("integrations.card_save", params)
    assert rig.vault.writes == [] and rig.cards.store.get(card.id).state == "pending"


async def test_a_card_moves_only_along_its_edges(conn):
    store = SecretCardStore(conn, lambda: 1)
    AgentHistory(conn).add(session_id="s", question="q", route="answer", prefix="", text="", status="done", source="app")
    card = store.open(turn_id=1, member_id=0, conversation_id=None, server_id="x", kind="header", secret="K",
                      name="X", language="en")
    again = store.open(turn_id=1, member_id=0, conversation_id=None, server_id="x", kind="header", secret="K",
                       name="X", language="en")
    assert again.id == card.id
    states = list(secret_cards.MOVES)
    for start in states:
        for to in states:
            with conn:
                conn.execute("UPDATE secret_cards SET state=? WHERE id=?", (start, card.id))
            moved = store.move(card.id, to)
            assert (moved is not None) == (to in secret_cards.MOVES[start]), (start, to)
            assert moved is None or moved.state == to


async def test_a_resume_cut_off_by_a_restart_leaves_the_key_saved_and_interrupted(tmp_path, conn):
    rig = Rig(tmp_path, conn)
    await rig.connection()
    (card,) = rig.show("Weather")
    rig.cards.store.move(card.id, "saved")
    assert rig.cards.store.claim_resume(rig.turn)
    assert rig.cards.store.get(card.id).state == "resuming"
    restarted = SecretCardStore(conn, lambda: 2)
    card = restarted.get(card.id)
    assert (card.state, card.error) == ("saved", "interrupted") and not card.resumed
    # Nothing resumed by itself; a retry does, once.
    assert rig.resume.calls == []
    assert restarted.claim_resume(rig.turn) and not restarted.claim_resume(rig.turn)


async def test_the_roster_says_a_connection_needs_its_secret_after_a_call_found_it_missing(tmp_path, conn):
    from raphael_core.integrations.client import McpConnector, McpFailure
    rig = Rig(tmp_path, conn)
    server = await rig.connection()

    class Team:
        _connections = staticmethod(rig.hub.connections)

        @staticmethod
        def roster():
            return []
    assert [app["state"] for app in snapshot(Team, 0, "en").apps] == ["unchecked"]
    rig.hub._failure[server] = "missing_secret"  # what a call that found the key missing leaves
    (app,) = snapshot(Team, 0, "en").apps
    assert (app["id"], app["state"]) == (server, "needs_secret")
    (card,) = rig.show(server)
    await rig.save(card)
    assert [app["state"] for app in snapshot(Team, 0, "en").apps] == ["unchecked"]
    # The production connector fails a connection with a declared secret that has no value.
    connector = McpConnector(MemoryVault())
    config = rig.hub._config.get(server)
    with pytest.raises(McpFailure) as raised:
        connector._secrets(config)
    assert raised.value.kind == "missing_secret"
