import json

import pytest

from raphael_core.brain.settings import (
    DEFAULTS,
    STT_ENGINES,
    SettingsStore,
)


def test_defaults_and_set(conn):
    s = SettingsStore(conn)
    assert s.get("monthly_limit_usd") == 10.0
    s.set("monthly_limit_usd", 20)
    assert s.get("monthly_limit_usd") == 20.0


def test_rejects_unknown_key_and_wrong_type(conn):
    s = SettingsStore(conn)
    with pytest.raises(KeyError):
        s.set("nope", 1)
    with pytest.raises(TypeError):
        s.set("history_retention_days", "thirty")


def test_the_defaults_are_the_documented_ones():
    assert DEFAULTS == {
        "owner_name": "",
        "owner_about": "",
        "owner_photo": "",
        "skin": "default",
        "monthly_limit_usd": 10.0,
        "cleanup_model": "openai/gpt-6-luna",
        "languages": ["zh-TW"],
        "history_retention_days": 30,
        "stt_engine": "cloud",
        "stt_cloud_model": "elevenlabs/scribe-v2",
        "agent_model": "openai/gpt-6-luna",
        "action_mode": "direct",
        "router_model": "",
        "memory_model": "",
        "pdf_model": "",
        "agent_fallbacks": [],
        "cleanup_fallbacks": [],
        "router_fallbacks": [],
        "vision_fallbacks": [],
        "pdf_fallbacks": [],
        "stt_fallbacks": [],
        "memory_fallbacks": [],
        "agent_city": "台北",
        "worker_cost_cap_usd": 0.2,
        "stt_local_model": "qwen3-asr",
        "model_favorites": {"cleanup": [], "answer": [], "stt": []},
        "web_search_service": "openrouter",
        "web_read_service": "builtin",
        "vision_model": "",
        "pdf_service": "builtin",
        "searxng_url": "",
        "web_search_enabled": True,
        "web_read_enabled": True,
        "vision_enabled": True,
        "pdf_enabled": True,
        "tts_enabled": False,
        "tts_service": "fish",
        "tts_fish_voice": "",
        "tts_fish_model": "s2.1-pro-free",
        "tts_language": "ja",
    }


def test_get_rejects_an_unknown_key(conn):
    with pytest.raises(KeyError):
        SettingsStore(conn).get("nope")


def test_all_is_the_defaults_with_saved_values_on_top(conn):
    s = SettingsStore(conn)
    assert s.all() == DEFAULTS
    s.set("languages", ["ja-JP"])
    assert s.all() == {**DEFAULTS, "languages": ["ja-JP"]}
    s.all()["languages"].append("changed behind its back")
    s.get("languages").append("changed behind its back")
    assert DEFAULTS["languages"] == ["zh-TW"]
    assert s.get("languages") == ["ja-JP"]


def test_all_ignores_saved_keys_that_are_not_settings(conn):
    conn.execute("INSERT INTO settings (key, value) VALUES ('retired_setting', '1')")
    assert SettingsStore(conn).all() == DEFAULTS


def test_a_whole_number_for_a_float_setting_is_kept_as_a_float(conn):
    s = SettingsStore(conn)
    s.set("monthly_limit_usd", 20)
    assert isinstance(s.get("monthly_limit_usd"), float)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("history_retention_days", 30.0),
        ("history_retention_days", True),
        ("monthly_limit_usd", True),
        ("monthly_limit_usd", "20"),
        ("agent_city", 1),
        ("cleanup_model", None),
    ],
)
def test_rejects_values_that_are_not_the_defaults_type(conn, key, value):
    s = SettingsStore(conn)
    with pytest.raises(TypeError):
        s.set(key, value)
    assert s.get(key) == DEFAULTS[key]


def test_values_are_stored_as_json_text(conn):
    s = SettingsStore(conn)
    s.set("languages", ["ja-JP"])
    s.set("history_retention_days", 7)
    s.set("monthly_limit_usd", 20)
    stored = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}
    assert stored == {
        "languages": '["ja-JP"]',
        "history_retention_days": "7",
        "monthly_limit_usd": "20.0",
    }


