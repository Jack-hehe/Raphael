"""Member-owned slash templates and Markdown skill documents; never executable packages.

Imports fetch bounded UTF-8 SKILL.md files from GitHub's raw host. No archives, scripts,
dependencies, redirects, or arbitrary URLs are accepted. Documents remain untrusted tool data.
"""

import asyncio
import json
import os
import re
import sqlite3
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

import httpx

from .store import MemberError, MemberStore, _text

MAX_SKILL_BYTES = 128 * 1024
MAX_SKILLS = 40
MAX_COMMANDS = 50
_PART = re.compile(r"[A-Za-z0-9_.-]{1,100}")
_COMMAND = re.compile(r"[a-z][a-z0-9_-]{0,31}")


def skill_urls(source: object) -> tuple[str, list[str]]:
    """Normalize a supported source without permitting arbitrary network destinations."""
    if not isinstance(source, str) or len(source) > 800:
        raise MemberError("source", "a skill needs a GitHub repository or SKILL.md URL")
    source = source.strip()
    parts = source.split("/")
    if "://" in source:
        try:
            url = urlsplit(source)
        except ValueError:
            raise MemberError("source", "invalid skill source URL") from None
        try:
            port = url.port
        except ValueError:
            raise MemberError("source", "invalid skill source port") from None
        if url.scheme != "https" or url.username or url.password or port or url.query or url.fragment:
            raise MemberError("source", "skill URLs must be plain HTTPS URLs")
        parts = [unquote(part) for part in url.path.strip("/").split("/")]
        if url.hostname in ("github.com", "www.github.com"):
            if len(parts) < 5 or parts[2] not in ("blob", "raw") or parts[-1] != "SKILL.md":
                raise MemberError("source", "the GitHub URL must name a SKILL.md file")
            parts = parts[:2] + parts[3:]
            paths = ["/".join(parts[2:])]
        elif url.hostname == "raw.githubusercontent.com":
            if len(parts) < 4 or parts[-1] != "SKILL.md":
                raise MemberError("source", "the URL must name a SKILL.md file")
            paths = ["/".join(parts[2:])]
        elif url.hostname in ("skills.sh", "www.skills.sh") and len(parts) == 3:
            skill = parts[2]
            paths = [f"{branch}/{folder}SKILL.md" for branch in ("main", "master")
                     for folder in (f"skills/{skill}/", f"{skill}/", "")]
        else:
            raise MemberError("source", "only GitHub and skills.sh skill sources are supported")
    else:
        if len(parts) != 2:
            raise MemberError("source", "a repository is owner/repo")
        paths = ["main/SKILL.md", "master/SKILL.md"]
    if not all(_PART.fullmatch(part) and part not in (".", "..") for part in parts):
        raise MemberError("source", "invalid skill source path")
    repo = "/".join(parts[:2])
    return source, [f"https://raw.githubusercontent.com/{repo}/{path}" for path in paths]


async def download_skill(source: object) -> tuple[str, str]:
    source, urls = skill_urls(source)
    try:
        async with asyncio.timeout(25), httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False) as client:
            for url in urls:
                async with client.stream("GET", url, headers={"Accept": "text/plain"}) as response:
                    if response.status_code == 404:
                        continue
                    response.raise_for_status()
                    if response.is_redirect:
                        raise MemberError("source", "skill redirects are not accepted")
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > MAX_SKILL_BYTES:
                            raise MemberError("size", "skill document is too large")
                    text = bytes(data).decode("utf-8-sig")
                    if not text.strip() or "\x00" in text:
                        raise MemberError("skill", "skill must be a UTF-8 Markdown document")
                    return source, text
    except MemberError:
        raise
    except (httpx.HTTPError, UnicodeError, ValueError, TimeoutError):
        raise MemberError("download", "could not download the skill Markdown document") from None
    raise MemberError("not_found", "no SKILL.md found at that source")


