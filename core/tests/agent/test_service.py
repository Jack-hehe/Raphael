"""Tests for the voice assistant as a whole: the five RPC methods, from a spoken request to the
answer that is shown, with fakes for OpenRouter, the device, Open-Meteo and the clock.

No test spends money, presses a key, opens a page or touches the network.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from raphael_core.agent import commands, service
from raphael_core.agent.commands import NONE, Command
from raphael_core.agent.device import StartApp
from raphael_core.agent.history import AgentHistory
from raphael_core.agent.policy import Tier
from raphael_core.agent.routing import Router as Decider
from raphael_core.agent.service import AgentService
from raphael_core.brain.openrouter import OpenRouterError, TextCompletion
from raphael_core.brain.secrets import MemoryKeyStore
from raphael_core.brain.settings import SettingsStore
from raphael_core.brain.spend import SpendTracker
from raphael_core.protocol import INVALID_PARAMS, RpcError
from raphael_core.router import Router as RpcRouter

from .conftest import classification, completion, decision

KEY = "sk-or-v1-test"
NOW_MS = int(datetime(2026, 10, 20, 6, 5, tzinfo=timezone.utc).timestamp() * 1000)
LOCAL_NOW = datetime(2026, 10, 20, 14, 5, 9, tzinfo=timezone(timedelta(hours=8)))
METHODS = [
    "agent.ask",
    "agent.cancel",
    "agent.confirm",
    "agent.history.clear",
    "agent.history.list",
]
GEOCODING = {
    "results": [
        {"name": "Taipei", "latitude": 25.04776, "longitude": 121.53185, "country": "Taiwan"}
    ]
}
FORECAST = {
    "current": {"temperature_2m": 24.1, "weather_code": 2},
    "daily": {
        "temperature_2m_max": [27.3, 26.0],
        "temperature_2m_min": [21.0, 20.5],
        "precipitation_probability_max": [40, 70],
        "weather_code": [3, 61],
    },
}


async def until(predicate, timeout=5.0):
    """Wait until `predicate()` is true; a test that waits for ever is a failed test."""
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, "timed out waiting for a condition"
        await asyncio.sleep(0.005)


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


def spoken(prefix="答", text="台北現在 24 度，多雲。", detail="", cost=0.0009):
    return completion({"prefix": prefix, "text": text, "detail": detail}, cost=cost)


def found(text="明天台北有雨。", cost=0.012, sources=()):
    return TextCompletion(
        text=text, model="openai/gpt-6-luna", cost_usd=cost, prompt_tokens=800,
        completion_tokens=40, latency_ms=1800, sources=list(sources),
    )  # fmt: skip


class Meteo:
    """Open-Meteo, as an httpx client."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.on_request = None  # an async function a request waits for before it is answered
        self.down = False
        self.http = httpx.AsyncClient(transport=httpx.MockTransport(self._handle))

    async def _handle(self, request):
        self.requests.append(request)
        if self.on_request is not None:
            await self.on_request(request)
        if self.down:
            raise httpx.ConnectError("no route")
        body = GEOCODING if request.url.host.startswith("geocoding") else FORECAST
        return httpx.Response(200, json=body)


class Rig:
    def __init__(self, conn, client, actions, **service_kwargs):
        self.conn = conn
        self.client = client
        self.actions = actions
        self.keys = MemoryKeyStore()
        self.keys.set(KEY)
        self.wall = Clock(NOW_MS)
        self.clock = Clock()
        self.meteo = Meteo()
        self.settings = SettingsStore(conn)
        self.history = AgentHistory(conn, self.wall)
        self.spend = SpendTracker(conn, now_ms=self.wall, tz=timezone.utc)
        kwargs = {
            "actions": actions,
            "now_ms": self.wall,
            "tz": timezone.utc,
            "clock": self.clock,
            "http": self.meteo.http,
            "now": lambda: LOCAL_NOW,
            "decider": Decider(client, jev_total_s=1.0, jev_first_s=0.6, luna_s=1.0),
        }
        self.service = AgentService(conn, client, self.keys, **{**kwargs, **service_kwargs})
        self.rpc = RpcRouter()
        self.service.register(self.rpc)

    async def call(self, method, params=None):
        return await self.rpc.call(method, params)

    async def ask(self, text, *, session="s1", language="zh-TW", target_app=None):
        return await self.call(
            "agent.ask",
            {"session_id": session, "text": text, "ui_language": language, "target_app": target_app},
        )

    async def confirm(self, confirm_id, approved=True):
        return await self.call("agent.confirm", {"id": confirm_id, "approved": approved})

    async def turns(self, **params):
        return (await self.call("agent.history.list", params))["turns"]

    def spent(self):
        return [tuple(r) for r in self.conn.execute("SELECT purpose, model, cost_usd FROM spend")]

    def seed(self, question, text, *, detail=None, route="answer", prefix="答", status="done"):
        return self.history.add(
            session_id="old", question=question, route=route, prefix=prefix, text=text,
            detail=detail, status=status,
        )  # fmt: skip

    async def aclose(self):
        await self.service.aclose()
        await self.meteo.http.aclose()


@pytest.fixture
async def make_rig(conn, client, actions):
    rigs = []

    def make(**service_kwargs):
        rig = Rig(conn, client, actions, **service_kwargs)
        rigs.append(rig)
        return rig

    yield make
    for rig in rigs:
        await rig.aclose()


@pytest.fixture
def rig(make_rig):
    return make_rig()


# --- the contract ----------------------------------------------------------------------------------


def test_the_five_methods_of_the_plan_are_registered(rig):
    assert rig.rpc.methods() == METHODS


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"session_id": "s", "text": "x"},  # no ui_language
        {"session_id": "", "text": "x", "ui_language": "en"},
        {"session_id": 5, "text": "x", "ui_language": "en"},
        {"session_id": "s", "text": 5, "ui_language": "en"},
        {"session_id": "s", "text": None, "ui_language": "en"},
        {"session_id": "s", "text": "x", "ui_language": 5},
        {"session_id": "s", "text": "x", "ui_language": "en", "target_app": 5},
    ],
)
async def test_ask_checks_its_params(rig, client, params):
    with pytest.raises(RpcError) as e:
        await rig.call("agent.ask", params)
    assert e.value.code == INVALID_PARAMS
    assert client.calls == 0