def test_setting_a_key_again_replaces_its_value(conn):
    s = SettingsStore(conn)
    s.set("languages", ["ja-JP"])
    s.set("languages", ["zh-HK", "en-GB"])
    assert s.get("languages") == ["zh-HK", "en-GB"]
    assert conn.execute("SELECT count(*) FROM settings").fetchone()[0] == 1


def test_non_finite_numbers_are_rejected_and_not_saved(conn):
    s = SettingsStore(conn)
    with pytest.raises(ValueError, match="not JSON compliant"):
        s.set("monthly_limit_usd", float("nan"))
    with pytest.raises(ValueError, match="not JSON compliant"):
        s.set("monthly_limit_usd", float("inf"))
    assert s.get("monthly_limit_usd") == 10.0


# --- speech-to-text ----------------------------------------------------------------------------


def test_speech_to_text_is_in_the_cloud_with_scribe_by_default(conn):
    s = SettingsStore(conn)
    assert s.get("stt_engine") == "cloud"
    assert s.get("stt_cloud_model") == "elevenlabs/scribe-v2"
    assert STT_ENGINES == ("cloud", "local")


def test_the_engine_can_be_local_or_cloud_and_nothing_else(conn):
    s = SettingsStore(conn)
    s.set("stt_engine", "local")
    assert s.get("stt_engine") == "local"
    s.set("stt_engine", "cloud")
    with pytest.raises(ValueError, match="stt_engine"):
        s.set("stt_engine", "gpu")
    with pytest.raises(TypeError):
        s.set("stt_engine", 1)
    assert s.get("stt_engine") == "cloud"


@pytest.mark.parametrize(
    "model",
    [
        "elevenlabs/scribe-v2",
        "openai/gpt-transcribe",
        "qwen/qwen3-asr-1.7b",
        "deepgram/nova-3",
        "some-new-maker/a-model-of-next-year",
    ],
)
def test_any_cloud_model_id_can_be_saved(conn, model):
    # The store keeps no list of models: whatever OpenRouter lists can be chosen.
    s = SettingsStore(conn)
    s.set("stt_cloud_model", model)
    assert s.get("stt_cloud_model") == model


def test_the_store_takes_any_cloud_model_id_and_leaves_the_list_to_the_brain(conn):
    # Which models OpenRouter lists for speech-to-text is the brain's to check (brain/catalog.py):
    # the store only keeps an id, without the spaces around it.
    s = SettingsStore(conn)
    s.set("stt_cloud_model", " deepgram/nova-3 ")
    assert s.get("stt_cloud_model") == "deepgram/nova-3"
    for model in ("", "   ", "openai/gpt transcribe", "m" * 101):
        with pytest.raises(ValueError, match="stt_cloud_model"):
            s.set("stt_cloud_model", model)
    assert s.get("stt_cloud_model") == "deepgram/nova-3"


# --- languages ---------------------------------------------------------------------------------


def test_languages_are_taiwan_chinese_by_default(conn):
    s = SettingsStore(conn)
    assert s.get("languages") == ["zh-TW"]


def test_a_valid_selection_of_languages_is_saved_in_order(conn):
    s = SettingsStore(conn)
    s.set("languages", ["en-GB", "zh-CN", "ja-JP"])
    assert s.get("languages") == ["en-GB", "zh-CN", "ja-JP"]
    assert s.all()["languages"] == ["en-GB", "zh-CN", "ja-JP"]


@pytest.mark.parametrize(
    "value",
    [[], ["auto"], ["zh-TW", "zh-HK"], ["en-US", "en-AU"], ["es-ES", "es-MX"], ["xx-XX"],
     ["zh-TW", "en-US", "ja-JP", "ko-KR", "fr-FR", "de-DE"]],
)  # fmt: skip
def test_an_invalid_selection_of_languages_is_refused_and_not_saved(conn, value):
    s = SettingsStore(conn)
    with pytest.raises(ValueError, match="'languages'"):
        s.set("languages", value)
    assert s.get("languages") == ["zh-TW"]


@pytest.mark.parametrize("value", ["zh-TW", None, [1], {"zh-TW": 1}])
def test_languages_that_are_not_a_list_of_strings_are_a_type_error(conn, value):
    with pytest.raises(TypeError):
        SettingsStore(conn).set("languages", value)


