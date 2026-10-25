"""Read only public subscription windows; never create a CLI conversation."""

import asyncio
import json

from .discovery import cli_available
from .process import Child


async def read_allowances(brains):
    rows = []
    for name, label in (("codex", "Codex"), ("claude", "Claude"), ("gemini", "Antigravity")):
        command = brains.command(name)
        disconnected = brains.disconnected(name) if hasattr(brains, "disconnected") else False
        signed_in = brains.logged_in(name) if command and not disconnected else False
        if name == "claude" and command and not disconnected and signed_in is None:
            signed_in = await _claude_signed_in(command, brains.environ)
        if cli_available(command, signed_in, disconnected):
            rows.append({"id": name, "name": label, "windows": []})
    if any(row["id"] == "codex" for row in rows):
        rows[0]["windows"] = await _codex_windows(brains)
    return {"brains": rows}


async def _claude_signed_in(command, environ):
    """Let the CLI check its OS credential store; retain only the login boolean."""
    child = Child([*command, "auth", "status", "--json"], environ=environ)
    try:
        async with asyncio.timeout(3):
            await child.start()
            raw = await child.process.stdout.read(64001)
            await child.process.wait()
        return child.process.returncode == 0 and len(raw) <= 64000 and json.loads(raw).get("loggedIn") is True
    except (OSError, ValueError, TypeError, AttributeError, TimeoutError):
        return False
    finally:
        await child.kill()


async def _codex_windows(brains):
    command = brains.command("codex")
    child = Child([*command, "app-server"], environ=brains.environ)

    async def request(number, method, params):
        await child.send({"jsonrpc": "2.0", "id": number, "method": method, "params": params})
        for _ in range(100):
            line = await child.process.stdout.readline()
            if not line or len(line) > 1_000_000:
                raise ValueError("allowance_unavailable")
            packet = json.loads(line)
            if packet.get("id") == number:
                if "error" in packet:
                    raise ValueError("allowance_unavailable")
                return packet.get("result") or {}
        raise ValueError("allowance_unavailable")

    try:
        async with asyncio.timeout(8):
            await child.start()
            await request(1, "initialize", {"clientInfo": {"name": "raphael", "version": "0.2.0"}, "capabilities": {}})
            await child.send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
            result = await request(2, "account/rateLimits/read", {})
        snapshot = (result.get("rateLimitsByLimitId") or {}).get("codex") or result.get("rateLimits") or {}
        windows = []
        for key in ("primary", "secondary"):
            window = snapshot.get(key) or {}
            period = {300: "fiveHour", 10080: "weekly"}.get(window.get("windowDurationMins"))
            used, reset = window.get("usedPercent"), window.get("resetsAt")
            if period and type(used) is int and 0 <= used <= 100:
                windows.append({"period": period, "remaining_percent": 100 - used,
                                "resets_at": reset if type(reset) is int and reset > 0 else None})
        return windows
    except (OSError, ValueError, TypeError, AttributeError, TimeoutError):
        return []
    finally:
        await child.kill()