@pytest.mark.parametrize(
    ("method", "params"),
    [
        ("agent.confirm", {}),
        ("agent.confirm", {"id": "x"}),
        ("agent.confirm", {"approved": True}),
        ("agent.confirm", {"id": 5, "approved": True}),
        ("agent.confirm", {"id": "x", "approved": "yes"}),
        ("agent.confirm", {"id": "x", "approved": 1}),
        ("agent.cancel", {}),
        ("agent.cancel", {"session_id": ""}),
        ("agent.cancel", {"session_id": 5}),
        ("agent.history.list", {"limit": "5"}),
        ("agent.history.list", {"limit": True}),
        ("agent.history.list", {"limit": 2.5}),
        ("agent.history.list", {"before_id": "5"}),
    ],
)
async def test_the_other_methods_check_their_params(rig, method, params):
    with pytest.raises(RpcError) as e:
        await rig.call(method, params)
    assert e.value.code == INVALID_PARAMS


@pytest.mark.parametrize(
    ("given", "prefix"),
    [("zh-TW", "了"), ("zh", "了"), ("zh-Hant-TW", "了"), ("ja", "了"), ("en", "Understood"),
     ("en-US", "Understood"), ("fr", "Understood")],
)  # fmt: skip
async def test_the_ui_language_is_taken_for_one_of_the_three(rig, client, given, prefix):
    client.decisions.append(decision("command", "volume_mute"))
    assert (await rig.ask("mute", language=given))["prefix"] == prefix


# --- fixed commands --------------------------------------------------------------------------------


async def test_a_command_is_done_at_once_and_answered_with_the_fixed_reply(rig, client, actions):
    client.decisions.append(decision("command", "open_youtube", cost=0.00004))
    result = await rig.ask("打開 YouTube")
    assert result == {"status": "done", "turn_id": 1, "prefix": "了", "text": ""}
    assert actions.calls == [("open_url", "https://www.youtube.com/")]
    assert client.json_calls == [] and client.text_calls == []  # no language model
    assert rig.spent() == [("agent", "typesafe/jev-1.14", 0.00004)]
    (turn,) = await rig.turns()
    assert turn == {
        "id": 1,
        "created_at_ms": NOW_MS,
        "question": "打開 YouTube",
        "route": "command",
        "prefix": "了",
        "text": "",
        "detail": None,
        "status": "done",
    }


@pytest.mark.parametrize(
    ("language", "prefix"), [("zh-TW", "了"), ("en", "Understood"), ("ja", "了")]
)
async def test_the_fixed_reply_is_in_the_ui_language(rig, client, language, prefix):
    client.decisions.append(decision("command", "lock_screen"))
    result = await rig.ask("lock", language=language)
    assert (result["prefix"], result["text"]) == (prefix, "")


async def test_the_command_a_request_names_is_the_one_that_runs(rig, client, actions):
    for name, call in [
        ("volume_up", ("press_key", "volume_up", 5)),
        ("volume_down", ("press_key", "volume_down", 5)),
        ("volume_mute", ("press_key", "volume_mute", 1)),
        ("media_play_pause", ("press_key", "play_pause", 1)),
        ("media_next", ("press_key", "next_track", 1)),
        ("media_previous", ("press_key", "previous_track", 1)),
        ("lock_screen", ("lock_screen",)),
        ("open_github", ("open_url", "https://github.com/")),
    ]:
        actions.calls.clear()
        client.decisions.append(decision("command", name))
        assert (await rig.ask("x"))["prefix"] == "了"
        assert actions.calls == [call], name


async def test_a_command_that_no_command_covers_is_refused_and_nothing_is_done(
    rig, client, actions
):
    client.decisions.append(decision("command", NONE))
    result = await rig.ask("把所有分頁都關掉")
    assert result["prefix"] == "否" and result["text"] == "目前無法執行此操作。"
    assert actions.calls == []
    assert (await rig.turns())[0]["route"] == "command"


async def test_a_command_whose_action_fails_is_said_to_have_failed(rig, client, actions):
    actions.fail["open_url"] = OSError("no browser: 秘密")
    client.decisions.append(decision("command", "open_youtube"))
    result = await rig.ask("打開 YouTube")
    assert (result["prefix"], result["text"]) == ("否", "執行失敗。")
    assert (await rig.turns())[0]["status"] == "error"


async def test_a_command_never_asks_for_a_confirmation_at_t1(rig, client, actions):
    client.decisions.append(decision("command", "lock_screen"))
    assert (await rig.ask("鎖定螢幕"))["status"] == "done"
    assert rig.service._confirmations.waiting == 0


# --- opening an app --------------------------------------------------------------------------------


async def test_an_app_named_in_the_request_is_opened(rig, client, actions):
    actions.apps = [StartApp("Spotify", "spotify!App"), StartApp("Notepad", "notepad.exe")]
    client.decisions.append(decision("command", "open_app"))
    result = await rig.ask("open spotify", language="en")
    assert result == {"status": "done", "turn_id": 1, "prefix": "Understood", "text": ""}
    assert ("launch_app", "spotify!App") in actions.calls
    assert client.json_calls == []  # the name was obvious: no language model


async def test_an_app_that_is_not_obvious_is_chosen_by_the_model_from_the_list(
    rig, client, actions
):
    actions.apps = [StartApp("計算機", "calc!App"), StartApp("Notepad", "notepad.exe")]
    client.decisions.append(decision("command", "open_app"))
    client.replies.append(completion({"app": "計算機"}))
    result = await rig.ask("open the calculator", language="en")
    assert result["prefix"] == "Understood"
    assert ("launch_app", "calc!App") in actions.calls
    prompt = client.json_calls[0]["user"]
    assert "open the calculator" in prompt and "計算機\nNotepad" in prompt
    assert [row[1] for row in rig.spent()] == ["typesafe/jev-1.14", "openai/gpt-6-luna"]


async def test_the_app_the_language_model_heard_while_routing_is_not_asked_for_again(
    rig, client, actions
):
    actions.apps = [StartApp("計算機", "calc!App")]
    client.decisions.append(decision("command", "open_app", route_confidence=0.2))  # unsure
    client.replies.append(
        completion(classification(route="command", command="open_app", app="計算機"))
    )
    await rig.ask("打開那個算數的東西")
    assert ("launch_app", "calc!App") in actions.calls
    assert len(client.json_calls) == 1 and len(client.decide_calls) == 1


