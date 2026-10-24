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
    brains = SimpleNamespace(command=lambda name: ["codex.exe"], logged_in=lambda name: True, environ={})
    assert await allowances.read_allowances(brains) == {"brains": [{"id": "codex", "name": "Codex CLI", "windows": [
        {"period": "fiveHour", "remaining_percent": 70, "resets_at": 123},
        {"period": "weekly", "remaining_percent": 30, "resets_at": 456},
    ]}]}
    assert child.send.await_args_list[2].args[0]["method"] == "account/rateLimits/read"
    child.kill.assert_awaited_once()


@pytest.mark.asyncio
async def test_unavailable_or_signed_out_cli_is_omitted():
    brains = SimpleNamespace(command=lambda name: None, logged_in=lambda name: False, environ={})
    assert await allowances.read_allowances(brains) == {"brains": []}
