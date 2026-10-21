"""Tests for the Telegram gateway and its service, against a fake Bot API (an httpx transport that
answers like api.telegram.org). Nothing here touches the network."""

import asyncio
import json
import logging

import httpx
import pytest

from raphael_core.integrations.vault import MemoryVault
from raphael_core.messaging import telegram
from raphael_core.messaging.config import MessagingStore
from raphael_core.messaging.service import TOKEN_SECRET, MessagingService
from raphael_core.messaging.telegram import (
    Pairing,
    TelegramApi,
    TelegramGateway,
    compose,
    split_message,
)
from raphael_core.protocol import RpcError
from raphael_core.router import Router

TOKEN = "123456:ABCdefGhIjKlMnOpQrStUvWxYz0123456789"
OWNER = 111


class FakeTelegram:
    """The Bot API: updates queued for getUpdates, every sent message recorded."""

    def __init__(self):
        self.updates: list[dict] = []
        self.sent: list[tuple[int, str]] = []
        self.actions: list[int] = []
        self.requests: list[tuple[str, dict]] = []
        self.fail_polls: list[int] = []  # status codes to answer the next polls with
        self.valid_token = TOKEN
        self.polled = asyncio.Event()

    def message(self, user_id, text=None, *, chat_type="private", **extra):
        update_id = len(self.updates) + 1
        body = {
            "chat": {"id": user_id, "type": chat_type},
            "from": {"id": user_id, "is_bot": False, "first_name": "Ann"},
            **extra,
        }
        if text is not None:
            body["text"] = text
        self.updates.append({"update_id": update_id, "message": body})

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        token, _, method = path[len("/bot") :].partition("/")
        payload = json.loads(request.content or b"{}")
        self.requests.append((method, payload))
        if token != self.valid_token:
            return httpx.Response(401, json={"ok": False, "description": "Unauthorized"})
        if method == "getMe":
            return httpx.Response(
                200, json={"ok": True, "result": {"id": 999, "is_bot": True, "username": "raph_bot"}}
            )
        if method == "getUpdates":
            self.polled.set()
            if self.fail_polls:
                return httpx.Response(self.fail_polls.pop(0), json={"ok": False})
            offset = payload.get("offset") or 0
            fresh = [u for u in self.updates if u["update_id"] >= offset]
            if not fresh:
                await asyncio.sleep(0.01)  # a long poll that found nothing: the loop must get a turn
            return httpx.Response(200, json={"ok": True, "result": fresh})
        if method == "sendMessage":
            self.sent.append((payload["chat_id"], payload["text"]))
        if method == "sendChatAction":
            self.actions.append(payload["chat_id"])
        return httpx.Response(200, json={"ok": True, "result": True})


@pytest.fixture
def fake():
    return FakeTelegram()


@pytest.fixture
def http(fake):
    return httpx.AsyncClient(transport=httpx.MockTransport(fake))


async def settle(predicate, tries=200):
    for _ in range(tries):
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("did not happen")


def make_gateway(fake, http, *, allowed=(OWNER,), answer="《答》好", stopped=True, sleep=None):
    asked: list[tuple[str, str, str]] = []

    async def ask(session, text, language):
        asked.append((session, text, language))
        return answer

    stops: list[str] = []

    async def stop(session):
        stops.append(session)
        return stopped

    async def nap(seconds):
        await asyncio.sleep(0)

    gateway = TelegramGateway(
        TelegramApi(TOKEN, http),
        ask=ask,
        stop=stop,
        is_allowed=lambda user_id: user_id in allowed,
        language=lambda: "en",
        sleep=sleep or nap,
    )
    gateway.asked, gateway.stops = asked, stops
    return gateway


async def run_until(gateway, predicate):
    task = asyncio.ensure_future(gateway.run())
    try:
        await settle(predicate)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


# --- pure helpers ---


def test_short_text_is_one_piece():
    assert split_message("hello") == ["hello"]


def test_long_text_is_split_within_the_limit_without_losing_words():
    text = " ".join(["word"] * 3000)
    pieces = split_message(text)
    assert len(pieces) > 1
    assert all(len(p) <= 4096 for p in pieces)
    assert " ".join(pieces).split() == text.split()


def test_text_without_any_break_is_cut_hard():
    pieces = split_message("x" * 9000)
    assert [len(p) for p in pieces] == [4096, 4096, 808]


def test_emoji_count_as_two_units():
    pieces = split_message("😀" * 3000)
    assert all(telegram._units(p) <= 4096 for p in pieces)
    assert "".join(pieces) == "😀" * 3000


