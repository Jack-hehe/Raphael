"""CLI availability and credential-file existence, never credential contents."""

import asyncio
import os
import re
import shutil
import subprocess
from pathlib import Path

from .process import Child, subscription_env
from .models import claude_models, codex_models, antigravity_models

NAMES = {"claude": "Claude Code", "codex": "Codex CLI", "gemini": "Antigravity"}
LOGIN = {"claude": "claude auth login", "codex": "codex login", "gemini": "agy"}
EXECUTABLE = {"claude": "claude", "codex": "codex", "gemini": "agy"}


def brain_ref(value):
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"cli:(claude|codex|gemini)(?::([A-Za-z0-9][A-Za-z0-9._/-]{0,199}))?(?:\?effort=(none|minimal|low|medium|high|xhigh|max))?", value)
    return (match[1], match[2]) if match else None


def brain_effort(value):
    return value.partition("?effort=")[2] or None if brain_ref(value) else None


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
        self._models = {}
        self._model_refresh = None
        self._models_at = {}

    def command(self, name):
        if self.commands is not None:
            return self.commands.get(name)
        executable = EXECUTABLE.get(name, name)
        found = shutil.which(executable, path=self.environ.get("PATH", ""))
        roots = [self.home / ".local" / "bin", self.home / ".bun" / "bin"]
        if self.environ.get("APPDATA"):
            roots.append(Path(self.environ["APPDATA"]) / "npm")
        if self.environ.get("LOCALAPPDATA"):
            local = Path(self.environ["LOCALAPPDATA"])
            roots.extend([local / "Microsoft" / "WinGet" / "Links", local / "Programs" / executable,
                          local / "Programs" / "Claude", local / "npm", local / "agy" / "bin"])
        candidates = [Path(found)] if found else []
        candidates.extend(root / (executable + suffix) for root in roots for suffix in (".exe", ".cmd", ".ps1", ""))
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
        gemini = self.home / ".gemini"
        files = {
            "claude": (claude / ".credentials.json", claude / "credentials.json"),
            "codex": (codex / "auth.json",),
            "gemini": (gemini / "google_accounts.json", gemini / "oauth_creds.json"),
        }
        try:
            if any(path.is_file() for path in files[name]):
                return True
        except OSError:
            return None
        # Claude also uses OS credential storage. Settings alone do not establish Google login.
        return None if name == "claude" else False

    async def _one(self, name):
        command = self.command(name)
        item = {"id": name, "name": NAMES[name], "installed": bool(command),
                "logged_in": self.logged_in(name) if command else False,
                "login_command": LOGIN[name], "models": self._models.get(name, [])}
        if name == "claude":
            item["models"] = claude_models(Path(self.environ.get("CLAUDE_CONFIG_DIR", self.home / ".claude")))
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
        if self._model_refresh is None and any(self._needs_models(name) for name in ("codex", "gemini")):
            self._model_refresh = asyncio.create_task(self._refresh_models())
        return value

    def _needs_models(self, name):
        lifetime = 3600 if self._models.get(name) else 60
        return name not in self._models_at or self._last_used - self._models_at[name] > lifetime

    async def _refresh_one(self, name):
        command = self.command(name)
        if not self._needs_models(name) or not command or not self.logged_in(name):
            return
        try:
            if name == "codex":
                models = await codex_models(command, self.environ,
                    Path(self.environ.get("CODEX_HOME", self.home / ".codex")) / "config.toml")
            else:
                models = await antigravity_models(command, self.environ,
                    self.home / ".gemini" / "antigravity-cli" / "settings.json")
            if models != self._models.get(name):
                self._models[name] = models
                if self.notify:
                    self.notify("brains.changed", {})
        except (OSError, ValueError, TimeoutError):
            pass
        self._models_at[name] = asyncio.get_running_loop().time()

    async def _refresh_models(self):
        try:
            await asyncio.gather(*(self._refresh_one(name) for name in ("codex", "gemini")))
        finally:
            self._model_refresh = None

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
        if self._model_refresh:
            self._model_refresh.cancel()
            await asyncio.gather(self._model_refresh, return_exceptions=True)
        if self._watcher:
            self._watcher.cancel()
            await asyncio.gather(self._watcher, return_exceptions=True)
