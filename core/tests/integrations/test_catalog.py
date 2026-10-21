"""Tests for the store's catalog: every entry is complete and nothing in it is a trap."""

import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from raphael_core.integrations import catalog
from raphael_core.integrations.catalog import CATEGORIES, ICONS, LANGUAGES, POPULAR, SERVICES, find, view


def test_the_ids_are_unique_and_plain():
    ids = [service.id for service in SERVICES]
    assert len(ids) == len(set(ids))
    assert all(re.fullmatch(r"[a-z][a-z0-9-]{1,30}", ident) for ident in ids)


def test_the_store_has_the_services_that_log_in_with_nothing_registered():
    ids = {service.id for service in SERVICES}
    assert {"notion", "linear", "atlassian", "cloudflare", "todoist", "sentry"} <= ids
    # They need an app registered first (or an invitation), so they are not one click.
    assert not ids & {"github", "google", "slack", "figma"}


@pytest.mark.parametrize("service", SERVICES, ids=lambda s: s.id)
def test_an_entry_is_complete(service):
    parts = urlsplit(service.url)
    assert parts.scheme == "https" and parts.hostname and not parts.query and not parts.fragment
    assert service.category in CATEGORIES
    assert service.icon in ICONS  # every entry shows its brand's own mark
    assert service.auth in ("oauth", "none")
    assert service.name and service.publisher
    for words in (service.summary, service.about, service.examples):
        assert set(words) == set(LANGUAGES)
    for lang in LANGUAGES:
        assert service.summary[lang].strip() and service.about[lang].strip()
        assert 2 <= len(service.examples[lang]) <= 3
        assert all(example.strip() for example in service.examples[lang])
        assert len(service.summary[lang]) <= 60


def test_an_entry_in_japanese_or_english_has_no_chinese_and_the_other_way_round():
    han = re.compile(r"[一-鿿]")
    kana = re.compile(r"[぀-ヿ]")
    for service in SERVICES:
        assert not han.search(service.summary["en"]) and not kana.search(service.summary["en"])
        assert kana.search(service.summary["ja"])
        assert not kana.search(service.summary["zh-TW"])


def test_the_popular_ones_are_in_the_catalog():
    assert all(find(ident) is not None for ident in POPULAR)


def test_find_and_the_view():
    notion = find("notion")
    assert notion is not None and find("nope") is None
    shown = view(notion)
    assert shown["id"] == "notion" and shown["popular"] is True
    assert set(shown["examples"]) == set(LANGUAGES)
    assert view(find("sentry"))["popular"] is False  # type: ignore[arg-type]


def test_the_public_servers_need_no_login_and_the_others_do():
    public = {s.id for s in SERVICES if s.auth == "none"}
    assert {"context7", "deepwiki"} <= public
    assert find("notion").auth == "oauth"  # type: ignore[union-attr]


def test_the_module_holds_no_secrets_or_client_ids():
    text = Path(catalog.__file__).read_text(encoding="utf-8").lower()
    assert "client_id" not in text and "client_secret" not in text


def test_every_mark_the_catalog_names_exists_in_the_app():
    """ICONS here and the marks of the window are one list: a drawn path of `service-icons.ts` or a logo
    file of `service-logos/`."""
    app = Path(__file__).resolve().parents[3] / "app" / "src" / "main"
    drawn = set(re.findall(r"^  (\w+): \{ title:", (app / "service-icons.ts").read_text(encoding="utf-8"), re.M))
    files = {re.sub(r"\.dark$", "", f.stem) for f in (app / "service-logos").iterdir() if f.suffix in (".svg", ".png")}
    assert set(ICONS) <= drawn | files
    assert {s.icon for s in SERVICES} <= drawn | files