async def test_an_app_that_is_not_installed_is_said_so_and_nothing_is_started(
    rig, client, actions
):
    actions.apps = [StartApp("Notepad", "notepad.exe")]
    client.decisions.append(decision("command", "open_app"))
    client.replies.append(completion({"app": ""}))
    result = await rig.ask("打開 Photoshop")
    assert (result["prefix"], result["text"]) == ("否", "找不到符合的應用程式。")
    assert not any(call[0] == "launch_app" for call in actions.calls)


async def test_a_model_that_fails_to_choose_the_app_is_a_failure_of_its_kind(
    rig, client, actions
):
    actions.apps = [StartApp("Notepad", "notepad.exe")]
    client.decisions.append(decision("command", "open_app"))
    client.replies.append(OpenRouterError(0, "network", "offline"))
    result = await rig.ask("open the thing", language="en")
    assert (result["prefix"], result["text"]) == ("Negative", "The network is unreachable.")


# --- answers ---------------------------------------------------------------------------------------


async def test_an_answer_with_the_weather_looks_it_up_for_the_city_and_asks_the_model_once(
    rig, client, actions
):
    client.decisions.append(decision("answer", weather=0.9, cost=0.00004))
    client.replies.append(spoken("答", "台北現在 24 度，多雲。", "今天 21 到 27 度。", cost=0.0009))
    result = await rig.ask("台北天氣如何")
    assert result == {"status": "done", "turn_id": 1, "prefix": "答", "text": "台北現在 24 度，多雲。"}
    geocoding, forecast = rig.meteo.requests
    assert geocoding.url.params["name"] == "Taipei"  # the default city 台北, which Open-Meteo lacks
    assert forecast.url.host == "api.open-meteo.com"
    assert len(client.json_calls) == 1
    call = client.json_calls[0]
    assert call["reasoning_effort"] == "none" and call["schema_name"] == "raphael_reply"
    assert 'weather: {"city": "Taipei", "asked_for": "台北"' in call["user"]
    assert "<question>\n台北天氣如何\n</question>" in call["user"]
    assert actions.calls == []
    (turn,) = await rig.turns()
    assert (turn["route"], turn["detail"], turn["status"]) == ("answer", "今天 21 到 27 度。", "done")
    assert rig.spent() == [
        ("agent", "typesafe/jev-1.14", 0.00004),
        ("agent", "openai/gpt-6-luna", 0.0009),
    ]


async def test_the_city_is_the_one_of_the_settings(rig, client):
    rig.settings.set("agent_city", "Reykjavik")
    client.decisions.append(decision("answer", weather=0.9))
    client.replies.append(spoken())
    await rig.ask("weather?", language="en")
    assert rig.meteo.requests[0].url.params["name"] == "Reykjavik"
    assert rig.meteo.requests[0].url.params["language"] == "en"


async def test_an_answer_with_a_search_pays_for_the_search_and_gives_the_model_its_result(
    rig, client
):
    client.decisions.append(decision("answer", search=0.9))
    client.texts.append(found("明天台北有雨。", cost=0.012, sources=[{"title": "CWA", "url": "u"}]))
    client.replies.append(spoken("告", "明天台北有雨。", ""))
    result = await rig.ask("明天台北會下雨嗎")
    assert result["prefix"] == "告"
    search = client.text_calls[0]
    assert search["user"] == "明天台北會下雨嗎" and search["model"] == "openai/gpt-6-luna"
    assert search["plugins"] == [{"id": "web", "max_results": 3}]
    assert 'web_search: {"answer": "明天台北有雨。"' in client.json_calls[0]["user"]
    assert [row[2] for row in rig.spent()] == [0.00004, 0.012, 0.0009]
    (turn,) = await rig.turns()
    assert turn["detail"] == "來源:\n- CWA u"  # the pages are kept in the history


async def test_an_answer_with_the_time_asks_the_computer(rig, client):
    client.decisions.append(decision("answer", time=0.9))
    client.replies.append(spoken("答", "現在是下午兩點五分。"))
    await rig.ask("現在幾點")
    user = client.json_calls[0]["user"]
    assert 'local_time: {"iso": "2026-10-20T14:05:09+08:00"' in user
    assert rig.meteo.requests == []


async def test_an_answer_that_needs_nothing_looked_up_goes_straight_to_the_model(rig, client):
    client.decisions.append(decision("answer"))
    client.replies.append(spoken("答", "四。"))
    await rig.ask("二加二")
    assert "Tool results" not in client.json_calls[0]["user"]
    assert rig.meteo.requests == [] and client.text_calls == []


async def test_the_weather_is_looked_up_for_the_city_the_question_names(rig, client):
    client.decisions.append(decision("answer", weather=0.9))
    client.replies.append(spoken("解", "東京は曇りです。"))
    await rig.ask("東京の天気は？", language="ja")
    geocoding, _ = rig.meteo.requests
    assert geocoding.url.params["name"] == "Tokyo"
    assert '"asked_for": "東京"' in client.json_calls[0]["user"]


async def test_a_question_that_names_no_city_gets_the_weather_of_the_settings(rig, client):
    rig.settings.set("agent_city", "高雄")
    client.decisions.append(decision("answer", weather=0.9))
    client.replies.append(spoken("答", "要帶傘。"))
    await rig.ask("明天要帶傘嗎")
    geocoding, _ = rig.meteo.requests
    assert geocoding.url.params["name"] == "Kaohsiung"


async def test_the_tools_an_answer_needs_run_together_not_one_after_another(rig, client):
    # Each waits for the other to have started: run one after the other, the first would wait
    # for ever (and give up after two seconds, with its data missing).
    weather_started, search_started = asyncio.Event(), asyncio.Event()

    async def weather_waits_for_the_search(request):
        weather_started.set()
        await asyncio.wait_for(search_started.wait(), 2)

    async def search_waits_for_the_weather(**kwargs):
        search_started.set()
        await asyncio.wait_for(weather_started.wait(), 2)
        return found()

    rig.meteo.on_request = weather_waits_for_the_search
    client.decisions.append(decision("answer", weather=0.9, search=0.9, time=0.9))
    client.texts.append(search_waits_for_the_weather)
    client.replies.append(spoken())
    await rig.ask("台北天氣怎樣，順便查一下明天的新聞")
    user = client.json_calls[0]["user"]
    assert 'weather: {"city"' in user and 'web_search: {"answer"' in user
    assert "local_time:" in user and "unavailable" not in user


