"""CLI availability and credential-file existence, never credential contents."""

import asyncio
import os
import re
import shutil
import subprocess
from pathlib import Path

from .process import Child, subscription_env

NAMES = {"claude": "Claude Code", "codex": "Codex CLI", "gemini": "Gemini CLI"}
MODELS = {"claude": ("sonnet", "opus", "haiku"), "codex": (), "gemini": ()}
LOGIN = {"claude": "claude auth login", "codex": "codex login", "gemini": "gemini"}


def brain_ref(value):
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"cli:(claude|codex|gemini)(?::([A-Za-z0-9][A-Za-z0-9._/-]{0,199}))?", value)
    return (match[1], match[2]) if match else None


def resolve_command(path, environ=None):
    path = Path(path).resolve()
    environ = os.environ if environ is None else environ
    if path.suffix.lower() not in (".cmd", ".ps1", ".bat"):
        return [str(path)]
    # Only recognize npm's quoted JavaScript entry point; never execute shim text.
    text = path.read_text(encoding="utf-8-sig")
    match = re.search(r'["\'](?:%~dp0|%dp0%|\$basedir)[/\\]([^"\'\r\n]+\.(?:js|cjs|mjs))["\']', text, re.I)
    if not match:
        raise ValueError("unsupported_shim")
    entry = (path.parent / match[1].replace("\\", "/")).resolve()
    if not entry.is_file() or not entry.is_relative_to(path.parent):
        raise ValueError("unsupported_shim")
    node = path.parent / "node.exe"
    found = str(node) if node.is_file() else shutil.which("node", path=environ.get("PATH", ""))
    if not found:
        raise ValueError("node_missing")
    return [found, str(entry)]


class CliBrains:
    def __init__(self, *, home=None, environ=None, commands=None, notify=None):
        self.home = Path.home() if home is None else Path(home)
        self.environ = dict(os.environ if environ is None else environ)
        self.commands = commands
        self.notify = notify
        self._previous = None
        self._lock = asyncio.Lock()
        self._watcher = None
        self._last_used = 0.0

    def command(self, name):
        if self.commands is not None:
            return self.commands.get(name)
        found = shutil.which(name, path=self.environ.get("PATH", ""))
        roots = [self.home / ".local" / "bin", self.home / ".bun" / "bin"]
        if self.environ.get("APPDATA"):
            roots.append(Path(self.environ["APPDATA"]) / "npm")
        if self.environ.get("LOCALAPPDATA"):
            local = Path(self.environ["LOCALAPPDATA"])
            roots.extend([local / "Microsoft" / "WinGet" / "Links", local / "Programs" / name,
                          local / "Programs" / "Claude", local / "npm"])
        candidates = [Path(found)] if found else []
        candidates.extend(root / (name + suffix) for root in roots for suffix in (".exe", ".cmd", ".ps1", ""))
        for path in candidates:
            if path.is_file():
                try:
                    return resolve_command(path, self.environ)
                except (ValueError, OSError):
                    continue
        return None

    def logged_in(self, name):
        claude = Path(self.environ.get("CLAUDE_CONFIG_DIR", self.home / ".claude"))
        codex = Path(self.environ.get("CODEX_HOME", self.home / ".codex"))
        gemini = Path(self.environ.get("GEMINI_CLI_HOME", self.home)) / ".gemini"
        files = {
            "claude": (claude / ".credentials.json", claude / "credentials.json"),
            "codex": (codex / "auth.json",),
            "gemini": (gemini / "oauth_creds.json",),
        }
        try:
            if any(path.is_file() for path in files[name]):
                return True
        except OSError:
            return None
        # Claude also uses OS credential storage. Settings alone do not establish Gemini login.
        return None if name == "claude" else False

    async def _one(self, name):
        command = self.command(name)
        item = {"id": name, "name": NAMES[name], "installed": bool(command),
                "logged_in": self.logged_in(name) if command else False,
                "login_command": LOGIN[name], "models": [
                    {"id": f"cli:{name}", "name": "CLI default"},
                    *[{"id": f"cli:{name}:{model}", "name": model} for model in MODELS[name]],
                ]}
        if command:
            child = Child([*command, "--version"], environ=self.environ)
            try:
                async with asyncio.timeout(2):
                    await child.start()
                    line = await child.process.stdout.readline()
                    await child.process.wait()
                if child.process.returncode == 0:
                    item["version"] = " ".join(line.decode("utf-8", errors="replace").split())[:120]
            except (OSError, ValueError, TimeoutError):
                item["notes"] = "Version check unavailable"
            finally:
                await child.kill()
        return item

    async def list(self, _params=None):
        self._last_used = asyncio.get_running_loop().time()
        async with self._lock:
            value = {"clis": list(await asyncio.gather(*(self._one(name) for name in NAMES)))}
            if self._previous is not None and self._previous != value and self.notify:
                self.notify("brains.changed", {})
            self._previous = value
        if self._watcher is None:
            self._watcher = asyncio.create_task(self._watch())
        return value

    def login(self, name):
        command = self.command(name)
        if not command or os.name != "nt":
            raise ValueError("cli_missing")
        arguments = {"claude": ["auth", "login"], "codex": ["login"], "gemini": []}[name]
        subprocess.Popen([*command, *arguments], env=subscription_env(self.environ),
                         creationflags=subprocess.CREATE_NEW_CONSOLE, close_fds=True)
        if self.notify:
            self.notify("brains.changed", {})
        return {"started": True}

    async def _watch(self):
        try:
            while asyncio.get_running_loop().time() - self._last_used < 180:
                await asyncio.sleep(60)
                previous = self._last_used
                await self.list()
                self._last_used = previous
        finally:
            self._watcher = None

    async def aclose(self):
        if self._watcher:
            self._watcher.cancel()
            await asyncio.gather(self._watcher, return_exceptions=True)
