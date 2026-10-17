import types

import pytest

from raphael_core import paths


@pytest.fixture(autouse=True)
def fake_home(tmp_path, monkeypatch):
    """Point every location paths could fall back to at tmp_path, never at the real profile."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.delenv("RAPHAEL_DATA_DIR", raising=False)
    monkeypatch.setenv("APPDATA", str(home / "AppData" / "Roaming"))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    return home


def on_platform(monkeypatch, name):
    monkeypatch.setattr(paths, "sys", types.SimpleNamespace(platform=name))


def test_the_override_is_used_and_created_on_first_use(tmp_path, monkeypatch):
    target = tmp_path / "somewhere" / "raphael"
    monkeypatch.setenv("RAPHAEL_DATA_DIR", str(target))
    assert paths.data_dir() == target
    assert target.is_dir()
    assert paths.data_dir() == target  # an existing directory is fine


def test_models_and_the_database_live_in_the_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("RAPHAEL_DATA_DIR", str(tmp_path / "data"))
    assert paths.models_dir() == tmp_path / "data" / "models"
    assert paths.db_path() == tmp_path / "data" / "raphael.db"


def test_windows_default_is_raphael_under_appdata(fake_home, monkeypatch):
    on_platform(monkeypatch, "win32")
    expected = fake_home / "AppData" / "Roaming" / "Raphael"
    assert paths.data_dir() == expected
    assert expected.is_dir()


def test_macos_default_is_raphael_under_application_support(fake_home, monkeypatch):
    on_platform(monkeypatch, "darwin")
    expected = fake_home / "Library" / "Application Support" / "Raphael"
    assert paths.data_dir() == expected
    assert expected.is_dir()


def test_an_empty_override_counts_as_unset(fake_home, monkeypatch):
    on_platform(monkeypatch, "win32")
    monkeypatch.setenv("RAPHAEL_DATA_DIR", "")
    assert paths.data_dir() == fake_home / "AppData" / "Roaming" / "Raphael"


def test_other_platforms_have_no_default(monkeypatch):
    on_platform(monkeypatch, "linux")
    with pytest.raises(RuntimeError, match="RAPHAEL_DATA_DIR"):
        paths.data_dir()