async def test_a_tool_that_fails_does_not_stop_the_answer_which_is_told_it_is_missing(
    rig, client
):
    rig.meteo.down = True
    client.decisions.append(decision("answer", weather=0.9))
    client.replies.append(spoken("否", "查不到天氣資料。"))
    result = await rig.ask("台北天氣如何")
    assert result["text"] == "查不到天氣資料。"
    assert "weather: unavailable (network)" in client.json_calls[0]["user"]


async def test_an_answer_is_worded_in_the_ui_language(rig, client):
    client.decisions.append(decision("answer", time=0.9))
    client.replies.append(spoken("Answer", "It is 14:05.", ""))
    result = await rig.ask("what time is it", language="en")
    assert result["prefix"] == "Answer"
    assert "Reply ONLY in English" in client.json_calls[0]["system"]


async def test_an_answer_is_given_the_last_three_turns_as_context(rig, client):
    for n in range(5):
        rig.seed(f"question {n}", f"answer {n}")
    client.decisions.append(decision("answer"))
    client.replies.append(spoken())
    await rig.ask("那明天呢")
    user = client.json_calls[0]["user"]
    assert "question 2" in user and "question 4" in user and "question 1" not in user


# --- discussion and small talk -----------------------------------------------------------------------


async def test_a_discussion_is_given_the_last_ten_turns_as_context(rig, client):
    for n in range(12):
        rig.seed(f"question {n}", f"answer {n}")
    client.decisions.append(decision("discuss"))
    client.replies.append(spoken("答", "先從預算開始。", "展開說明。"))
    result = await rig.ask("我們來規劃週末")
    assert result["text"] == "先從預算開始。"
    call = client.json_calls[0]
    assert "think something through" in call["system"]
    user = call["user"]
    assert "question 2" in user and "question 11" in user and "question 1\n" not in user
    assert user.index("question 2") < user.index("question 11")  # oldest first
    assert client.text_calls == [] and rig.meteo.requests == []
    assert (await rig.turns())[0]["route"] == "discuss"


async def test_small_talk_is_a_short_reply_from_the_model(rig, client):
    client.decisions.append(decision("chat"))
    client.replies.append(spoken("答", "不客氣。"))
    result = await rig.ask("謝謝")
    assert (result["prefix"], result["text"]) == ("答", "不客氣。")
    assert "small talk" in client.json_calls[0]["system"]


async def test_the_context_holds_only_what_was_answered(rig, client):
    rig.seed("kept", "kept answer")
    rig.seed("refused", "no", status="denied")
    rig.seed("failed", "no", status="error")
    rig.seed("waiting", "ask", status="confirm")
    client.decisions.append(decision("discuss"))
    client.replies.append(spoken())
    await rig.ask("continue")
    user = client.json_calls[0]["user"]
    assert "kept" in user and "refused" not in user and "failed" not in user
    assert "waiting" not in user


# --- fixed refusals --------------------------------------------------------------------------------


async def test_a_request_too_garbled_to_act_on_gets_the_fixed_reply_with_no_model(rig, client):
    client.decisions.append(decision("unclear"))
    result = await rig.ask("呃那個")
    assert (result["prefix"], result["text"]) == ("否", "無法辨識指示。請再說一次。")
    assert client.json_calls == []
    assert (await rig.turns())[0]["route"] == "unclear"


async def test_a_request_to_do_a_task_in_the_background_is_refused_honestly(rig, client):
    client.decisions.append(decision("delegate"))
    result = await rig.ask("幫我整理本週的信件並回覆")
    assert result["prefix"] == "否"
    assert result["text"] == "背景任務與外部服務尚未開放。此項目前無法執行。"
    assert client.json_calls == []
    assert (await rig.turns())[0]["route"] == "delegate"


@pytest.mark.parametrize(
    ("language", "text"),
    [
        (
            "en",
            "Background tasks and connected services are not yet available. "
            "This cannot be done now.",
        ),
        ("ja", "バックグラウンド処理と外部サービスは未対応です。現時点では実行できません。"),
    ],
)
async def test_the_refusal_is_in_the_ui_language(rig, client, language, text):
    client.decisions.append(decision("delegate"))
    result = await rig.ask("x", language=language)
    assert result["text"] == text


@pytest.mark.parametrize("text", ["", "   ", "\n"])
async def test_an_empty_request_is_not_understood_and_costs_nothing(rig, client, text):
    result = await rig.ask(text)
    assert (result["prefix"], result["text"]) == ("否", "無法辨識指示。請再說一次。")
    assert client.calls == 0


# --- the router's settings and fallbacks -----------------------------------------------------------


async def test_a_router_choice_an_older_version_saved_is_ignored_and_jev_routes(rig, client):
    # The setting `agent_router` is gone: Jev always routes. A saved "luna" must neither break the
    # start nor skip Jev.
    rig.conn.execute("INSERT INTO settings (key, value) VALUES ('agent_router', '\"luna\"')")
    client.decisions.append(decision("chat"))
    client.replies.append(spoken("答", "你好。"))
    result = await rig.ask("你好")
    assert result["text"] == "你好。"
    assert len(client.decide_calls) == 1 and len(client.json_calls) == 1  # the answer only


async def test_when_jev_fails_the_language_model_routes(rig, client):
    client.decisions.extend([OpenRouterError(503, "server", "down")] * 2)
    client.replies.append(completion(classification(route="chat")))
    client.replies.append(spoken("答", "你好。"))
    assert (await rig.ask("你好"))["text"] == "你好。"


async def test_the_model_of_the_settings_answers(rig, client):
    rig.settings.set("agent_model", "anthropic/claude-haiku-5.5")
    client.decisions.append(decision("chat"))
    client.replies.append(spoken())
    await rig.ask("你好")
    assert client.json_calls[0]["model"] == "anthropic/claude-haiku-5.5"


@pytest.mark.parametrize(
    ("key", "saved"),
    [("agent_model", '"  "'), ("agent_city", '""')],
)
async def test_a_saved_setting_that_is_not_valid_reads_as_its_default(rig, client, key, saved):
    rig.conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (key, saved))
    client.decisions.append(decision("answer", weather=0.9))
    client.replies.append(spoken())
    await rig.ask("天氣")
    assert client.json_calls[0]["model"] == "openai/gpt-6-luna"
    assert rig.meteo.requests[0].url.params["name"] == "Taipei"
    assert len(client.decide_calls) == 1  # jev