async def _bounded_get(client: httpx.AsyncClient, url: str, limit: int) -> bytes:
    async with client.stream("GET", url) as response:
        response.raise_for_status()
        if response.is_redirect:
            raise MemberError("source", "skill redirects are not accepted")
        data = bytearray()
        async for chunk in response.aiter_bytes():
            data.extend(chunk)
            if len(data) > limit:
                raise MemberError("size", "skill source metadata is too large")
        return bytes(data)


async def download_skills(source: object) -> list[tuple[str, str]]:
    """Import repository collections too, fetching only SKILL.md blobs and bounded tree metadata."""
    try:
        return [await download_skill(source)]
    except MemberError as error:
        if error.kind != "not_found":
            raise
    normalized, candidates = skill_urls(source)
    parsed = urlsplit(normalized)
    is_repo = "://" not in normalized
    wanted = parsed.path.strip("/").split("/")[-1] if parsed.hostname in ("skills.sh", "www.skills.sh") else None
    if not is_repo and wanted is None:
        raise MemberError("not_found", "no SKILL.md found at that source")
    repo = "/".join(urlsplit(candidates[0]).path.strip("/").split("/")[:2])
    try:
        async with asyncio.timeout(30), httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False) as client:
            metadata = json.loads(await _bounded_get(client, f"https://api.github.com/repos/{repo}", 128 * 1024))
            branch = metadata.get("default_branch") if isinstance(metadata, dict) else None
            if not isinstance(branch, str) or not all(_PART.fullmatch(part) and part not in (".", "..") for part in branch.split("/")):
                raise MemberError("source", "invalid repository branch metadata")
            tree = json.loads(await _bounded_get(client, f"https://api.github.com/repos/{repo}/git/trees/{quote(branch, safe='')}?recursive=1", 2 * 1024 * 1024))
            if not isinstance(tree, dict) or tree.get("truncated"):
                raise MemberError("size", "repository tree is too large")
            paths = []
            for entry in tree.get("tree", []):
                if not isinstance(entry, dict) or entry.get("type") != "blob" or entry.get("mode") == "120000":
                    continue
                path = entry.get("path", "")
                parts = path.split("/") if isinstance(path, str) else []
                if not parts or parts[-1] != "SKILL.md" or not all(_PART.fullmatch(part) and part not in (".", "..") for part in parts):
                    continue
                if wanted is not None and (len(parts) < 2 or parts[-2] != wanted):
                    continue
                paths.append(path)
            if not paths:
                raise MemberError("not_found", "no SKILL.md found in that repository")
            if len(paths) > MAX_SKILLS:
                raise MemberError("full", "repository has too many skills; import individual SKILL.md URLs")
            documents = []
            for path in sorted(paths):
                url = f"https://raw.githubusercontent.com/{repo}/{branch}/{path}"
                raw = await _bounded_get(client, url, MAX_SKILL_BYTES)
                text = raw.decode("utf-8-sig")
                if not text.strip() or "\x00" in text:
                    raise MemberError("skill", "skill must be a UTF-8 Markdown document")
                documents.append((url, text))
            return documents
    except MemberError:
        raise
    except (httpx.HTTPError, UnicodeError, ValueError, TimeoutError):
        raise MemberError("download", "could not download the repository's skill Markdown") from None


def skill_metadata(text: str) -> tuple[str, str]:
    """Read only plain metadata; YAML tags and object constructors are never interpreted."""
    lines = text.splitlines()
    values: dict[str, str] = {}
    if lines and lines[0].strip() == "---":
        field = None
        for line in lines[1:]:
            if line.strip() == "---":
                break
            if field and line[:1].isspace():
                values[field] += " " + line.strip()
                continue
            field = None
            key, sep, value = line.partition(":")
            if sep and key in ("name", "description"):
                value = value.strip()
                if value in ("|", ">", "|-", ">-"):
                    values[key] = ""
                    field = key
                else:
                    values[key] = value.strip("\"'")
    name = _text(values.get("name", ""), "name", 100, blank=False)
    description = _text(values.get("description", ""), "description", 1000, blank=False)
    return name, description


