"""Read only public subscription windows; never create a CLI conversation."""

import asyncio
import json

from .process import Child


async def read_allowances(brains):
    command = brains.command("codex")
    if not command or brains.logged_in("codex") is not True:
        return {"brains": []}
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
        return {"brains": [{"id": "codex", "name": "Codex CLI", "windows": windows}] if windows else []}
    except (OSError, ValueError, TypeError, AttributeError, TimeoutError):
        return {"brains": []}
    finally:
        await child.kill()