def test_the_single_language_of_before_the_first_release_is_no_longer_a_setting(conn):
    # A database from a build older than the first release may hold it: it is neither read nor
    # offered, and it cannot be set again.
    conn.execute("INSERT INTO settings (key, value) VALUES ('language', '\"ja\"')")
    s = SettingsStore(conn)
    assert s.get("languages") == ["zh-TW"] and "language" not in s.all()
    with pytest.raises(KeyError):
        s.get("language")
    with pytest.raises(KeyError):
        s.set("language", "ja")


def test_a_saved_selection_that_is_no_longer_valid_reads_as_the_default(conn):
    conn.execute("INSERT INTO settings (key, value) VALUES ('languages', '[\"xx-XX\"]')")
    assert SettingsStore(conn).get("languages") == ["zh-TW"]



def test_learning_corrections_is_no_longer_a_setting_and_a_saved_off_is_ignored(conn):
    # Learning is always on. A database from a version with the switch may hold it off: it is
    # neither read nor offered, and it cannot be set again.
    conn.execute("INSERT INTO settings (key, value) VALUES ('learn_corrections', 'false')")
    s = SettingsStore(conn)
    assert "learn_corrections" not in s.all()
    with pytest.raises(KeyError):
        s.get("learn_corrections")
    with pytest.raises(KeyError):
        s.set("learn_corrections", True)


# --- the voice assistant -----------------------------------------------------------------------


def test_the_assistant_answers_with_luna_in_taipei_by_default(conn):
    s = SettingsStore(conn)
    assert s.get("agent_model") == "openai/gpt-6-luna"
    assert s.get("agent_city") == "台北"


def test_the_router_is_no_longer_a_setting_and_a_saved_choice_is_ignored(conn):
    # Jev always routes. A database from a version with the choice may still hold one: it is
    # neither read nor offered, and it cannot be set again.
    conn.execute("INSERT INTO settings (key, value) VALUES ('agent_router', '\"luna\"')")
    s = SettingsStore(conn)
    assert "agent_router" not in s.all()
    assert s.all() == DEFAULTS
    with pytest.raises(KeyError):
        s.get("agent_router")
    with pytest.raises(KeyError):
        s.set("agent_router", "jev")


def test_the_answer_model_and_the_city_are_saved_without_the_spaces_around_them(conn):
    s = SettingsStore(conn)
    s.set("agent_model", " anthropic/claude-haiku-5.5 ")
    s.set("agent_city", "  New York ")
    assert s.get("agent_model") == "anthropic/claude-haiku-5.5"
    assert s.get("agent_city") == "New York"
    stored = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}
    assert stored == {"agent_model": '"anthropic/claude-haiku-5.5"', "agent_city": '"New York"'}


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("agent_model", ""),
        ("agent_model", "   "),
        ("agent_model", "openai/gpt 6"),  # a model id has no spaces
        ("agent_model", "m" * 101),
        ("cleanup_model", " "),
        ("cleanup_model", "openai/gpt 6"),
        ("cleanup_model", "m" * 101),
        ("agent_city", ""),
        ("agent_city", " \t\n"),
        ("agent_city", "c" * 101),
    ],
)
def test_a_blank_or_absurd_model_or_city_is_refused_and_not_saved(conn, key, value):
    s = SettingsStore(conn)
    with pytest.raises(ValueError, match=key):
        s.set(key, value)
    assert s.get(key) == DEFAULTS[key]


@pytest.mark.parametrize("key", ["agent_model", "cleanup_model", "stt_cloud_model", "agent_city"])
def test_a_model_or_city_that_is_not_text_is_a_type_error(conn, key):
    with pytest.raises(TypeError):
        SettingsStore(conn).set(key, None)


# --- favourite models ---------------------------------------------------------------------------

_NO_FAVOURITES = {"cleanup": [], "answer": [], "stt": []}


def test_nobody_has_favourites_at_first(conn):
    s = SettingsStore(conn)
    assert s.get("model_favorites") == _NO_FAVOURITES
    assert s.all()["model_favorites"] == _NO_FAVOURITES


def test_favourites_are_kept_per_role_in_order(conn):
    s = SettingsStore(conn)
    s.set("model_favorites", {"cleanup": ["a/b", "c/d"], "answer": ["e/f"]})
    assert s.get("model_favorites") == {"cleanup": ["a/b", "c/d"], "answer": ["e/f"], "stt": []}