def test_compose_adds_detail_only_when_it_says_more():
    assert compose("《答》", "好", None) == "《答》好"
    assert compose("《答》", "好", "好") == "《答》好"
    assert compose("《答》", "好", "longer") == "《答》好\n\nlonger"


# --- pairing ---


def test_a_code_is_six_characters_and_told_once_per_user():
    pairing = Pairing()
    code = pairing.issue(1, 1, "Ann")
    assert code is not None and len(code) == 6
    assert pairing.issue(1, 1, "Ann") is None
    assert pairing.issue(2, 2, "Bo") != code


def test_a_code_expires_after_ten_minutes():
    now = [0.0]
    pairing = Pairing(clock=lambda: now[0])
    code = pairing.issue(1, 1, "Ann")
    now[0] = 599
    assert [p.code for p, _ in pairing.pending()] == [code]
    now[0] = 601
    assert pairing.pending() == []
    assert pairing.take(code) is None
    assert pairing.issue(1, 1, "Ann") is not None  # may ask again


def test_take_ignores_case_and_spaces():
    pairing = Pairing()
    code = pairing.issue(1, 1, "Ann")
    assert pairing.take(f" {code.lower()} ").user_id == 1


def test_only_ten_codes_wait_at_once():
    pairing = Pairing()
    assert all(pairing.issue(i, i, "") for i in range(10))
    assert pairing.issue(99, 99, "") is None


# --- the gateway ---


async def test_a_stranger_gets_one_code_and_never_reaches_the_assistant(fake, http):
    fake.message(555, "do things")
    fake.message(555, "please")
    gateway = make_gateway(fake, http)
    await run_until(gateway, lambda: fake.sent)
    await asyncio.sleep(0.05)
    assert len(fake.sent) == 1
    chat, text = fake.sent[0]
    code = gateway.pairing.pending()[0][0].code
    assert chat == 555 and code in text
    assert gateway.asked == []


async def test_an_allowed_user_is_answered_with_a_stable_session(fake, http):
    fake.message(OWNER, "what time is it")
    fake.message(OWNER, "and the weather")
    gateway = make_gateway(fake, http)
    await run_until(gateway, lambda: len(fake.sent) == 2)
    assert gateway.asked == [
        (f"telegram:{OWNER}", "what time is it", "en"),
        (f"telegram:{OWNER}", "and the weather", "en"),
    ]
    assert fake.sent == [(OWNER, "《答》好")] * 2


async def test_typing_is_shown_while_the_assistant_works(fake, http):
    release = asyncio.Event()
    gateway = make_gateway(fake, http)

    async def slow(session, text, language):
        await release.wait()
        return "done"

    gateway._ask = slow
    fake.message(OWNER, "hi")
    task = asyncio.ensure_future(gateway.run())
    await settle(lambda: fake.actions)
    release.set()
    await settle(lambda: fake.sent)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert fake.actions[0] == OWNER


async def test_a_long_answer_is_sent_in_pieces(fake, http):
    fake.message(OWNER, "essay")
    gateway = make_gateway(fake, http, answer="a " * 5000)
    await run_until(gateway, lambda: len(fake.sent) >= 3)
    assert all(len(text) <= 4096 for _, text in fake.sent)


async def test_groups_and_bots_are_ignored(fake, http):
    fake.message(OWNER, "in a group", chat_type="group")
    fake.message(777, "hello", chat_type="supergroup")
    fake.updates.append(
        {
            "update_id": 50,
            "message": {
                "chat": {"id": 8, "type": "private"},
                "from": {"id": 8, "is_bot": True},
                "text": "beep",
            },
        }
    )
    gateway = make_gateway(fake, http, allowed=(OWNER, 8))
    await run_until(gateway, lambda: gateway._offset == 51)
    await asyncio.sleep(0.05)
    assert fake.sent == [] and gateway.asked == []


async def test_commands_reply_without_asking_the_assistant(fake, http):
    fake.message(OWNER, "/start")
    fake.message(OWNER, "/help@raph_bot")
    fake.message(OWNER, "/stop")
    gateway = make_gateway(fake, http)
    await run_until(gateway, lambda: len(fake.sent) == 3)
    assert gateway.asked == []
    assert gateway.stops == [f"telegram:{OWNER}"]
    assert "Raphael" in fake.sent[0][1]
    assert fake.sent[2][1] == "Stopped."