class MemberCapabilities:
    def __init__(self, conn: sqlite3.Connection, folder: Path, members: MemberStore):
        self.conn = conn
        self.folder = folder
        self.members = members

    def check(self, member_id: int) -> None:
        if type(member_id) is not int or member_id <= 0 or self.members.member(member_id) is None:
            raise MemberError("not_found", "no such member")

    def skills(self, member_id: int) -> list[dict]:
        self.check(member_id)
        rows = self.conn.execute("SELECT id, name, description, enabled, source FROM member_skills WHERE member_id = ? ORDER BY id", (member_id,))
        return [{**dict(row), "enabled": bool(row["enabled"])} for row in rows]

    def _path(self, member_id: int, skill_id: int) -> Path:
        return self.folder / str(member_id) / f"{skill_id}.md"

    def keep_skill(self, member_id: int, source: str, text: str) -> dict:
        self.check(member_id)
        if len(text.encode("utf-8")) > MAX_SKILL_BYTES:
            raise MemberError("size", "skill document is too large")
        name, description = skill_metadata(text)
        if len(self.skills(member_id)) >= MAX_SKILLS:
            raise MemberError("full", "member skill limit reached")
        if any(skill["name"].casefold() == name.casefold() for skill in self.skills(member_id)):
            raise MemberError("taken", "a skill with that name already exists")
        path = None
        try:
            with self.conn:
                cursor = self.conn.execute("INSERT INTO member_skills (member_id, name, description, source) VALUES (?, ?, ?, ?)", (member_id, name, description, source))
                path = self._path(member_id, int(cursor.lastrowid))
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
        except OSError:
            if path is not None:
                path.unlink(missing_ok=True)
            raise MemberError("io", "could not keep the skill document") from None
        return next(skill for skill in self.skills(member_id) if skill["id"] == cursor.lastrowid)

    def update_skill(self, member_id: int, skill_id: int, enabled: object) -> dict:
        self.check(member_id)
        if type(enabled) is not bool:
            raise MemberError("enabled", "enabled must be true or false")
        if not any(skill["id"] == skill_id for skill in self.skills(member_id)):
            raise MemberError("not_found", "no such member skill")
        with self.conn:
            self.conn.execute("UPDATE member_skills SET enabled = ? WHERE member_id = ? AND id = ?", (enabled, member_id, skill_id))
        return next(skill for skill in self.skills(member_id) if skill["id"] == skill_id)

    def _skill(self, member_id: int, skill_id: int) -> dict:
        self.check(member_id)
        skill = next((skill for skill in self.skills(member_id) if skill["id"] == skill_id), None)
        if skill is None:
            raise MemberError("not_found", "no such member skill")
        return skill

    def document(self, member_id: int, skill_id: int) -> tuple[dict, str]:
        """A skill and its whole Markdown document, as the owner's page for it shows them (one that is
        switched off too: only the model's `read_skill` leaves those out)."""
        skill = self._skill(member_id, skill_id)
        path = self._path(member_id, skill_id)
        try:
            if path.stat().st_size > MAX_SKILL_BYTES:
                raise MemberError("size", "skill document is too large")
            return skill, path.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise MemberError("not_found", "the skill document is gone") from None
        except (OSError, UnicodeError):
            raise MemberError("io", "could not read the skill document") from None

    def rewrite_skill(self, member_id: int, skill_id: int, text: object) -> dict:
        """Replace the document of a skill with what the owner edited it to. Its name and description, which
        are what the model is shown of it and say when to use it, are read from the new document, so they
        change with it; the next `read_skill` gives the new text at once. The old document stays when the new
        one cannot be kept."""
        self._skill(member_id, skill_id)
        if not isinstance(text, str):
            raise MemberError("skill", "skill must be a UTF-8 Markdown document")
        text = text.replace("\r\n", "\n")
        if len(text.encode("utf-8")) > MAX_SKILL_BYTES:
            raise MemberError("size", "skill document is too large")
        if not text.strip() or "\x00" in text:
            raise MemberError("skill", "skill must be a UTF-8 Markdown document")
        name, description = skill_metadata(text)
        if any(other["id"] != skill_id and other["name"].casefold() == name.casefold() for other in self.skills(member_id)):
            raise MemberError("taken", "a skill with that name already exists")
        path = self._path(member_id, skill_id)
        temporary = path.with_suffix(".md.tmp")
        try:
            with self.conn:
                self.conn.execute("UPDATE member_skills SET name = ?, description = ? WHERE member_id = ? AND id = ?", (name, description, member_id, skill_id))
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary.write_text(text, encoding="utf-8", newline="\n")
                os.replace(temporary, path)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise MemberError("io", "could not keep the skill document") from None
        return self._skill(member_id, skill_id)

    def delete_skill(self, member_id: int, skill_id: int) -> bool:
        self.check(member_id)
        if not any(skill["id"] == skill_id for skill in self.skills(member_id)):
            return False
        try:
            self._path(member_id, skill_id).unlink(missing_ok=True)
        except OSError:
            raise MemberError("io", "could not delete the skill document") from None
        with self.conn:
            cursor = self.conn.execute("DELETE FROM member_skills WHERE member_id = ? AND id = ?", (member_id, skill_id))
        return bool(cursor.rowcount)

    def read_skill(self, member_id: int, name: object) -> str | None:
        if not isinstance(name, str):
            return None
        for skill in self.skills(member_id):
            if skill["enabled"] and skill["name"] == name:
                path = self._path(member_id, skill["id"])
                if path.stat().st_size > MAX_SKILL_BYTES:
                    return None
                return path.read_text(encoding="utf-8")
        return None

    def commands(self, member_id: int) -> list[dict]:
        self.check(member_id)
        return [dict(row) for row in self.conn.execute("SELECT id, name, description, template FROM member_commands WHERE member_id = ? ORDER BY name", (member_id,))]

    def save_command(self, member_id: int, command_id: int | None, name: object, description: object, template: object) -> dict:
        commands = self.commands(member_id)
        if not isinstance(name, str) or not _COMMAND.fullmatch(name):
            raise MemberError("name", "command name must be 1-32 lowercase letters, digits, underscores or hyphens")
        description = _text(description, "description", 200)
        template = _text(template, "template", 8000, blank=False, one_line=False)
        if "{input}" not in template:
            raise MemberError("template", "command template must include {input}")
        if command_id is not None and not any(command["id"] == command_id for command in commands):
            raise MemberError("not_found", "no such member command")
        if command_id is None and len(commands) >= MAX_COMMANDS:
            raise MemberError("full", "member command limit reached")
        try:
            with self.conn:
                if command_id is None:
                    cursor = self.conn.execute("INSERT INTO member_commands (member_id, name, description, template) VALUES (?, ?, ?, ?)", (member_id, name, description, template))
                    command_id = int(cursor.lastrowid)
                else:
                    self.conn.execute("UPDATE member_commands SET name = ?, description = ?, template = ? WHERE member_id = ? AND id = ?", (name, description, template, member_id, command_id))
        except sqlite3.IntegrityError:
            raise MemberError("taken", "a command with that name already exists") from None
        return next(command for command in self.commands(member_id) if command["id"] == command_id)

    def delete_command(self, member_id: int, command_id: int) -> bool:
        self.check(member_id)
        with self.conn:
            cursor = self.conn.execute("DELETE FROM member_commands WHERE member_id = ? AND id = ?", (member_id, command_id))
        return bool(cursor.rowcount)

    def expand(self, member_id: int, text: str) -> str:
        parts = text.split(maxsplit=1)
        name = parts[0] if parts else ""
        rest = parts[1] if len(parts) > 1 else ""
        if not text.startswith("/") or member_id == 0:
            return text
        for command in self.commands(member_id):
            if name == "/" + command["name"]:
                return command["template"].replace("{input}", rest)
        return text
