"""The release packaging scripts in core/packaging, without PyInstaller or a frozen build."""

import importlib.util
import re
from pathlib import Path

import pytest

PACKAGING = Path(__file__).resolve().parents[1] / "packaging"


def load_script(name: str):
    """Import core/packaging/<name>.py under a module name of its own."""
    spec = importlib.util.spec_from_file_location(f"raphael_packaging_{name}", PACKAGING / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def notices():
    return load_script("notices")


@pytest.fixture(scope="module")
def manifest(notices):
    return notices.load_source_manifest()


def test_every_pinned_license_text_is_listed_and_every_listed_text_matches_its_checksum(notices, manifest):
    listed = {item["file"] for item in manifest["licenses"]}
    assert listed == {path.name for path in notices.LICENSES.glob("*.txt")}
    for item in manifest["licenses"]:
        assert item["url"].startswith("https://"), item["file"]
        assert not re.search(r"/(?:main|master|HEAD)/", item["url"]), f"{item['file']} is not pinned to a version"
        assert re.fullmatch(r"[0-9a-f]{64}", item["sha256"]), item["file"]


def test_a_changed_license_text_is_refused(notices, manifest, monkeypatch, tmp_path):
    for item in manifest["licenses"]:
        (tmp_path / item["file"]).write_bytes((notices.LICENSES / item["file"]).read_bytes())
    (tmp_path / "sources.json").write_bytes((notices.LICENSES / "sources.json").read_bytes())
    monkeypatch.setattr(notices, "LICENSES", tmp_path)
    assert notices.load_source_manifest()["licenses"]

    (tmp_path / "libffi-3.4.2-LICENSE.txt").write_text("changed", encoding="utf-8")

    with pytest.raises(RuntimeError, match="libffi-3.4.2-LICENSE.txt"):
        notices.load_source_manifest()


def test_the_shipped_binary_components_have_notices(manifest):
    components = {item["component"]: item for item in manifest["licenses"] if "component" in item}
    assert {"OpenSSL 3.5.8", "libffi 3.4.2", "Expat 2.8.3", "libmpdec 2.5.1", "marisa-trie 0.3.1"} <= set(components)
    # The binaries that carry them, so a reader can tell what each text is for.
    assert "libcrypto-3-x64.dll" in components["OpenSSL 3.5.8"]["shipped_in"]
    assert "libffi-8.dll" in components["libffi 3.4.2"]["shipped_in"]
    assert "pyexpat.pyd" in components["Expat 2.8.3"]["shipped_in"]
    assert "_decimal.pyd" in components["libmpdec 2.5.1"]["shipped_in"]
    assert components["marisa-trie 0.3.1"]["license"] == "BSD-2-Clause OR LGPL-2.1-or-later"


def test_the_pins_cover_every_version_the_texts_and_source_links_depend_on(notices, manifest):
    assert set(manifest["pinned_versions"]) == set(notices.installed_versions())
    for item in manifest["licenses"]:
        if "component" in item:
            version = item["component"].rsplit(" ", 1)[1]
            assert version in item["file"], f"{item['file']} is not named after {item['component']}"


def test_a_build_with_other_versions_than_the_pins_is_refused(notices, manifest):
    pinned = manifest["pinned_versions"]
    notices.check_pinned_versions(pinned, dict(pinned))

    with pytest.raises(RuntimeError, match="openssl: pinned 3.5.8, installed 3.0.19; soxr: pinned 1.1.0, installed None"):
        notices.check_pinned_versions(pinned, {**pinned, "openssl": "3.0.19", "soxr": None})


def test_the_notice_text_says_where_the_lgpl_source_is(notices, manifest):
    text = "\n".join(notices.component_sections(manifest))

    assert "## Source code of LGPL components" in text
    for fragment in (
        "libsoxr 0.1.3-14-ga66f3ee (LGPL-2.1-or-later)",
        "https://github.com/dofuuz/soxr/tree/a66f3eeeeb62a32403ff143b756eed92b1ec6b62",
        "soxr-1.1.0.tar.gz",
        "marisa-trie 0.3.1",
        "https://github.com/s-yata/marisa-trie/tree/3e87d53b78e15f2f43783d5e376561a8c9722051",
        "opencc-1.4.2.tar.gz",
    ):
        assert fragment in text
    for component in manifest["licenses"]:
        if "component" in component:
            assert f"upstream/{component['file']}" in text
    for item in manifest["source_code"]:
        for source in item["sources"]:
            assert source["url"].startswith("https://")
            assert re.fullmatch(r"[0-9a-f]{64}", source.get("sha256", "0" * 64))