# --- no key, no budget, no network -------------------------------------------------------------------


async def test_without_a_key_the_commands_still_work_and_the_rest_is_refused_with_the_reason(
    rig, client, actions
):
    rig.keys.clear()
    assert (await rig.ask("靜音"))["prefix"] == "了"
    assert actions.calls == [("press_key", "volume_mute", 1)]
    result = await rig.ask("台北天氣如何")
    assert (result["prefix"], result["text"]) == ("否", "未設定 OpenRouter 金鑰。")
    assert client.calls == 0 and rig.meteo.requests == []
    assert [t["status"] for t in await rig.turns()] == ["error", "done"]


async def test_over_the_monthly_limit_the_commands_still_work_and_the_rest_is_refused(
    rig, client, actions
):
    rig.settings.set("monthly_limit_usd", 1)
    rig.spend.record(purpose="cleanup", model="m", cost_usd=1.0, prompt_tokens=1, completion_tokens=1)
    assert (await rig.ask("open youtube", language="en"))["prefix"] == "Understood"
    assert actions.calls == [("open_url", "https://www.youtube.com/")]
    result = await rig.ask("what is the weather", language="en")
    assert (result["prefix"], result["text"]) == ("Negative", "This month's limit has been reached.")
    assert client.calls == 0


async def test_offline_the_commands_still_work_and_the_rest_is_refused_with_the_reason(
    rig, client, actions
):
    network = OpenRouterError(0, "network", "offline")
    client.decisions.extend([network, network])
    client.replies.extend([network, network])
    assert (await rig.ask("打開 YouTube"))["prefix"] == "了"  # by the list of words
    assert actions.calls == [("open_url", "https://www.youtube.com/")]
    result = await rig.ask("台北天氣如何")
    assert (result["prefix"], result["text"]) == ("否", "無法連線至網路。")


async def test_a_request_the_list_of_words_cannot_find_is_not_guessed_at(rig, client, actions):
    rig.keys.clear()
    result = await rig.ask("幫我打開 YouTube 然後搜尋貓咪影片")
    assert result["prefix"] == "否" and actions.calls == []


async def test_a_key_store_that_fails_is_a_key_that_does_not_work(rig, client, caplog):
    class Broken:
        def get(self):
            raise RuntimeError(f"vault locked: {KEY}")

    rig.service._keys = Broken()
    with caplog.at_level(logging.DEBUG):
        result = await rig.ask("你好")
    assert (result["prefix"], result["text"]) == ("否", "OpenRouter 金鑰無法使用。")
    assert client.calls == 0 and KEY not in caplog.text


async def test_a_key_openrouter_refuses_is_said_so_and_the_model_is_not_asked_twice(rig, client):
    client.decisions.append(OpenRouterError(401, "auth", "bad key"))
    result = await rig.ask("你好")
    assert result["text"] == "OpenRouter 金鑰無法使用。" and client.json_calls == []


async def test_a_model_that_fails_after_jev_decided_is_a_failure_but_jev_is_paid_for(
    rig, client
):
    client.decisions.append(decision("answer", cost=0.00004))
    client.replies.append(OpenRouterError(0, "timeout", "slow"))
    result = await rig.ask("什麼是量子糾纏")
    assert (result["prefix"], result["text"]) == ("否", "無法連線至網路。")
    (turn,) = await rig.turns()
    assert turn["status"] == "error" and turn["route"] == "answer"
    assert rig.spent() == [("agent", "typesafe/jev-1.14", 0.00004)]


async def test_every_failure_is_an_answer_never_an_rpc_error(rig, client):
    client.decisions.append(RuntimeError("something nobody planned for"))
    client.replies.append(RuntimeError("nor this"))
    result = await rig.ask("你好")
    assert result["status"] == "done" and result["prefix"] == "否"


# --- confirmations -----------------------------------------------------------------------------------


async def ask_to_copy(rig, client, text="把答案複製起來"):
    client.decisions.append(decision("command", "copy_text"))
    return await rig.ask(text)


async def test_copying_asks_first_and_touches_nothing(rig, client, actions):
    rig.seed("台北天氣", "台北現在 24 度。")
    result = await ask_to_copy(rig, client)
    confirm = result["confirm"]
    assert result["status"] == "confirm" and result["turn_id"] == 2
    assert str(uuid.UUID(confirm["id"])) == confirm["id"]
    assert confirm == {
        "id": confirm["id"],
        "tier": "T2",
        "prefix": "問",
        "text": "是否將上一則回答複製到剪貼簿？",
        "yes": "是",
        "no": "否",
        "timeout_ms": 30000,
    }
    assert actions.calls == []
    assert (await rig.turns())[0]["status"] == "confirm"


@pytest.mark.parametrize(
    ("language", "prefix", "text", "yes", "no"),
    [
        ("en", "Question", "Copy my last answer to the clipboard?", "Yes", "No"),
        ("ja", "問", "直前の回答をクリップボードにコピーしますか？", "はい", "いいえ"),
    ],
)
async def test_the_question_is_asked_in_the_ui_language(
    rig, client, language, prefix, text, yes, no
):
    rig.seed("q", "a")
    client.decisions.append(decision("command", "copy_text"))
    confirm = (await rig.ask("copy", language=language))["confirm"]
    assert (confirm["prefix"], confirm["text"], confirm["yes"], confirm["no"]) == (
        prefix,
        text,
        yes,
        no,
    )


async def test_a_yes_copies_the_last_answer_once(rig, client, actions):
    rig.seed("台北天氣", "台北現在 24 度。")
    asked = await ask_to_copy(rig, client)
    done = await rig.confirm(asked["confirm"]["id"], True)
    assert done == {"status": "done", "turn_id": 2, "prefix": "了", "text": "已複製到剪貼簿。"}
    assert actions.calls == [("copy_to_clipboard", "台北現在 24 度。")]
    turn = (await rig.turns())[0]
    assert (turn["prefix"], turn["text"], turn["status"]) == ("了", "已複製到剪貼簿。", "done")
    # the same id again: nothing more happens
    again = await rig.confirm(asked["confirm"]["id"], True)
    assert again["prefix"] == "否" and len(actions.calls) == 1


