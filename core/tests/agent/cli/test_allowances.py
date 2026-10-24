import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from raphael_core.agent.cli import allowances


@pytest.mark.asyncio
async def test_only_public_subscription_windows_leave_the_child(monkeypatch):
    stream = SimpleNamespace(readline=AsyncMock(side_effect=[
        b'{"id":1,"result":{}}\n',
        json.dumps({"id": 2, "result": {"accountId": "private", "rateLimits": {
            "primary": {"usedPercent": 30, "windowDurationMins": 300, "resetsAt": 123},
            "secondary": {"usedPercent": 70, "windowDurationMins": 10080, "resetsAt": 456},
        }}}).encode(),
    ]))
    child = SimpleNamespace(start=AsyncMock(), send=AsyncMock(), kill=AsyncMock(),
                            process=SimpleNamespace(stdout=stream))
    monkeypatch.setattr(allowances, "Child", lambda *a, **k: child)
    brains = SimpleNamespace(command=lambda name: ["codex.exe"] if name == "codex" else None, logged_in=lambda name: True, environ={})
    assert await allowances.read_allowances(brains) == {"brains": [{"id": "codex", "name": "Codex", "windows": [
        {"period": "fiveHour", "remaining_percent": 70, "resets_at": 123},
        {"period": "weekly", "remaining_percent": 30, "resets_at": 456},
    ]}]}
    assert child.send.await_args_list[2].args[0]["method"] == "account/rateLimits/read"
    child.kill.assert_awaited_once()


@pytest.mark.asyncio
async def test_unavailable_or_signed_out_cli_is_omitted():
    brains = SimpleNamespace(command=lambda name: None, logged_in=lambda name: False, environ={})
    assert await allowances.read_allowances(brains) == {"brains": []}


@pytest.mark.asyncio
async def test_signed_in_clis_remain_visible_when_live_query_fails(monkeypatch):
    child = SimpleNamespace(start=AsyncMock(side_effect=OSError()), kill=AsyncMock())
    monkeypatch.setattr(allowances, "Child", lambda *a, **k: child)
    brains = SimpleNamespace(command=lambda name: [name], logged_in=lambda name: True, environ={})
    result = await allowances.read_allowances(brains)
    assert [(row["id"], row["name"], row["windows"]) for row in result["brains"]] == [
        ("codex", "Codex", []), ("claude", "Claude", []), ("gemini", "Antigravity", []),
    ]


@pytest.mark.asyncio
async def test_claude_os_store_login_can_be_confirmed_by_its_public_status_command(monkeypatch):
    child = SimpleNamespace(start=AsyncMock(), kill=AsyncMock(), process=SimpleNamespace(
        wait=AsyncMock(), returncode=0,
        stdout=SimpleNamespace(read=AsyncMock(return_value=b'{"loggedIn":true,"email":"PRIVATE"}\n'))))
    commands = []
    monkeypatch.setattr(allowances, "Child", lambda args, **kw: commands.append(args) or child)
    brains = SimpleNamespace(command=lambda name: ["claude.exe"] if name == "claude" else None,
                             logged_in=lambda name: None, environ={})
    assert await allowances.read_allowances(brains) == {"brains": [{"id": "claude", "name": "Claude", "windows": []}]}
    assert commands == [["claude.exe", "auth", "status", "--json"]]