async def test_stop_without_a_task_says_so(fake, http):
    fake.message(OWNER, "/stop")
    gateway = make_gateway(fake, http, stopped=False)
    await run_until(gateway, lambda: fake.sent)
    assert fake.sent[0][1] == "Nothing is running."


async def test_a_voice_message_is_declined_politely(fake, http):
    fake.message(OWNER, None, voice={"file_id": "x", "duration": 2})
    gateway = make_gateway(fake, http)
    await run_until(gateway, lambda: fake.sent)
    assert "text" in fake.sent[0][1] and gateway.asked == []


async def test_a_failing_assistant_gets_a_plain_reply(fake, http):
    fake.message(OWNER, "boom")
    gateway = make_gateway(fake, http)

    async def broken(*args):
        raise RuntimeError("secret detail")

    gateway._ask = broken
    await run_until(gateway, lambda: fake.sent)
    assert "secret detail" not in fake.sent[0][1]


async def test_poll_errors_back_off_and_recover(fake, http):
    fake.fail_polls = [500, 500, 429]
    delays = []

    async def record(seconds):
        delays.append(seconds)
        await asyncio.sleep(0)

    fake.message(OWNER, "hi")
    gateway = make_gateway(fake, http, sleep=record)
    await run_until(gateway, lambda: fake.sent)
    assert delays[:2] == [1.0, 2.0]
    assert delays[2] >= 4.0
    assert gateway.error is None


async def test_a_revoked_token_ends_the_polling(fake, http):
    fake.valid_token = "other"
    gateway = make_gateway(fake, http)
    await asyncio.wait_for(gateway.run(), 1)
    assert gateway.error == "unauthorized"


async def test_removing_a_user_takes_effect_at_once(fake, http):
    allowed = {OWNER}
    gateway = make_gateway(fake, http)
    gateway._is_allowed = allowed.__contains__
    fake.message(OWNER, "first")
    task = asyncio.ensure_future(gateway.run())
    await settle(lambda: fake.sent)
    allowed.clear()
    fake.message(OWNER, "second")
    await asyncio.sleep(0.1)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert [a[1] for a in gateway.asked] == ["first"]


async def test_nothing_private_reaches_the_log(fake, http, caplog):
    caplog.set_level(logging.DEBUG)
    fake.message(OWNER, "my secret plan")
    fake.message(555, "let me in")
    gateway = make_gateway(fake, http)
    await run_until(gateway, lambda: len(fake.sent) == 2)
    logged = caplog.text
    assert "my secret plan" not in logged and "Ann" not in logged and TOKEN not in logged


async def test_a_network_failure_does_not_leak_the_token():
    def refuse(request):
        raise httpx.ConnectError("cannot reach " + str(request.url))

    api = TelegramApi(TOKEN, httpx.AsyncClient(transport=httpx.MockTransport(refuse)))
    with pytest.raises(telegram.TelegramError) as caught:
        await api.get_me()
    assert TOKEN not in str(caught.value) and caught.value.__cause__ is None


def test_httpx_request_lines_with_the_token_are_filtered(caplog):
    caplog.set_level(logging.INFO, logger="httpx")
    logging.getLogger("httpx").info('HTTP Request: POST https://api.telegram.org/bot%s/getMe', TOKEN)
    assert TOKEN not in caplog.text


# --- the service ---


@pytest.fixture
def rig(tmp_path, fake, http):
    vault = MemoryVault()
    asked = []

    async def ask(session, text, language):
        asked.append(text)
        return "ok"

    async def stop(session):
        return False

    service = MessagingService(
        MessagingStore(tmp_path), vault, ask=ask, stop=stop, language=lambda: "en", http=http
    )
    router = Router()
    service.register(router)

    class Rig:
        pass

    r = Rig()
    r.service, r.router, r.vault, r.dir, r.http = service, router, vault, tmp_path, http
    r.call = lambda method, **params: router.call(f"messaging.telegram.{method}", params)
    return r


async def test_nothing_is_connected_at_first(rig):
    status = await rig.call("status")
    assert status["connected"] is False and status["allowed"] == [] and status["bot"] is None


async def test_a_good_token_connects_and_is_kept_only_in_the_vault(rig, fake):
    reply = await rig.call("connect", token=f" {TOKEN} ")
    assert reply["ok"] is True
    status = reply["status"]
    assert status["connected"] and status["enabled"] and status["bot"] == {"username": "raph_bot"}
    assert rig.vault.get(TOKEN_SECRET) == TOKEN
    assert TOKEN not in json.dumps(reply)
    assert TOKEN not in (rig.dir / "messaging.json").read_text(encoding="utf-8")
    await rig.service.aclose()