async def test_what_is_copied_is_the_answer_and_what_its_detail_adds(rig, client, actions):
    rig.seed("q", "台北現在 24 度。", detail="今天最高 27 度，最低 21 度。\n\n來源:\n- CWA u")
    asked = await ask_to_copy(rig, client)
    await rig.confirm(asked["confirm"]["id"], True)
    assert actions.calls == [
        ("copy_to_clipboard", "台北現在 24 度。\n今天最高 27 度，最低 21 度。\n\n來源:\n- CWA u")
    ]


async def test_an_answer_whose_line_was_cut_is_copied_whole_once(rig, client, actions):
    rig.seed("q", "第一句。第二句很長…", detail="第一句。第二句很長很長，到這裡才完。")
    asked = await ask_to_copy(rig, client)
    await rig.confirm(asked["confirm"]["id"], True)
    assert actions.calls == [("copy_to_clipboard", "第一句。第二句很長很長，到這裡才完。")]


async def test_the_last_answer_is_the_last_one_that_was_given_not_a_refusal_or_a_command(
    rig, client, actions
):
    rig.seed("q", "the answer")
    rig.seed("ctl", "", route="command", prefix="了")
    rig.seed("nope", "refusal", prefix="否", status="error")
    asked = await ask_to_copy(rig, client)
    await rig.confirm(asked["confirm"]["id"], True)
    assert actions.calls == [("copy_to_clipboard", "the answer")]


async def test_a_text_the_speaker_quotes_is_what_is_copied(rig, client, actions):
    rig.seed("q", "the answer")
    result = await ask_to_copy(rig, client, "把「今天會議在三點」複製起來")
    assert result["confirm"]["text"] == "是否將引號內的文字複製到剪貼簿？"
    await rig.confirm(result["confirm"]["id"], True)
    assert actions.calls == [("copy_to_clipboard", "今天會議在三點")]


@pytest.mark.parametrize(
    ("said", "copied"),
    [
        ('copy "see you at three"', "see you at three"),
        ("copy “see you at three”", "see you at three"),
        ("コピーして『三時に会いましょう』", "三時に会いましょう"),
        ("copy 'it''s fine'", None),  # not a quotation: the last answer is copied
    ],
)
async def test_the_ways_a_text_is_quoted(rig, client, actions, said, copied):
    rig.seed("q", "the answer")
    result = await ask_to_copy(rig, client, said)
    await rig.confirm(result["confirm"]["id"], True)
    assert actions.calls == [("copy_to_clipboard", copied or "the answer")]


async def test_a_no_copies_nothing_and_says_it_understood(rig, client, actions):
    rig.seed("q", "a")
    asked = await ask_to_copy(rig, client)
    done = await rig.confirm(asked["confirm"]["id"], False)
    assert done == {"status": "done", "turn_id": 2, "prefix": "了", "text": "已取消。"}
    assert actions.calls == []
    assert (await rig.turns())[0]["status"] == "denied"


async def test_a_yes_that_comes_too_late_does_nothing_and_says_so(rig, client, actions):
    rig.seed("q", "a")
    asked = await ask_to_copy(rig, client)
    rig.clock.now += 30
    done = await rig.confirm(asked["confirm"]["id"], True)
    assert done == {"status": "done", "turn_id": 2, "prefix": "否", "text": "逾時未獲回應。未執行。"}
    assert actions.calls == []
    assert (await rig.turns())[0]["status"] == "expired"


async def test_a_yes_a_moment_before_thirty_seconds_still_counts(rig, client, actions):
    rig.seed("q", "a")
    asked = await ask_to_copy(rig, client)
    rig.clock.now += 29.5
    assert (await rig.confirm(asked["confirm"]["id"], True))["prefix"] == "了"
    assert len(actions.calls) == 1


async def test_a_question_nobody_answered_is_expired_in_the_history_without_being_asked(
    rig, client, actions
):
    rig.seed("q", "a")
    await ask_to_copy(rig, client)
    rig.clock.now += 31
    (turn,) = [t for t in await rig.turns() if t["route"] == "command"]
    assert turn["status"] == "expired" and turn["prefix"] == "否"
    assert actions.calls == []


async def test_an_id_nobody_issued_is_a_refusal(rig, actions):
    done = await rig.confirm("00000000-0000-0000-0000-000000000000", True)
    assert done["status"] == "done" and done["prefix"] == "否" and done["turn_id"] == 0
    assert actions.calls == []


async def test_the_refusal_for_an_unknown_id_is_in_the_language_last_spoken(rig, client):
    client.decisions.append(decision("chat"))
    client.replies.append(spoken("Answer", "Hello."))
    await rig.ask("hi", language="en")
    done = await rig.confirm("nope", True)
    assert (done["prefix"], done["text"]) == ("Negative", "No response in time. Nothing was executed.")


async def test_cancelling_the_session_denies_its_question_and_a_late_yes_does_nothing(
    rig, client, actions
):
    rig.seed("q", "a")
    asked = await ask_to_copy(rig, client)
    assert await rig.call("agent.cancel", {"session_id": "s1"}) == {}
    assert (await rig.turns())[0]["status"] == "denied"
    late = await rig.confirm(asked["confirm"]["id"], True)
    assert late["prefix"] == "否" and actions.calls == []


async def test_cancelling_another_session_leaves_the_question_alone(rig, client, actions):
    rig.seed("q", "a")
    asked = await ask_to_copy(rig, client)
    assert await rig.call("agent.cancel", {"session_id": "somebody else"}) == {}
    assert (await rig.confirm(asked["confirm"]["id"], True))["prefix"] == "了"


async def test_a_new_request_replaces_a_question_that_waits(rig, client, actions):
    rig.seed("q", "a")
    asked = await ask_to_copy(rig, client)
    client.decisions.append(decision("chat"))
    client.replies.append(spoken("答", "好。"))
    await rig.ask("你好", session="s2")
    late = await rig.confirm(asked["confirm"]["id"], True)
    assert late["prefix"] == "否" and actions.calls == []
    assert any(t["status"] == "denied" for t in await rig.turns())


