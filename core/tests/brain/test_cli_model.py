import pytest

from .test_service import router, make_brain, fake_client, keys, clock  # noqa: F401
from raphael_core.protocol import RpcError


async def test_only_answer_role_accepts_cli_brain(router):
    result = await router.call("settings.set", {"key": "agent_model", "value": "cli:codex:gpt-6.1-sol"})
    assert result["settings"]["agent_model"] == "cli:codex:gpt-6.1-sol"
    for key, value in [("cleanup_model", "cli:codex"), ("agent_model", "cli:unknown")]:
        with pytest.raises(RpcError):
            await router.call("settings.set", {"key": key, "value": value})