async def test_a_bad_token_is_refused_without_storing_anything(rig, fake):
    fake.valid_token = "other"
    assert await rig.call("connect", token=TOKEN) == {"ok": False, "reason": "invalid_token"}
    assert await rig.call("connect", token="nonsense") == {"ok": False, "reason": "invalid_token"}
    assert rig.vault.names() == []


async def test_pairing_flow_from_stranger_to_allowed_user(rig, fake):
    await rig.call("connect", token=TOKEN)
    fake.message(555, "hi")
    await settle(lambda: fake.sent)
    status = await rig.call("status")
    code = status["pending"][0]["code"]
    assert status["pending"][0]["name"] == "Ann" and code in fake.sent[0][1]
    approved = await rig.call("approve", code=code.lower())
    assert approved["approved"] is True
    assert approved["status"]["allowed"] == [{"user_id": 555, "name": "Ann"}]
    assert approved["status"]["pending"] == []
    await settle(lambda: len(fake.sent) == 2)  # the welcome
    fake.message(555, "now talk")
    await settle(lambda: len(fake.sent) == 3)
    # the allowlist is on disk and survives a restart
    assert MessagingStore(rig.dir).load().allowed[0].user_id == 555
    await rig.service.aclose()


async def test_approving_an_unknown_code_does_nothing(rig):
    await rig.call("connect", token=TOKEN)
    assert (await rig.call("approve", code="ZZZZZZ"))["approved"] is False
    await rig.service.aclose()


async def test_rejecting_removes_the_code(rig, fake):
    await rig.call("connect", token=TOKEN)
    fake.message(555, "hi")
    await settle(lambda: fake.sent)
    code = (await rig.call("status"))["pending"][0]["code"]
    assert (await rig.call("reject", code=code))["pending"] == []
    await rig.service.aclose()


async def test_a_telegram_message_cannot_approve_a_pairing(rig, fake):
    await rig.call("connect", token=TOKEN)
    fake.message(555, "hi")
    await settle(lambda: fake.sent)
    code = (await rig.call("status"))["pending"][0]["code"]
    fake.message(OWNER, f"/approve {code}")  # not allowed itself
    fake.message(555, code)
    await asyncio.sleep(0.1)
    status = await rig.call("status")
    assert status["allowed"] == [] and status["pending"][0]["code"] == code
    await rig.service.aclose()


async def test_remove_user_and_switch_off_and_on(rig, fake):
    await rig.call("connect", token=TOKEN)
    rig.service._config.allowed.append(
        __import__("raphael_core.messaging.config", fromlist=["AllowedUser"]).AllowedUser(OWNER, "O")
    )
    off = await rig.call("set_enabled", enabled=False)
    assert off["enabled"] is False and off["running"] is False
    on = await rig.call("set_enabled", enabled=True)
    assert on["running"] is True
    removed = await rig.call("remove_user", user_id=OWNER)
    assert removed["allowed"] == []
    await rig.service.aclose()


async def test_disconnect_deletes_the_token_and_the_allowlist(rig, fake):
    await rig.call("connect", token=TOKEN)
    status = await rig.call("disconnect")
    assert status["connected"] is False and status["running"] is False
    assert rig.vault.names() == []
    assert MessagingStore(rig.dir).load().connected is False


async def test_enabling_without_a_bot_is_refused(rig):
    with pytest.raises(RpcError):
        await rig.call("set_enabled", enabled=True)


async def test_a_bot_left_on_polls_again_after_a_restart(rig, fake, http):
    await rig.call("connect", token=TOKEN)
    await rig.service.aclose()
    again = MessagingService(
        MessagingStore(rig.dir), rig.vault, ask=rig.service._ask, stop=rig.service._stop,
        language=lambda: "en", http=http,
    )
    again.start()
    assert (await again._status({}))["running"] is True
    await again.aclose()


async def test_a_background_report_goes_to_its_chat_only_if_allowed(rig, fake):
    from raphael_core.messaging.config import AllowedUser

    await rig.call("connect", token=TOKEN)
    rig.service._config.allowed.append(AllowedUser(OWNER, "O"))
    await rig.service.report(f"telegram:{OWNER}", "《告》done")
    await rig.service.report("telegram:555", "leak")
    await rig.service.report("overlay-1", "leak")
    assert fake.sent == [(OWNER, "《告》done")]
    await rig.service.aclose()
