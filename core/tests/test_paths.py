import types

import pytest

from raphael_core import paths


@pytest.fixture(autouse=True)
def fake_home(tmp_path, monkeypatch):
    """Point every location paths could fall back to at tmp_path, never at the real profile."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.delenv("RAPHAEL_DATA_DIR", raising=False)
    monkeypatch.delenv("RAPHAEL_MODELS_DIR", raising=False)
    monkeypatch.setenv("APPDATA", str(home / "AppData" / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(home / "AppData" / "Local"))
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


def test_models_dir_can_name_another_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("RAPHAEL_DATA_DIR", str(tmp_path / "data"))
    other = tmp_path / "elsewhere"
    assert paths.models_dir(other) == other / "models"
    assert not other.exists()  # only a path is made, nothing on disk


def test_the_database_path_can_name_another_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("RAPHAEL_DATA_DIR", str(tmp_path / "data"))
    assert paths.db_path(tmp_path / "other") == tmp_path / "other" / "raphael.db"
    assert not (tmp_path / "other").exists()


def test_the_models_can_live_outside_the_data_dir(tmp_path, monkeypatch):
    # The end-to-end test runs the core on an empty data dir but with the models that are already
    # downloaded, which are in the real one.
    monkeypatch.setenv("RAPHAEL_DATA_DIR", str(tmp_path / "data"))
    shared = tmp_path / "shared-models"
    monkeypatch.setenv("RAPHAEL_MODELS_DIR", str(shared))
    assert paths.models_dir() == shared
    assert paths.models_dir(tmp_path / "other") == shared  # the override wins over any base
    assert paths.db_path() == tmp_path / "data" / "raphael.db"  # the database stays put


def test_an_empty_models_override_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("RAPHAEL_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("RAPHAEL_MODELS_DIR", "")
    assert paths.models_dir() == tmp_path / "data" / "models"


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


def test_windows_models_default_to_the_machine_local_folder(fake_home, monkeypatch):
    on_platform(monkeypatch, "win32")
    assert paths.local_dir() == fake_home / "AppData" / "Local" / "Raphael"
    assert paths.models_dir() == fake_home / "AppData" / "Local" / "Raphael" / "models"
    assert not (fake_home / "AppData").exists()  # only a path is made, nothing on disk


def test_the_database_stays_in_the_roaming_data_dir_on_windows(fake_home, monkeypatch):
    on_platform(monkeypatch, "win32")
    assert paths.db_path() == fake_home / "AppData" / "Roaming" / "Raphael" / "raphael.db"


def test_macos_models_default_to_the_caches_folder(fake_home, monkeypatch):
    on_platform(monkeypatch, "darwin")
    assert paths.models_dir() == fake_home / "Library" / "Caches" / "Raphael" / "models"


def test_an_explicit_base_still_holds_the_models_beside_it(fake_home, monkeypatch, tmp_path):
    on_platform(monkeypatch, "win32")
    assert paths.models_dir(tmp_path / "base") == tmp_path / "base" / "models"


def test_the_models_override_wins_over_the_local_default(fake_home, monkeypatch, tmp_path):
    on_platform(monkeypatch, "win32")
    monkeypatch.setenv("RAPHAEL_MODELS_DIR", str(tmp_path / "m"))
    assert paths.models_dir() == tmp_path / "m"


def test_the_data_dir_override_takes_the_models_with_it(fake_home, monkeypatch, tmp_path):
    on_platform(monkeypatch, "win32")
    monkeypatch.setenv("RAPHAEL_DATA_DIR", str(tmp_path / "d"))
    assert paths.models_dir() == tmp_path / "d" / "models"
    assert not (fake_home / "AppData").exists()  # the real local folder is never named
