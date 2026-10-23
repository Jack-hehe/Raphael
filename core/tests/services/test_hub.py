"""The services as the settings page sees them: the choices in the settings, `services.status`,
the keys (`services.set_key`, `services.clear_key`), which never come back in an answer or reach a
log, and the addresses a text holds."""

import logging

import keyring
import pytest

from raphael_core.brain.catalog import parse_catalog
from raphael_core.brain.secrets import MemoryKeyStore
from raphael_core.brain.service import Brain
from raphael_core.brain.settings import SettingsStore
from raphael_core.protocol import INVALID_PARAMS, RpcError
from raphael_core.router import Router
from raphael_core.services.hub import ServiceHub, offline_transport
from raphael_core.services.keys import MAX_KEY_CHARS, credential_keys, memory_keys
from raphael_core.services.urls import SeenUrls, page_key, urls_in

from brain.test_catalog import RECORDED, Clock, FakeModelsClient, edited

KEY = "BSA-the-owners-brave-key-0123456789"


class Rig:
    def __init__(self, conn, models=None):
        self.settings = SettingsStore(conn)
        self.keys = memory_keys()
        self.hub = ServiceHub(
            conn, object(), self.keys, models=models, transport=offline_transport()
        )
        self.rpc = Router()
        self.hub.register(self.rpc)

    async def call(self, method, params=None):
        return await self.rpc.call(method, params)


@pytest.fixture
def rig(conn):
    return Rig(conn, models=lambda: parse_catalog(RECORDED))


def option(view, service):
    return next(o for o in view["options"] if o["id"] == service)


# --- services.status ----------------------------------------------------------------------------


async def test_the_status_says_what_is_chosen_and_what_each_service_needs(rig):
    status = await rig.call("services.status")
    assert status == {
        "search": {
            "enabled": True,
            "service": "openrouter",
            "options": [
                {"id": "openrouter", "needs_key": False, "has_key": False, "key_optional": False},
                {"id": "brave", "needs_key": True, "has_key": False, "key_optional": False},
                {"id": "tavily", "needs_key": True, "has_key": False, "key_optional": False},
                {"id": "exa", "needs_key": True, "has_key": False, "key_optional": False},
                {"id": "searxng", "needs_key": False, "has_key": False, "key_optional": False,
                 "url_set": False},
            ],
        },
        "read": {
            "enabled": True,
            "service": "builtin",
            "options": [
                {"id": "builtin", "needs_key": False, "has_key": False, "key_optional": False},
                {"id": "jina", "needs_key": False, "has_key": False, "key_optional": True},
                {"id": "firecrawl", "needs_key": True, "has_key": False, "key_optional": False},
                {"id": "tavily", "needs_key": True, "has_key": False, "key_optional": False},
            ],
        },
        "pdf": {
            "enabled": True,
            "service": "builtin",
            "options": [
                {"id": "builtin", "needs_key": False, "has_key": False, "key_optional": False},
                {"id": "openrouter", "needs_key": False, "has_key": False, "key_optional": False},
            ],
        },
        "vision": {
            "enabled": True,
            "model": "",
            "resolved_model": "openai/gpt-6-luna",
            "supports_images": True,
        },
        "voice": {
            "enabled": False,
            "service": "fish",
            "options": [
                {"id": "fish", "needs_key": True, "has_key": False, "key_optional": False},
            ],
            "fish_voice_set": False,
        },
    }  # fmt: skip


async def test_the_status_follows_the_settings(rig, conn):
    rig.settings.set("web_search_service", "searxng")
    rig.settings.set("searxng_url", "http://192.168.1.20:8080")
    rig.settings.set("web_read_service", "tavily")
    rig.settings.set("pdf_service", "openrouter")
    rig.settings.set("vision_model", "anthropic/claude-haiku-5.5")
    status = await rig.call("services.status")
    assert status["search"]["service"] == "searxng"
    assert option(status["search"], "searxng")["url_set"] is True
    assert (status["read"]["service"], status["pdf"]["service"]) == ("tavily", "openrouter")
    assert status["vision"] == {
        "enabled": True,
        "model": "anthropic/claude-haiku-5.5",
        "resolved_model": "anthropic/claude-haiku-5.5",
        "supports_images": True,
    }


async def test_whether_a_model_reads_images_is_unknown_without_the_list(conn):
    rig = Rig(conn, models=lambda: None)
    assert (await rig.call("services.status"))["vision"]["supports_images"] is None


async def test_a_model_the_list_says_reads_no_images_is_said_so(conn):
    text_only = {"input_modalities": ["text"], "output_modalities": ["text"]}
    listed = parse_catalog(edited("openai/gpt-6-luna", architecture=text_only))
    rig = Rig(conn, models=lambda: listed)
    assert (await rig.call("services.status"))["vision"]["supports_images"] is False