async def test_a_question_that_ran_out_before_a_new_request_is_expired_not_denied(
    rig, client, actions
):
    rig.seed("q", "a")
    asked = await ask_to_copy(rig, client)
    rig.clock.now += 31
    client.decisions.append(decision("chat"))
    client.replies.append(spoken("答", "好。"))
    await rig.ask("你好", session="s2")
    row = next(t for t in await rig.turns() if t["id"] == asked["turn_id"])
    assert row["status"] == "expired" and row["prefix"] == "否"
    assert (await rig.confirm(asked["confirm"]["id"], True))["text"] == "逾時未獲回應。未執行。"
    assert actions.calls == []


async def test_nothing_to_copy_is_said_and_nothing_is_asked(rig, client, actions):
    result = await ask_to_copy(rig, client)
    assert result["status"] == "done"
    assert (result["prefix"], result["text"]) == ("否", "無可複製的內容。")
    assert rig.service._confirmations.waiting == 0 and actions.calls == []


async def test_small_talk_is_not_what_copy_copies(rig, client, actions):
    rig.seed("台北天氣？", "台北晴，25 度。")
    rig.seed("謝謝", "不客氣。", route="chat", prefix="了")
    asked = await ask_to_copy(rig, client)
    await rig.confirm(asked["confirm"]["id"], True)
    assert actions.calls == [("copy_to_clipboard", "台北晴，25 度。")]


async def test_a_copy_that_fails_after_the_yes_is_said_to_have_failed(rig, client, actions):
    rig.seed("q", "a")
    actions.fail["copy_to_clipboard"] = OSError("the clipboard is busy")
    asked = await ask_to_copy(rig, client)
    done = await rig.confirm(asked["confirm"]["id"], True)
    assert (done["prefix"], done["text"]) == ("否", "執行失敗。")
    assert (await rig.turns())[0]["status"] == "error"


async def test_an_irreversible_command_is_asked_as_t3(make_rig, client, actions, monkeypatch):
    monkeypatch.setitem(
        commands.COMMANDS, "burn", Command("burn", Tier.T3, "Burn it all down.", "copy")
    )
    rig = make_rig()
    rig.seed("q", "a")
    # Jev does not know the new command: the language model routes, with the command list of now.
    client.decisions.append(decision("command", route_confidence=0.2))
    client.replies.append(completion(classification(route="command", command="burn")))
    result = await rig.ask("burn it")
    assert result["confirm"]["tier"] == "T3"
    assert actions.calls == []
    assert (await rig.confirm(result["confirm"]["id"], True))["prefix"] == "了"
    assert actions.calls == [("copy_to_clipboard", "a")]


async def test_nothing_at_t2_or_above_runs_on_the_strength_of_a_spoken_request(
    rig, client, actions
):
    rig.seed("q", "a")
    for _ in range(3):
        await ask_to_copy(rig, client)
    assert actions.calls == []


# --- cancelling a request that is still being thought about ------------------------------------------


async def test_cancelling_a_request_that_is_still_thinking_ends_it_and_nothing_is_done(
    make_rig, client, actions
):
    rig = make_rig(decider=Decider(client, jev_total_s=10.0, jev_first_s=10.0))

    async def hang(**kwargs):
        await asyncio.sleep(30)

    client.decisions.append(hang)
    asking = asyncio.ensure_future(rig.ask("打開 YouTube"))
    await until(lambda: client.decide_calls)
    assert await rig.call("agent.cancel", {"session_id": "s1"}) == {}
    result = await asyncio.wait_for(asking, 2)
    assert result == {"status": "done", "turn_id": 0, "prefix": "了", "text": "已取消。"}
    assert actions.calls == [] and await rig.turns() == []


async def test_a_request_that_arrives_after_its_cancel_is_cancelled_and_runs_nothing(
    rig, client, actions
):
    # Esc and the request crossed on the pipe: the cancel came first.
    assert await rig.call("agent.cancel", {"session_id": "s1"}) == {}
    result = await rig.ask("打開 YouTube")
    assert result == {"status": "done", "turn_id": 0, "prefix": "了", "text": "已取消。"}
    assert client.decide_calls == [] and actions.calls == [] and await rig.turns() == []


async def test_a_cancel_holds_only_its_own_session_and_only_for_a_while(rig, client, actions):
    await rig.call("agent.cancel", {"session_id": "s1"})
    client.decisions.append(decision("command", command="open_youtube"))
    assert (await rig.ask("打開 YouTube", session="s2"))["prefix"] == "了"
    assert actions.calls == [("open_url", "https://www.youtube.com/")]
    rig.clock.now += service.CANCELLED_MEMORY_S
    client.decisions.append(decision("command", command="open_youtube"))
    await rig.ask("打開 YouTube", session="s1")
    assert len(actions.calls) == 2


async def test_cancelling_another_session_does_not_end_a_request(make_rig, client, actions):
    rig = make_rig(decider=Decider(client, jev_total_s=10.0, jev_first_s=10.0))
    gate = asyncio.Event()

    async def slow(**kwargs):
        await gate.wait()
        return decision("command", "lock_screen")

    client.decisions.append(slow)
    asking = asyncio.ensure_future(rig.ask("鎖定螢幕"))
    await until(lambda: client.decide_calls)
    await rig.call("agent.cancel", {"session_id": "other"})
    gate.set()
    assert (await asyncio.wait_for(asking, 2))["prefix"] == "了"
    assert actions.calls == [("lock_screen",)]


async def test_cancelling_the_handler_itself_is_not_mistaken_for_a_cancel_request(
    make_rig, client
):
    rig = make_rig(decider=Decider(client, jev_total_s=10.0, jev_first_s=10.0))

    async def hang(**kwargs):
        await asyncio.sleep(30)

    client.decisions.append(hang)
    asking = asyncio.ensure_future(rig.ask("你好"))
    await until(lambda: client.decide_calls)
    asking.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asking


async def test_a_second_request_of_a_session_replaces_the_first_that_is_still_going(
    make_rig, client, actions
):
    rig = make_rig(decider=Decider(client, jev_total_s=10.0, jev_first_s=10.0))

    async def hang(**kwargs):
        await asyncio.sleep(30)

    client.decisions.extend([hang, decision("command", "lock_screen")])
    first = asyncio.ensure_future(rig.ask("鎖定螢幕"))
    await until(lambda: client.decide_calls)
    second = await rig.ask("鎖定螢幕")
    assert second["prefix"] == "了"
    assert (await asyncio.wait_for(first, 2))["turn_id"] == 0
    assert actions.calls == [("lock_screen",)]  # once


