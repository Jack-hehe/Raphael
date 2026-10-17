import pytest

from raphael_core.brain.settings import DEFAULTS, SettingsStore


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
        "cleanup_model": "google/gemini-3.1-flash-lite",
        "language": "auto",
        "history_retention_days": 30,
    }


def test_get_rejects_an_unknown_key(conn):
    with pytest.raises(KeyError):
        SettingsStore(conn).get("nope")


def test_all_is_the_defaults_with_saved_values_on_top(conn):
    s = SettingsStore(conn)
    assert s.all() == DEFAULTS
    s.set("language", "ja")
    assert s.all() == {**DEFAULTS, "language": "ja"}
    s.all()["language"] = "changed behind its back"
    assert DEFAULTS["language"] == "auto"


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
        "language": '"ja"',
        "history_retention_days": "7",
        "monthly_limit_usd": "20.0",
    }


def test_setting_a_key_again_replaces_its_value(conn):
    s = SettingsStore(conn)
    s.set("language", "ja")
    s.set("language", "zh")
    assert s.get("language") == "zh"
    assert conn.execute("SELECT count(*) FROM settings").fetchone()[0] == 1


def test_non_finite_numbers_are_rejected_and_not_saved(conn):
    s = SettingsStore(conn)
    with pytest.raises(ValueError, match="not JSON compliant"):
        s.set("monthly_limit_usd", float("nan"))
    with pytest.raises(ValueError, match="not JSON compliant"):
        s.set("monthly_limit_usd", float("inf"))
    assert s.get("monthly_limit_usd") == 10.0
