import pytest

from raphael_core.brain.settings import DEFAULTS, STT_CLOUD_MODELS, STT_ENGINES, SettingsStore


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
        "learn_corrections": True,
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


def test_a_cloud_model_that_is_not_listed_is_refused(conn):
    s = SettingsStore(conn)
    for model in ("openai/gpt-6-luna", "", "openai/gpt-transcribe "):
        with pytest.raises(ValueError, match="stt_cloud_model"):
            s.set("stt_cloud_model", model)
    assert s.get("stt_cloud_model") == "elevenlabs/scribe-v2"


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

def test_learning_corrections_is_on_by_default_and_takes_a_bool(conn):
    s = SettingsStore(conn)
    assert s.get("learn_corrections") is True
    s.set("learn_corrections", False)
    assert s.get("learn_corrections") is False
    with pytest.raises(TypeError):
        s.set("learn_corrections", 0)