def test_favourites_are_saved_across_stores(conn):
    SettingsStore(conn).set("model_favorites", {"stt": ["elevenlabs/scribe-v2"]})
    assert SettingsStore(conn).get("model_favorites")["stt"] == ["elevenlabs/scribe-v2"]


def test_a_favourite_listed_twice_is_kept_once(conn):
    s = SettingsStore(conn)
    s.set("model_favorites", {"cleanup": ["a/b", "c/d", "a/b"]})
    assert s.get("model_favorites")["cleanup"] == ["a/b", "c/d"]


def test_changing_what_get_returns_does_not_change_the_default(conn):
    s = SettingsStore(conn)
    s.get("model_favorites")["cleanup"].append("x/y")
    s.all()["model_favorites"]["stt"].append("x/y")
    assert SettingsStore(conn).get("model_favorites") == _NO_FAVOURITES


@pytest.mark.parametrize(
    "value",
    [
        {"voice": ["a/b"]},  # not a role
        {"cleanup": [f"m/{i}" for i in range(51)]},  # too many
        {"cleanup": ["a b"]},  # a model id has no spaces
        {"cleanup": [""]},
        {"cleanup": ["x" * 101]},
    ],
)
def test_favourites_that_are_not_valid_are_refused_and_not_saved(conn, value):
    s = SettingsStore(conn)
    with pytest.raises(ValueError):
        s.set("model_favorites", value)
    assert s.get("model_favorites") == _NO_FAVOURITES


@pytest.mark.parametrize(
    "value", ["a/b", ["a/b"], {"cleanup": "a/b"}, {"cleanup": [1]}, {"cleanup": None}, None]
)
def test_favourites_of_the_wrong_shape_are_a_type_error(conn, value):
    with pytest.raises(TypeError):
        SettingsStore(conn).set("model_favorites", value)


def test_exactly_fifty_favourites_are_fine(conn):
    s = SettingsStore(conn)
    s.set("model_favorites", {"answer": [f"m/{i}" for i in range(50)]})
    assert len(s.get("model_favorites")["answer"]) == 50


def test_a_saved_value_that_is_damaged_reads_as_the_default(conn):
    conn.execute("INSERT INTO settings (key, value) VALUES ('model_favorites', '[1, 2]')")
    conn.commit()
    s = SettingsStore(conn)
    assert s.get("model_favorites") == _NO_FAVOURITES
    assert s.all()["model_favorites"] == _NO_FAVOURITES


# --- the local speech model --------------------------------------------------------------------


def test_the_local_engine_is_qwen3_asr_until_another_is_chosen(conn):
    s = SettingsStore(conn)
    assert s.get("stt_local_model") == "qwen3-asr"
    assert s.all()["stt_local_model"] == "qwen3-asr"


def test_every_model_of_the_catalogue_can_be_the_local_engine(conn):
    from raphael_core.device.models import LOCAL_STT_MODELS

    s = SettingsStore(conn)
    for entry in LOCAL_STT_MODELS:
        s.set("stt_local_model", entry.name)
        assert s.get("stt_local_model") == entry.name


@pytest.mark.parametrize("value", ["silero-vad", "whisper-large", "", "Qwen3-ASR"])
def test_a_local_engine_that_is_not_in_the_catalogue_is_refused(conn, value):
    s = SettingsStore(conn)
    with pytest.raises(ValueError, match="stt_local_model"):
        s.set("stt_local_model", value)
    assert s.get("stt_local_model") == "qwen3-asr"


def test_the_local_engine_must_be_text(conn):
    with pytest.raises(TypeError):
        SettingsStore(conn).set("stt_local_model", 3)


def test_there_can_be_no_local_model_while_the_cloud_is_the_engine(conn):
    s = SettingsStore(conn)
    s.set("stt_local_model", None)
    assert s.get("stt_local_model") is None
    assert s.all()["stt_local_model"] is None
    s.set("stt_local_model", "sensevoice")
    assert s.get("stt_local_model") == "sensevoice"


def test_the_local_engine_needs_a_local_model(conn):
    s = SettingsStore(conn)
    s.set("stt_local_model", None)
    with pytest.raises(ValueError, match="stt_local_model"):
        s.set("stt_engine", "local")
    assert s.get("stt_engine") == "cloud"
    s.set("stt_local_model", "sensevoice")
    s.set("stt_engine", "local")
    with pytest.raises(ValueError, match="stt_local_model"):
        s.set("stt_local_model", None)
    assert s.get("stt_local_model") == "sensevoice"


