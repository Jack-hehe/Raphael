"""The switchboard with a channel that is not Telegram: nothing in it depends on one app."""

import asyncio
import contextlib

from raphael_core.messaging.channels.base import Incoming
from raphael_core.messaging.core import Switchboard, session_id, split_session
from raphael_core.messaging.service import MessagingService
from raphael_core.messaging.config import AllowedUser, MessagingStore
from raphael_core.integrations.vault import MemoryVault
from raphael_core.messaging.channels.base import ChannelType, Identity


class FakeChannel:
    kind = "slack"
    error = None

    def __init__(self):
        self.inbox: asyncio.Queue[Incoming] = asyncio.Queue()
        self.sent: list[tuple[str, str]] = []
        self.busy_chats: list[str] = []

    async def run(self, deliver):
        while True:
            deliver(await self.inbox.get())

    async def send(self, chat, text):
        self.sent.append((chat, text))

    @contextlib.asynccontextmanager
    async def busy(self, chat):
        self.busy_chats.append(chat)
        yield

    async def fetch(self, attachment, limit):
        raise AssertionError("no attachment expected")


async def settle(predicate, tries=200):
    for _ in range(tries):
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("did not happen")


def make(channel, *, allowed=("U1",)):
    asked, stops = [], []

    async def ask(session, text, language, source):
        asked.append((session, text, source))
        return "answer"

    async def stop(session):
        stops.append(session)
        return True

    board = Switchboard(
        channel,
        ask=ask,
        stop=stop,
        is_allowed=lambda user: user in allowed,
        chat_allowed=lambda chat: chat in {"D1"},
        language=lambda: "en",
    )
    board.asked, board.stops = asked, stops
    return board


async def run(board, predicate):
    task = asyncio.ensure_future(board.run())
    try:
        await settle(predicate)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def test_a_session_id_names_the_channel_and_the_chat():
    assert session_id("slack", "D1") == "slack:D1"
    assert split_session("slack:D1") == ("slack", "D1")
    assert split_session("overlay-1") is None and split_session("slack:") is None


async def test_the_session_and_source_are_the_channels_and_the_answer_goes_back_to_the_chat():
    channel = FakeChannel()
    board = make(channel)
    await channel.inbox.put(Incoming(chat="D1", user="U1", text="hello"))
    await run(board, lambda: channel.sent)
    assert board.asked == [("slack:D1", "hello", "slack")]
    assert channel.sent == [("D1", "answer")] and channel.busy_chats == ["D1"]


async def test_a_message_outside_a_private_chat_is_ignored():
    channel = FakeChannel()
    board = make(channel)
    await channel.inbox.put(Incoming(chat="C1", user="U1", text="in a channel", private=False))
    await channel.inbox.put(Incoming(chat="D1", user="U1", text="direct"))
    await run(board, lambda: channel.sent)
    assert [a[1] for a in board.asked] == ["direct"]


async def test_stop_names_the_session_of_the_chat_that_asked():
    channel = FakeChannel()
    board = make(channel)
    await channel.inbox.put(Incoming(chat="D1", user="U1", text="/stop"))
    await run(board, lambda: channel.sent)
    assert board.stops == ["slack:D1"] and channel.sent == [("D1", "Stopped.")]


async def test_a_stranger_is_given_a_code_and_the_report_only_reaches_allowed_chats():
    channel = FakeChannel()
    board = make(channel)
    await channel.inbox.put(Incoming(chat="D9", user="U9", name="Eve", text="let me in"))
    await run(board, lambda: channel.sent)
    assert board.pairing.pending()[0][0].user == "U9" and board.asked == []
    channel.sent.clear()
    await board.tell("D9", "leak")
    await board.tell("D1", "report")
    assert channel.sent == [("D1", "report")]


async def test_the_service_sends_a_report_through_the_channel_named_in_the_session(tmp_path):
    channel = FakeChannel()
    kind = ChannelType(
        kind="slack",
        secrets=("bot_token", "app_token"),
        read_secrets=lambda params: {"bot_token": "b", "app_token": "a"},
        check=lambda secrets, http: asyncio.sleep(0, Identity("T1", "ws")),
        make=lambda secrets, http: channel,
    )
    vault = MemoryVault()

    async def ask(*args):
        return ""

    async def stop(session):
        return False

    service = MessagingService(
        MessagingStore(tmp_path), vault, ask=ask, stop=stop, language=lambda: "en", types={"slack": kind}
    )
    assert service.handles("slack:D1") and not service.handles("telegram:1") and not service.handles("overlay-1")
    reply = await service._connect("slack", {})
    assert reply["ok"] is True
    assert sorted(vault.names()) == ["messaging:slack:app_token", "messaging:slack:bot_token"]
    service._config.channel("slack").allowed.append(AllowedUser("U1", "Ann", "D1"))
    await service.report("slack:D1", "done")
    await service.report("slack:D2", "leak")
    await service.report("telegram:D1", "leak")
    assert channel.sent == [("D1", "done")]
    status = await service._disconnect("slack", {})
    assert status["connected"] is False and vault.names() == []
    await service.aclose()
