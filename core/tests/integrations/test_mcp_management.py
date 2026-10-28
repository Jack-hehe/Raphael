import json

import pytest

from raphael_core.integrations.service import IntegrationsService
from raphael_core.integrations.vault import secret_name
from raphael_core.router import Router


@pytest.fixture
def rpc(conn, tmp_path, vault, connector):
    service = IntegrationsService(conn, tmp_path, vault, connector=connector, import_home=tmp_path)
    router = Router()
    service.register(router)
    return router


async def test_preview_imports_disabled_without_exposing_secrets(rpc, tmp_path, vault):
    preview = await rpc.call("integrations.preview", {"text": json.dumps({"mcpServers": {
        "Notes": {"command": "npx", "args": ["a b"], "env": {"TOKEN": "secret-test"}}
    }})})
    assert "secret-test" not in json.dumps(preview)
    assert preview["candidates"][0]["env"] == ["TOKEN"]
    await rpc.call("integrations.import", {"ids": [preview["candidates"][0]["id"]]})
    server = (await rpc.call("toolsets.list"))["groups"][0]
    assert not server["enabled"]
    assert server["args"] == ["a b"]
    assert vault.get(secret_name(server["id"], "env", "TOKEN")) == "secret-test"
    assert "secret-test" not in (tmp_path / "integrations.json").read_text()
    assert (await rpc.call("integrations.preview", {"text": '{"mcpServers":{"Notes":{"command":"x"}}}'}))["candidates"] == []


async def test_edit_preserves_blank_secrets_and_removes_omitted_names(rpc, vault):
    server = (await rpc.call("integrations.add", {"name": "Notes", "transport": "stdio", "command": "npx",
        "env": [{"name": "TOKEN", "value": "saved"}, {"name": "DROP", "value": "gone"}]}))["server"]
    changed = (await rpc.call("integrations.update", {"id": server["id"], "name": "Renamed", "transport": "stdio", "command": "uvx",
        "args": ["space argument"], "env": [{"name": "TOKEN", "value": ""}], "result_size_limit": 2500}))["server"]
    assert changed["name"] == "Renamed"
    assert changed["result_size_limit"] == 2500
    assert not changed["enabled"]
    assert vault.get(secret_name(server["id"], "env", "TOKEN")) == "saved"
    assert vault.get(secret_name(server["id"], "env", "DROP")) is None


async def test_discovery_reads_only_definitions_and_omits_duplicates(rpc, tmp_path):
    (tmp_path / '.codex').mkdir()
    (tmp_path / '.codex/config.toml').write_text('[mcp_servers.notes]\ncommand = "uvx"\nargs = ["notes"]\n', encoding='utf-8')
    (tmp_path / '.cursor').mkdir()
    (tmp_path / '.cursor/mcp.json').write_text('{"mcpServers":{"remote":{"url":"https://example.com/mcp","headers":{"Authorization":"secret-test"}}}}')
    preview = await rpc.call("integrations.discover")
    assert {c["name"] for c in preview["candidates"]} == {"notes", "remote"}
    assert "secret-test" not in json.dumps(preview)


async def test_testing_an_unsaved_server_leaves_no_config_or_vault_entries(rpc, servers, vault):
    servers.add("Notes")
    result = await rpc.call("integrations.test", {"name": "Notes", "transport": "stdio", "command": "npx",
        "env": [{"name": "TOKEN", "value": "secret-test"}]})
    assert result["ok"] is True
    assert result["tools"]
    assert (await rpc.call("toolsets.list"))["groups"] == []
    assert vault.names() == []


async def test_all_six_known_config_paths_are_discovered_without_opening_auth_or_history(rpc, tmp_path, monkeypatch):
    from pathlib import Path
    locations = [".claude.json", ".claude/settings.json", "AppData/Roaming/Claude/claude_desktop_config.json",
                 ".codex/config.toml", ".cursor/mcp.json", ".gemini/config/mcp_config.json"]
    for index, location in enumerate(locations):
        path = tmp_path / location
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'[mcp_servers.server{index}]\nurl="https://example.com/mcp"\nhttp_headers={{Authorization="private-header"}}' if path.suffix == ".toml" else
                        json.dumps({"mcpServers": {f"server{index}": {"command": "npx"}}}), encoding="utf-8")
    opened = []
    original = Path.read_text
    def tracked(path, *args, **kwargs):
        opened.append(path.relative_to(tmp_path).as_posix())
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", tracked)
    preview = await rpc.call("integrations.discover")
    assert len(preview["candidates"]) == 6
    # The service also reads its own list to omit already configured definitions.
    assert set(opened) == set(locations) | {"integrations.json"}
    assert "private-header" not in json.dumps(preview)
    assert preview["candidates"][3]["headers"] == ["Authorization"]


@pytest.mark.parametrize("limit", [0, 99, 100001, True, 100.5, "8000"])
async def test_invalid_result_limits_do_not_create_servers(rpc, vault, limit):
    from raphael_core.protocol import RpcError
    with pytest.raises(RpcError):
        await rpc.call("integrations.add", {"name": "Invalid", "transport": "stdio", "command": "npx", "result_size_limit": limit})
    assert (await rpc.call("toolsets.list"))["groups"] == [] and vault.names() == []


def test_result_size_limit_is_used_for_rendered_tool_output():
    from types import SimpleNamespace
    from raphael_core.integrations.client import render_result
    result = SimpleNamespace(content=[SimpleNamespace(type="text", text="x" * 500)])
    text, truncated = render_result(result, 100)
    assert truncated and text.startswith("x" * 100) and len(text) < 110