def test_raphaels_voice_provider_is_fish_and_no_voice_is_not_a_provider(conn):
    settings = SettingsStore(conn)
    assert settings.get("tts_service") == "fish" and settings.get("tts_enabled") is False
    settings.set("tts_service", "fish")
    assert settings.get("tts_service") == "fish"
    for bad in ("elevenlabs", "Fish", "", "none"):
        with pytest.raises(ValueError):
            settings.set("tts_service", bad)
    with pytest.raises(TypeError):
        settings.set("tts_service", 1)


def test_the_voice_speaks_japanese_and_no_other_language_yet(conn):
    settings = SettingsStore(conn)
    assert settings.get("tts_language") == "ja"
    settings.set("tts_language", "ja")
    for bad in ("zh-TW", "en", ""):
        with pytest.raises(ValueError):
            settings.set("tts_language", bad)


ENABLED = ("web_search_enabled", "web_read_enabled", "vision_enabled", "pdf_enabled", "tts_enabled")


def test_the_switches_are_on_or_off_and_nothing_else(conn):
    settings = SettingsStore(conn)
    assert [settings.get(key) for key in ENABLED] == [True, True, True, True, False]
    for key in ENABLED:
        settings.set(key, not DEFAULTS[key])
        assert settings.get(key) is (not DEFAULTS[key])
        for bad in (1, 0, "true", None):
            with pytest.raises(TypeError):
                settings.set(key, bad)


def _save_raw(conn, key, value):
    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)", (key, json.dumps(value)))


def test_a_saved_none_voice_reads_as_the_default_provider_with_the_voice_off(conn):
    _save_raw(conn, "tts_service", "none")
    settings = SettingsStore(conn)
    assert settings.get("tts_service") == "fish" and settings.get("tts_enabled") is False
    everything = settings.all()
    assert everything["tts_service"] == "fish" and everything["tts_enabled"] is False


def test_a_saved_fish_voice_with_an_id_reads_as_on_and_without_one_as_off(conn):
    _save_raw(conn, "tts_service", "fish")
    settings = SettingsStore(conn)
    assert settings.get("tts_enabled") is False  # fish, but no voice id: it could not speak
    settings.set("tts_fish_voice", "0123456789abcdef0123456789abcdef")
    assert settings.get("tts_enabled") is True and settings.all()["tts_enabled"] is True


def test_a_saved_switch_wins_over_what_the_older_voice_setting_says(conn):
    _save_raw(conn, "tts_service", "fish")
    _save_raw(conn, "tts_fish_voice", "0123456789abcdef0123456789abcdef")
    settings = SettingsStore(conn)
    assert settings.get("tts_enabled") is True
    settings.set("tts_enabled", False)
    assert settings.get("tts_enabled") is False and settings.all()["tts_enabled"] is False


def test_the_fish_voice_is_32_hexadecimal_characters_or_none_and_is_not_in_the_defaults(conn):
    settings = SettingsStore(conn)
    assert DEFAULTS["tts_fish_voice"] == "" and settings.get("tts_fish_voice") == ""
    settings.set("tts_fish_voice", "  0123456789ABCDEF0123456789abcdef  ")
    assert settings.get("tts_fish_voice") == "0123456789abcdef0123456789abcdef"
    settings.set("tts_fish_voice", "")
    assert settings.get("tts_fish_voice") == ""
    for bad in ("0123", "g" * 32, "0" * 33, "0123456789abcdef 123456789abcdef"):
        with pytest.raises(ValueError):
            settings.set("tts_fish_voice", bad)
    with pytest.raises(TypeError):
        settings.set("tts_fish_voice", 5)


def test_the_fish_model_is_a_short_token(conn):
    settings = SettingsStore(conn)
    settings.set("tts_fish_model", " s2.1-pro ")
    assert settings.get("tts_fish_model") == "s2.1-pro"
    for bad in ("", "  ", "has space", "s2/pro", "x" * 41, "-s1", "好"):
        with pytest.raises(ValueError):
            settings.set("tts_fish_model", bad)