# --- the keys -----------------------------------------------------------------------------------


async def test_a_key_is_stored_trimmed_never_answered_and_never_logged(rig, caplog):
    caplog.set_level(logging.DEBUG)
    reply = await rig.call("services.set_key", {"service": "brave", "key": f"  {KEY}\n"})
    assert reply == {"ok": True}
    assert rig.keys.get("brave") == KEY
    status = await rig.call("services.status")
    assert option(status["search"], "brave")["has_key"] is True
    assert KEY not in str(status) and KEY not in caplog.text
    assert await rig.call("services.clear_key", {"service": "brave"}) == {"ok": True}
    assert rig.keys.get("brave") is None
    assert KEY not in caplog.text


async def test_tavily_has_one_key_for_its_search_and_its_reader(rig):
    await rig.call("services.set_key", {"service": "tavily", "key": "tvly-1"})
    status = await rig.call("services.status")
    assert option(status["search"], "tavily")["has_key"] and option(status["read"], "tavily")["has_key"]


@pytest.mark.parametrize(
    ("key", "kind"),
    [("   ", "blank"), ("x" * (MAX_KEY_CHARS + 1), "long"), ("two words", "characters"),
     ("line\x00break", "characters")],
)  # fmt: skip
async def test_a_key_that_is_none_is_refused_by_its_kind_and_not_echoed(rig, key, kind, caplog):
    with pytest.raises(RpcError) as caught:
        await rig.call("services.set_key", {"service": "exa", "key": key})
    assert caught.value.code == INVALID_PARAMS and caught.value.data == {"kind": kind}
    if key.strip():
        assert key not in caught.value.message and key not in caplog.text
    assert rig.keys.get("exa") is None


async def test_a_key_of_the_longest_length_is_taken(rig):
    await rig.call("services.set_key", {"service": "exa", "key": "k" * MAX_KEY_CHARS})
    assert rig.keys.has("exa")


@pytest.mark.parametrize("service", ["openrouter", "searxng", "builtin", "nonsense", None, 3])
async def test_only_a_keyed_service_has_a_key(rig, service):
    with pytest.raises(RpcError) as caught:
        await rig.call("services.set_key", {"service": service, "key": KEY})
    assert caught.value.code == INVALID_PARAMS and KEY not in caught.value.message
    with pytest.raises(RpcError):
        await rig.call("services.clear_key", {"service": service})


def test_each_key_is_a_credential_of_its_own_in_the_vault():
    keys = credential_keys()
    keys.set("brave", "b-key")
    keys.set("jina", "j-key")
    keys.set("brave", "b-key-2")
    assert keyring.get_password("Raphael service brave", "key") == "b-key-2"
    assert keyring.get_password("Raphael service jina", "key") == "j-key"
    keys.clear("brave")
    assert keyring.get_password("Raphael service brave", "key") is None and keys.get("brave") is None


def test_a_key_store_that_fails_reads_as_no_key(caplog):
    class Broken(MemoryKeyStore):
        def get(self):
            raise RuntimeError(f"vault said {KEY}")

    from raphael_core.services.keys import ServiceKeys

    keys = ServiceKeys(lambda service: Broken())
    assert keys.get("brave") is None and KEY not in caplog.text


# --- Raphael's voice ----------------------------------------------------------------------------

FISH_KEY = "fish-the-owners-key-0123456789abcdef"
FISH_VOICE = "0123456789abcdef0123456789abcdef"


async def test_the_key_of_fish_is_kept_and_removed_like_any_other_and_never_comes_back(rig, caplog):
    with caplog.at_level(logging.DEBUG):
        await rig.call("services.set_key", {"service": "fish", "key": f"  {FISH_KEY}  "})
        status = await rig.call("services.status")
        assert option(status["voice"], "fish")["has_key"] is True
        assert rig.keys.get("fish") == FISH_KEY and FISH_KEY not in str(status)
        await rig.call("services.clear_key", {"service": "fish"})
    assert option((await rig.call("services.status"))["voice"], "fish")["has_key"] is False
    assert FISH_KEY not in caplog.text


async def test_the_status_of_the_voice_follows_the_settings_without_giving_the_voice_id(rig):
    rig.settings.set("tts_service", "fish")
    rig.settings.set("tts_fish_voice", FISH_VOICE)
    voice = (await rig.call("services.status"))["voice"]
    assert voice["service"] == "fish" and voice["fish_voice_set"] is True
    assert FISH_VOICE not in str(voice)


