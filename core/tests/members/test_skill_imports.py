"""Skill sources and downloads are constrained to bounded Markdown data."""

import httpx
import pytest

from raphael_core.members.capabilities import MAX_SKILL_BYTES, download_skill, skill_urls
from raphael_core.members.store import MemberError


@pytest.mark.parametrize("source", ["https://localhost/SKILL.md", "http://github.com/a/b/blob/main/SKILL.md",
                                    "https://github.com/a/b/blob/main/../SKILL.md", "a/../b",
                                    "https://github.com/a/b/blob/main/install.py",
                                    "https://raw.githubusercontent.com/a/b/main/SKILL.md?x=1"])
def test_untrusted_sources_are_rejected_before_fetching(source):
    with pytest.raises(MemberError):
        skill_urls(source)


@pytest.mark.parametrize("source,expected", [
    ("owner/repo", "https://raw.githubusercontent.com/owner/repo/main/SKILL.md"),
    ("https://github.com/owner/repo/blob/main/skills/write/SKILL.md", "https://raw.githubusercontent.com/owner/repo/main/skills/write/SKILL.md"),
    ("https://skills.sh/owner/repo/write", "https://raw.githubusercontent.com/owner/repo/main/skills/write/SKILL.md"),
])
def test_sources_resolve_only_to_github_markdown(source, expected):
    assert skill_urls(source)[1][0] == expected


async def test_imported_document_is_private_toggleable_and_deleted(rig, monkeypatch):
    one = (await rig.call("members.create", {"name": "Ada"}))["member"]["id"]
    other = (await rig.call("members.create", {"name": "Other"}))["member"]["id"]
    text = "---\nname: writing\ndescription: Clear writing\n---\nFull body"

    async def download(source):
        return [(source, text)]

    monkeypatch.setattr("raphael_core.members.service.download_skills", download)
    skill = (await rig.call("members.skills.import", {"member_id": one, "source": "owner/repo"}))["skill"]
    assert skill["name"] == "writing" and skill["enabled"]
    assert (await rig.call("members.skills.list", {"member_id": other}))["skills"] == []
    assert rig.service.skill_read(one, "writing") == text
    assert rig.service.skill_read(other, "writing") is None
    path = rig.service.capabilities._path(one, skill["id"])
    assert path.read_text(encoding="utf-8") == text
    await rig.call("members.skills.update", {"member_id": one, "id": skill["id"], "enabled": False})
    assert rig.service.skill_read(one, "writing") is None
    await rig.call("members.skills.delete", {"member_id": one, "id": skill["id"]})
    assert not path.exists()


@pytest.mark.parametrize("response", ["oversize", "redirect", "binary"])
async def test_downloader_refuses_oversize_redirects_and_binary_data(monkeypatch, response):
    real_client = httpx.AsyncClient

    def handle(request):
        assert request.url.host == "raw.githubusercontent.com" and request.url.path.endswith("SKILL.md")
        if response == "redirect":
            return httpx.Response(302, headers={"Location": "https://localhost/private"})
        return httpx.Response(200, content=b"x" * (MAX_SKILL_BYTES + 1) if response == "oversize" else b"\x00binary")

    monkeypatch.setattr("raphael_core.members.capabilities.httpx.AsyncClient",
                        lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(handle)))
    with pytest.raises(MemberError):
        await download_skill("owner/repo")


async def test_repository_collection_fetches_only_markdown_and_skips_symlinks(monkeypatch):
    from raphael_core.members.capabilities import download_skills

    real_client = httpx.AsyncClient
    requested = []

    def handle(request):
        requested.append(str(request.url))
        path = request.url.path
        if path.endswith("/main/SKILL.md") or path.endswith("/master/SKILL.md"):
            return httpx.Response(404)
        if request.url.host == "api.github.com":
            if "/git/trees/" not in path:
                return httpx.Response(200, json={"default_branch": "develop"})
            return httpx.Response(200, json={"tree": [
                {"path": "skills/write/SKILL.md", "type": "blob", "mode": "100644"},
                {"path": "skills/read/SKILL.md", "type": "blob", "mode": "100644"},
                {"path": "skills/linked/SKILL.md", "type": "blob", "mode": "120000"},
                {"path": "scripts/install.sh", "type": "blob", "mode": "100755"},
            ]})
        return httpx.Response(200, text="---\nname: " + path.split("/")[-2] + "\ndescription: A skill\n---\nText")

    monkeypatch.setattr("raphael_core.members.capabilities.httpx.AsyncClient",
                        lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(handle)))
    skills = await download_skills("owner/repo")
    assert len(skills) == 2
    assert all("install.sh" not in url and "linked" not in url for url in requested)
    assert all(url.endswith("SKILL.md") for url, text in skills)
