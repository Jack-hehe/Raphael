"""Model discovery uses public metadata, never inference or credential contents."""

import json
import sys
import asyncio

from raphael_core.agent.cli.models import claude_models, agy_models, codex_models
from raphael_core.agent.cli.discovery import brain_ref, brain_effort


def test_claude_config_resolves_alias_default(tmp_path):
    folder = tmp_path / ".claude"
    folder.mkdir()
    (folder / "settings.json").write_text(json.dumps({"model": "opus"}))
    models = claude_models(folder)
    assert len(models) == 3
    assert next(m for m in models if m["is_default"])["id"] == "cli:claude:opus"
    assert all(m["name"].startswith("Claude ") and m["description"] for m in models)
    assert models[0]["reasoning_efforts"]


def test_agy_collapses_efforts_and_resolves_display_default(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"model": "Gemini Argon High"}))
    models = agy_models("gemini-4-argon-low\tGemini Argon Low\ngemini-4-argon-high\tGemini Argon High\n", settings)
    assert len(models) == 1
    assert models[0]["reasoning_efforts"] == ["low", "high"]
    assert models[0]["is_default"] and models[0]["default_effort"] == "high"
    assert models[0]["id"] == "cli:gemini:gemini-4-argon"
    assert models[0]["effort_models"]["low"] == "cli:gemini:gemini-4-argon-low"


async def test_antigravity_uses_public_model_command_and_google_account_file(tmp_path):
    from raphael_core.agent.cli.discovery import CliBrains

    script = tmp_path / "agy.py"
    script.write_text('''import sys
if sys.argv[1:] == ["--version"]:
    print("agy 1.0")
else:
    assert sys.argv[1:] == ["models"]
    print("gemini-4-argon-low\\tGemini Argon Low")
    print("claude-opus-5-5-high\\tClaude Opus 5.5 High")
''', encoding="utf-8")
    folder = tmp_path / ".gemini"
    (folder / "antigravity-cli").mkdir(parents=True)
    (folder / "google_accounts.json").write_text("not credential JSON")
    (folder / "antigravity-cli" / "settings.json").write_text(json.dumps({"model": "Gemini Argon Low"}))
    brains = CliBrains(home=tmp_path, environ={}, commands={"gemini": [sys.executable, str(script)]})
    try:
        source = (await brains.list())["clis"][2]
        assert source["name"] == "Antigravity" and source["logged_in"] is True
        assert source["login_command"] == "agy"
        await asyncio.wait_for(brains._model_refresh, 3)
        source = (await brains.list())["clis"][2]
        assert [m["provider"] for m in source["models"]] == ["Google", "Anthropic"]
        assert source["models"][0]["is_default"]
    finally:
        await brains.aclose()


def test_antigravity_windows_executable_keeps_gemini_id(tmp_path):
    from raphael_core.agent.cli.discovery import CliBrains

    executable = tmp_path / "agy" / "bin" / "agy.exe"
    executable.parent.mkdir(parents=True)
    executable.touch()
    brains = CliBrains(home=tmp_path, environ={"PATH": "", "LOCALAPPDATA": str(tmp_path)})
    assert brains.command("gemini") == [str(executable)]


async def test_codex_public_model_list_protocol_pagination_hidden_and_config_default(tmp_path):
    script = tmp_path / "models.py"
    script.write_text('''import json, sys
def send(id, result):
    print(json.dumps({"jsonrpc":"2.0","id":id,"result":result}), flush=True)
initialized = False
for line in sys.stdin:
    p=json.loads(line)
    if p.get("method")=="initialize":
        send(p["id"], {})
    elif p.get("method")=="initialized":
        initialized=True
    elif p.get("method")=="model/list":
        assert initialized
        if p["params"].get("cursor"):
            send(p["id"], {"data":[{"id":"small","model":"small","displayName":"Small","isDefault":False,"supportedReasoningEfforts":[{"reasoningEffort":"low"},{"reasoningEffort":"high"}],"defaultReasoningEffort":"high"}],"nextCursor":None})
        else:
            send(p["id"], {"data":[{"id":"hidden","hidden":True},{"id":"large","model":"large","displayName":"Large","isDefault":True}],"nextCursor":"page2"})
''', encoding="utf-8")
    config = tmp_path / "config.toml"
    config.write_text('model = "small"\nmodel_reasoning_effort = "low"\n')
    models = await codex_models([sys.executable, str(script)], {}, config)
    assert [m["id"] for m in models] == ["cli:codex:large", "cli:codex:small"]
    assert [m["is_default"] for m in models] == [False, True]
    assert models[1]["reasoning_efforts"] == ["low", "high"]
    assert models[1]["default_effort"] == "low"


def test_effort_ref_keeps_model_separate_from_transport_setting():
    assert brain_ref("cli:codex:small?effort=high") == ("codex", "small")
    assert brain_effort("cli:codex:small?effort=high") == "high"
    assert brain_ref("cli:codex:small?effort=arbitrary") is None


async def test_discovery_returns_before_model_refresh_and_new_login_populates_cache(tmp_path):
    from raphael_core.agent.cli.discovery import CliBrains

    script = tmp_path / "cached.py"
    script.write_text('''import json, sys, time
if "--version" in sys.argv:
    print("fake 1.0")
    sys.exit(0)
for line in sys.stdin:
    p=json.loads(line)
    if p.get("method")=="initialize":
        print(json.dumps({"id":p["id"],"result":{}}),flush=True)
    elif p.get("method")=="model/list":
        time.sleep(.15)
        print(json.dumps({"id":p["id"],"result":{"data":[{"id":"small","displayName":"Small","isDefault":True}]}}),flush=True)
    else:
        assert p.get("method")=="initialized"
''', encoding="utf-8")
    refreshed = asyncio.Event()
    brains = CliBrains(home=tmp_path, environ={}, commands={"codex": [sys.executable, str(script)]},
                       notify=lambda *_: refreshed.set())
    try:
        result = await brains.list()
        assert result["clis"][1]["logged_in"] is False
        await asyncio.sleep(.02)
        (tmp_path / ".codex").mkdir()
        (tmp_path / ".codex" / "auth.json").touch()
        result = await brains.list()
        assert result["clis"][1]["models"] == []
        refreshed.clear()
        await asyncio.wait_for(refreshed.wait(), 3)
        result = await brains.list()
        assert result["clis"][1]["models"][0]["id"] == "cli:codex:small"
        assert result["clis"][1]["models"][0]["is_default"]
    finally:
        await brains.aclose()