async def test_a_voice_service_that_cannot_be_read_back_reads_as_the_default(conn):
    conn.execute("INSERT INTO settings (key, value) VALUES ('tts_service', '\"edge\"')")
    assert Rig(conn).hub.tts_service() == "fish"


async def test_a_key_for_fish_with_a_space_in_it_is_refused_by_a_kind_and_never_echoed(rig):
    with pytest.raises(RpcError) as caught:
        await rig.call("services.set_key", {"service": "fish", "key": f"has a space {FISH_KEY}"})
    assert caught.value.code == INVALID_PARAMS and FISH_KEY not in caught.value.message


# --- the settings -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "good", "bad"),
    [
        ("web_search_service", ["openrouter", "brave", "tavily", "exa", "searxng"], ["google", ""]),
        ("web_read_service", ["builtin", "jina", "firecrawl", "tavily"], ["brave", "Builtin"]),
        ("pdf_service", ["builtin", "openrouter"], ["pypdf"]),
    ],
)
def test_a_service_is_one_of_its_choices(conn, key, good, bad):
    settings = SettingsStore(conn)
    for value in good:
        settings.set(key, value)
        assert settings.get(key) == value
    for value in bad:
        with pytest.raises(ValueError):
            settings.set(key, value)
    with pytest.raises(TypeError):
        settings.set(key, 1)


def test_the_address_of_searxng_is_http_or_https_or_none(conn):
    settings = SettingsStore(conn)
    settings.set("searxng_url", "  https://search.example.org/  ")
    assert settings.get("searxng_url") == "https://search.example.org/"
    settings.set("searxng_url", "")
    assert settings.get("searxng_url") == ""
    for bad in ("ftp://x.org", "javascript:alert(1)", "search.example.org", "http://", "http://a b/",
                "https://" + "a" * 400 + ".org"):  # fmt: skip
        with pytest.raises(ValueError):
            settings.set("searxng_url", bad)


def test_the_vision_model_is_a_model_id_or_none(conn):
    settings = SettingsStore(conn)
    settings.set("vision_model", " openai/gpt-6-luna ")
    assert settings.get("vision_model") == "openai/gpt-6-luna"
    settings.set("vision_model", "")
    assert settings.get("vision_model") == ""
    with pytest.raises(ValueError):
        settings.set("vision_model", "has space")


async def test_choosing_the_vision_model_takes_one_of_the_list_that_reads_images(conn):
    text_only = {"input_modalities": ["text"], "output_modalities": ["text"]}
    client = FakeModelsClient()
    client.reply = edited("anthropic/claude-haiku-5.5", architecture=text_only)
    rpc = Router()
    Brain(conn, client, MemoryKeyStore(), now_ms=Clock()).register(rpc)
    await rpc.call("models.catalog", {})  # the list is read once

    async def choose(model):
        return (await rpc.call("settings.set", {"key": "vision_model", "value": model}))["settings"]

    assert (await choose("google/gemini-3.1-flash-lite"))["vision_model"] == "google/gemini-3.1-flash-lite"
    assert (await choose(""))["vision_model"] == ""
    for model, reason in (("anthropic/claude-haiku-5.5", "no_image_input"), ("x/unknown", "OpenRouter")):
        with pytest.raises(RpcError) as caught:
            await choose(model)
        assert caught.value.code == INVALID_PARAMS and reason in caught.value.message


# --- addresses ----------------------------------------------------------------------------------


def test_the_addresses_of_a_text_without_the_punctuation_around_them():
    text = (
        "看看 https://example.com/a?b=1。還有(https://en.wikipedia.org/wiki/Foo_(bar)) 和 "
        "「http://x.org/p」, https://example.com/a?b=1 again, and ftp://no.org"
    )
    assert urls_in(text) == [
        "https://example.com/a?b=1",
        "https://en.wikipedia.org/wiki/Foo_(bar)",
        "http://x.org/p",
    ]
    assert urls_in(text, 2) == ["https://example.com/a?b=1", "https://en.wikipedia.org/wiki/Foo_(bar)"]


def test_an_address_is_seen_exactly_or_as_the_same_page():
    seen = SeenUrls(["Read https://Example.com/docs/page?x=1#top please"])
    assert seen.seen("https://Example.com/docs/page?x=1#top")
    assert seen.seen("https://example.com/docs/page")  # the same origin and path
    assert seen.seen("https://example.com:443/docs/page?other=2")
    assert not seen.seen("https://example.com/docs/other")
    assert not seen.seen("http://example.com/docs/page")  # another scheme is another origin
    assert not seen.seen("https://evil.example/docs/page")
    assert page_key("not a url") is None