# --- the history -------------------------------------------------------------------------------------


async def test_the_history_lists_the_newest_turn_first_with_the_fields_of_the_plan(rig, client):
    for n in range(3):
        client.decisions.append(decision("command", "volume_mute"))
        await rig.ask(f"靜音 {n}")
    turns = await rig.turns()
    assert [t["question"] for t in turns] == ["靜音 2", "靜音 1", "靜音 0"]
    assert [t["id"] for t in turns] == [3, 2, 1]
    assert set(turns[0]) == {
        "id", "created_at_ms", "question", "route", "prefix", "text", "detail", "status"
    }  # fmt: skip


async def test_the_history_pages_by_limit_and_before_id(rig):
    for n in range(7):
        rig.seed(f"q{n}", "a")
    first = await rig.turns(limit=3)
    assert [t["question"] for t in first] == ["q6", "q5", "q4"]
    second = await rig.turns(limit=3, before_id=first[-1]["id"])
    assert [t["question"] for t in second] == ["q3", "q2", "q1"]
    assert [t["question"] for t in await rig.turns(before_id=second[-1]["id"])] == ["q0"]


async def test_the_history_gives_fifty_turns_by_default_and_at_most_two_hundred(rig):
    for n in range(210):
        rig.seed(f"q{n}", "a")
    assert len(await rig.turns()) == 50
    assert len(await rig.turns(limit=1000)) == 200
    assert len(await rig.turns(limit=0)) == 1  # at least one: a limit of 0 or less is not "all"
    assert len(await rig.turns(limit=-5)) == 1


async def test_clearing_the_history_deletes_every_turn_and_counts_them(rig):
    for n in range(4):
        rig.seed(f"q{n}", "a")
    assert await rig.call("agent.history.clear", {}) == {"deleted": 4}
    assert await rig.turns() == []
    assert await rig.call("agent.history.clear", {}) == {"deleted": 0}


async def test_a_cleared_history_is_no_context(rig, client):
    rig.seed("secret question", "secret answer")
    await rig.call("agent.history.clear", {})
    client.decisions.append(decision("discuss"))
    client.replies.append(spoken())
    await rig.ask("continue")
    assert "secret" not in client.json_calls[0]["user"]


async def test_the_turns_older_than_the_retention_are_deleted(rig, client):
    rig.settings.set("history_retention_days", 7)
    old = rig.seed("old", "a")
    rig.wall.now += 8 * 86_400_000
    fresh = rig.seed("fresh", "a")
    client.decisions.append(decision("command", "volume_mute"))
    await rig.ask("靜音")
    questions = [t["question"] for t in await rig.turns()]
    assert "old" not in questions and "fresh" in questions and old != fresh


async def test_a_question_that_died_with_the_last_process_is_expired_when_the_core_starts(
    make_rig, conn
):
    seeded = AgentHistory(conn, Clock(NOW_MS))
    seeded.add(
        session_id="gone", question="q", route="command", prefix="問", text="?", status="confirm"
    )
    rig = make_rig()
    assert (await rig.turns())[0]["status"] == "expired"


async def test_a_history_that_cannot_be_written_does_not_lose_the_answer(rig, client, caplog):
    client.decisions.append(decision("chat"))
    client.replies.append(spoken("答", "你好。"))
    rig.conn.execute("DROP TABLE agent_turns")
    with caplog.at_level(logging.WARNING):
        result = await rig.ask("你好")
    assert result == {"status": "done", "turn_id": 0, "prefix": "答", "text": "你好。"}
    assert "OperationalError" in caplog.text


# --- costs -------------------------------------------------------------------------------------------


async def test_the_cost_of_a_turn_is_everything_it_spent(rig, client):
    client.decisions.append(decision("answer", search=0.9, cost=0.00004))
    client.texts.append(found(cost=0.012))
    client.replies.append(spoken(cost=0.0009))
    await rig.ask("明天有雨嗎")
    row = rig.conn.execute("SELECT cost_usd FROM agent_turns").fetchone()
    assert row[0] == pytest.approx(0.00004 + 0.012 + 0.0009)
    assert rig.spend.month_total() == pytest.approx(row[0])
    assert {purpose for purpose, _, _ in rig.spent()} == {"agent"}


async def test_a_confirmed_turn_adds_what_the_confirmation_cost(rig, client):
    rig.seed("q", "a")
    asked = await ask_to_copy(rig, client)
    await rig.confirm(asked["confirm"]["id"], True)
    assert rig.conn.execute("SELECT cost_usd FROM agent_turns WHERE id = 2").fetchone()[0] == (
        pytest.approx(0.00004)
    )


# --- what is kept out of the log ---------------------------------------------------------------------


async def test_no_question_or_answer_or_city_is_logged(rig, client, caplog):
    rig.settings.set("agent_city", "我家那條秘密巷子")
    rig.meteo.down = True
    client.decisions.append(decision("answer", weather=0.9, search=0.9))
    client.texts.append(OpenRouterError(503, "server", "秘密 message"))
    client.replies.append(spoken("答", "這是秘密的答案。"))
    with caplog.at_level(logging.DEBUG):
        await rig.ask("我的秘密問題")
    assert "秘密" not in caplog.text and KEY not in caplog.text


# --- what nobody planned for -------------------------------------------------------------------------


async def test_a_bug_in_a_route_is_a_refusal_the_shell_can_show_not_an_rpc_error(
    rig, client, caplog
):
    async def broken(**kwargs):
        raise ValueError("a bug with the question 秘密 in it")

    rig.service._voice.reply = broken
    client.decisions.append(decision("chat"))
    with caplog.at_level(logging.DEBUG):
        result = await rig.ask("你好")
    assert (result["status"], result["prefix"], result["text"]) == (
        "done",
        "否",
        "處理過程中發生異常。請再試一次。",
    )
    assert "ValueError" in caplog.text and "秘密" not in caplog.text
    assert (await rig.turns())[0]["status"] == "error"


async def test_a_bug_in_the_router_is_a_failure_too_and_a_command_is_still_found(
    rig, client, actions
):
    async def broken(*args, **kwargs):
        raise KeyError("nobody planned for this")

    rig.service._decider.classify = broken
    assert (await rig.ask("靜音"))["prefix"] == "了"
    assert actions.calls == [("press_key", "volume_mute", 1)]
    assert (await rig.ask("你好"))["prefix"] == "否"
