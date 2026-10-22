import pytest

from raphael_core.brain.settings import (
    DEFAULTS,
    STT_CLOUD_MODELS,
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
        "monthly_limit_usd": 10.0,
        "cleanup_model": "openai/gpt-6-luna",
        "languages": ["zh-TW"],
        "language": "zh",
        "history_retention_days": 30,
        "stt_engine": "cloud",
        "stt_cloud_model": "elevenlabs/scribe-v2",
        "agent_model": "openai/gpt-6-luna",
        "agent_city": "台北",
        "stt_local_model": "qwen3-asr",
        "model_favorites": {"cleanup": [], "answer": [], "stt": []},
    }


def test_get_rejects_an_unknown_key(conn):
    with pytest.raises(KeyError):
        SettingsStore(conn).get("nope")


def test_all_is_the_defaults_with_saved_values_on_top(conn):
    s = SettingsStore(conn)
    assert s.all() == DEFAULTS
    s.set("language", "ja")
    assert s.all() == {**DEFAULTS, "languages": ["ja-JP"], "language": "ja"}
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
        ("language", 1),
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
    s.set("language", "ja")
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
    assert STT_CLOUD_MODELS[0] == "elevenlabs/scribe-v2"


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


@pytest.mark.parametrize("model", STT_CLOUD_MODELS)
def test_every_listed_cloud_model_can_be_chosen(conn, model):
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
    assert s.get("language") == "zh"


def test_a_valid_selection_of_languages_is_saved_in_order(conn):
    s = SettingsStore(conn)
    s.set("languages", ["en-GB", "zh-CN", "ja-JP"])
    assert s.get("languages") == ["en-GB", "zh-CN", "ja-JP"]
    assert s.all()["languages"] == ["en-GB", "zh-CN", "ja-JP"]
    assert s.get("language") == "auto"


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


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("auto", ["zh-TW", "en-US", "ja-JP"]),
        ("zh", ["zh-TW"]),
        ("ja", ["ja-JP"]),
        ("en", ["en-US"]),
    ],
)
def test_a_saved_old_language_reads_as_languages(conn, old, new):
    conn.execute("INSERT INTO settings (key, value) VALUES ('language', ?)", (f'"{old}"',))
    s = SettingsStore(conn)
    assert s.get("languages") == new
    assert s.all()["languages"] == new
    assert s.get("language") == ("auto" if old == "auto" else old)


def test_a_saved_selection_wins_over_the_old_language(conn):
    conn.execute("INSERT INTO settings (key, value) VALUES ('language', '\"ja\"')")
    s = SettingsStore(conn)
    s.set("languages", ["zh-HK"])
    assert s.get("languages") == ["zh-HK"]
    assert s.get("language") == "zh"


def test_a_saved_selection_that_is_no_longer_valid_reads_as_the_default(conn):
    conn.execute("INSERT INTO settings (key, value) VALUES ('languages', '[\"xx-XX\"]')")
    assert SettingsStore(conn).get("languages") == ["zh-TW"]


def test_setting_the_old_language_sets_languages(conn):
    s = SettingsStore(conn)
    s.set("language", "auto")
    assert s.get("languages") == ["zh-TW", "en-US", "ja-JP"]
    with pytest.raises(ValueError, match="'language'"):
        s.set("language", "de")
    assert s.get("languages") == ["zh-TW", "en-US", "ja-JP"]

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
